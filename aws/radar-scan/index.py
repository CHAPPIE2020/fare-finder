"""Viral Radar (小眾爆發雷達) scanner — YouTube Data API v3.

Two EventBridge schedules call this one function:
  {"mode": "discover"}  every 6h  -> search.list per paid keyword, store fresh candidate videos
  {"mode": "track"}     every 1h  -> videos.list for candidates, score them, enqueue alerts

Quota (default project): search.list has its own bucket of 100 calls/day, videos.list and
channels.list cost 1 unit per call of up to 50 ids from the 10,000-unit bucket.
discover: MAX_KEYWORDS_PER_RUN (default 20) x 4 runs/day = 80 searches/day.

Paywall: only subscribers whose RADAR row is active, or cancelled but still inside the paid
period, are scanned/alerted (same rule as flight-parser). Lapsed grace rows become expired.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr

UA = "Mozilla/5.0 (compatible; flight-notifier-radar/1.0)"
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
ROUTE = "RADAR"
ALERT_QUEUE_URL = os.environ["ALERT_QUEUE_URL"]
REGION_CODE = os.environ.get("REGION_CODE", "TW")
LANGUAGE = os.environ.get("RELEVANCE_LANGUAGE", "zh-Hant")
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "48"))
MAX_KEYWORDS_PER_RUN = int(os.environ.get("MAX_KEYWORDS_PER_RUN", "20"))
MIN_VIEWS = int(os.environ.get("MIN_VIEWS", "3000"))
MAX_VIDEOS_PER_ALERT = int(os.environ.get("MAX_VIDEOS_PER_ALERT", "5"))
TTL_DAYS = 3

_sm = boto3.client("secretsmanager")
_sqs = boto3.client("sqs")
_ddb = boto3.resource("dynamodb")
_subs = _ddb.Table("subscriptions")
_videos = _ddb.Table("radar_videos")
_api_key = None


def _key():
    global _api_key
    if _api_key is None:
        _api_key = json.loads(_sm.get_secret_value(SecretId="flight/youtube")["SecretString"])["api_key"]
    return _api_key


def _yt(path, params):
    q = urllib.parse.urlencode({**params, "key": _key()})
    req = urllib.request.Request("https://www.googleapis.com/youtube/v3/%s?%s" % (path, q),
                                 headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8", "replace")[:300]
        print("YouTube API %s %s: %s" % (path, err.code, body))
        if err.code == 403 and "quota" in body.lower():
            raise QuotaExceeded(body)
        return None
    except urllib.error.URLError as err:
        print("YouTube API %s network error: %s" % (path, err))
        return None


class QuotaExceeded(Exception):
    pass


def _now():
    return datetime.now(timezone.utc)


def _paid_radar_rows():
    """Paid RADAR subscribers; lazily retire cancelled rows whose paid period has ended."""
    now_str = _now().strftime(TS_FMT)
    rows, kwargs = [], {"FilterExpression": Attr("route").eq(ROUTE)}
    while True:
        resp = _subs.scan(**kwargs)
        rows.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    paid = []
    for it in rows:
        status = it.get("subscription_status")
        if status == "active":
            paid.append(it)
        elif status == "cancelled":
            if (it.get("current_period_end") or "") >= now_str:
                paid.append(it)
            else:
                _subs.update_item(
                    Key={"email": it["email"], "route": ROUTE},
                    UpdateExpression="SET subscription_status = :x, expired_at = :n, updated_at = :n",
                    ConditionExpression="subscription_status = :c",
                    ExpressionAttributeValues={":x": "expired", ":c": "cancelled", ":n": now_str},
                )
                print("grace over: %s#RADAR cancelled -> expired" % it["email"])
    return paid


def _update(table, key, set_values, add_values=None, set_if_missing=None):
    """UpdateItem with every attribute name aliased (avoids DynamoDB reserved words)."""
    names, values, parts = {}, {}, []
    for i, (k, v) in enumerate(set_values.items()):
        names["#s%d" % i], values[":s%d" % i] = k, v
        parts.append("#s%d = :s%d" % (i, i))
    for i, (k, v) in enumerate((set_if_missing or {}).items()):
        names["#m%d" % i], values[":m%d" % i] = k, v
        parts.append("#m%d = if_not_exists(#m%d, :m%d)" % (i, i, i))
    expr = "SET " + ", ".join(parts)
    if add_values:
        adds = []
        for i, (k, v) in enumerate(add_values.items()):
            names["#a%d" % i], values[":a%d" % i] = k, v
            adds.append("#a%d :a%d" % (i, i))
        expr += " ADD " + ", ".join(adds)
    table.update_item(Key=key, UpdateExpression=expr, ExpressionAttributeNames=names,
                      ExpressionAttributeValues=values)


def _norm(keyword):
    return " ".join(str(keyword).split()).lower()


# ---------------------------------------------------------------- discover
def discover(run_index):
    subs = _paid_radar_rows()
    keywords = sorted({_norm(k) for s in subs for k in (s.get("keywords") or []) if str(k).strip()})
    if not keywords:
        print("discover: no paid radar keywords")
        return {"searched": 0, "stored": 0}

    # rotate through keywords when there are more than one run can afford
    if len(keywords) > MAX_KEYWORDS_PER_RUN:
        start = (run_index * MAX_KEYWORDS_PER_RUN) % len(keywords)
        keywords = (keywords + keywords)[start:start + MAX_KEYWORDS_PER_RUN]

    since = (_now() - timedelta(hours=LOOKBACK_HOURS)).strftime(TS_FMT)
    found = {}  # video_id -> {snippet..., keywords:set}
    searched = 0
    for kw in keywords:
        try:
            data = _yt("search", {"part": "snippet", "q": kw, "type": "video", "order": "viewCount",
                                  "publishedAfter": since, "regionCode": REGION_CODE,
                                  "relevanceLanguage": LANGUAGE, "maxResults": 25})
        except QuotaExceeded:
            print("discover: search quota exhausted, stopping this run")
            break
        searched += 1
        for item in (data or {}).get("items", []):
            vid = (item.get("id") or {}).get("videoId")
            sn = item.get("snippet") or {}
            if not vid:
                continue
            entry = found.setdefault(vid, {"snippet": sn, "keywords": set()})
            entry["keywords"].add(kw)
        print("discover: '%s' -> %d result(s)" % (kw, len((data or {}).get("items", []))))

    if not found:
        return {"searched": searched, "stored": 0}

    channel_ids = list({v["snippet"].get("channelId") for v in found.values() if v["snippet"].get("channelId")})
    baselines = _channel_baselines(channel_ids)
    expires = int((_now() + timedelta(days=TTL_DAYS)).timestamp())
    now_str = _now().strftime(TS_FMT)
    for vid, v in found.items():
        sn = v["snippet"]
        base = baselines.get(sn.get("channelId"), {})
        _update(
            _videos, {"video_id": vid},
            {
                "title": sn.get("title", ""), "channel_id": sn.get("channelId", ""),
                "channel_title": sn.get("channelTitle", ""), "published_at": sn.get("publishedAt", ""),
                "description": (sn.get("description") or "")[:500],
                "channel_avg_views": Decimal(str(base.get("avg_views", 0))),
                "channel_subscribers": Decimal(str(base.get("subscribers", 0))),
                "expires_at": expires, "last_discovered_at": now_str,
            },
            add_values={"keywords": set(v["keywords"])},
            set_if_missing={"first_seen_at": now_str},
        )
    print("discover: stored/updated %d candidate video(s) from %d search(es)" % (len(found), searched))
    return {"searched": searched, "stored": len(found)}


def _channel_baselines(channel_ids):
    """Lifetime average views per video for each channel (a crude 'normal' for that channel)."""
    out = {}
    for i in range(0, len(channel_ids), 50):
        data = _yt("channels", {"part": "statistics", "id": ",".join(channel_ids[i:i + 50])})
        for ch in (data or {}).get("items", []):
            st = ch.get("statistics") or {}
            views = int(st.get("viewCount", 0) or 0)
            count = int(st.get("videoCount", 0) or 0)
            subs = 0 if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0) or 0)
            out[ch["id"]] = {"avg_views": round(views / count) if count else 0, "subscribers": subs}
    return out


# ---------------------------------------------------------------- track
def track():
    subs = _paid_radar_rows()
    if not subs:
        print("track: no paid radar subscribers")
        return {"tracked": 0, "alerts": 0}

    candidates, kwargs = [], {}
    while True:
        resp = _videos.scan(**kwargs)
        candidates.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    now = _now()
    cutoff = (now - timedelta(hours=LOOKBACK_HOURS)).strftime(TS_FMT)
    candidates = [c for c in candidates if (c.get("published_at") or "") >= cutoff]
    if not candidates:
        print("track: no fresh candidates")
        return {"tracked": 0, "alerts": 0}

    stats = {}
    ids = [c["video_id"] for c in candidates]
    for i in range(0, len(ids), 50):
        data = _yt("videos", {"part": "statistics,contentDetails", "id": ",".join(ids[i:i + 50])})
        for v in (data or {}).get("items", []):
            st = v.get("statistics") or {}
            stats[v["id"]] = {
                "views": int(st.get("viewCount", 0) or 0),
                "likes": int(st.get("likeCount", 0) or 0),
                "comments": int(st.get("commentCount", 0) or 0),
                "duration": (v.get("contentDetails") or {}).get("duration", ""),
            }

    scored = []
    for c in candidates:
        st = stats.get(c["video_id"])
        if not st:
            continue
        published = datetime.strptime(c["published_at"][:19] + "Z", TS_FMT).replace(tzinfo=timezone.utc)
        age_h = max((now - published).total_seconds() / 3600, 0.5)
        avg = float(c.get("channel_avg_views") or 0)
        ratio = st["views"] / avg if avg > 0 else 0.0
        velocity = st["views"] / age_h
        _update(
            _videos, {"video_id": c["video_id"]},
            {
                "views": st["views"], "likes": st["likes"], "comments": st["comments"],
                "duration": st["duration"], "ratio": Decimal(str(round(ratio, 2))),
                "views_per_hour": Decimal(str(round(velocity))), "age_hours": Decimal(str(round(age_h, 1))),
                "last_checked_at": now.strftime(TS_FMT),
            },
        )
        scored.append({
            "video_id": c["video_id"], "title": c.get("title", ""), "channel_title": c.get("channel_title", ""),
            "published_at": c.get("published_at", ""), "views": st["views"], "ratio": round(ratio, 1),
            "views_per_hour": round(velocity), "age_hours": round(age_h, 1), "duration": st["duration"],
            "keywords": sorted(c.get("keywords") or []),
        })

    alerts = 0
    for s in subs:
        wanted = {_norm(k) for k in (s.get("keywords") or [])}
        min_ratio = float(s.get("min_ratio") or 3)
        hits = [v for v in scored
                if wanted.intersection(v["keywords"]) and v["views"] >= MIN_VIEWS and v["ratio"] >= min_ratio]
        if not hits:
            continue
        hits.sort(key=lambda v: (v["ratio"], v["views_per_hour"]), reverse=True)
        # the consumer drops videos this subscriber was already told about, then caps the email
        _sqs.send_message(QueueUrl=ALERT_QUEUE_URL, MessageBody=json.dumps({
            "email": s["email"], "keywords": sorted(wanted), "min_ratio": min_ratio,
            "videos": hits[:MAX_VIDEOS_PER_ALERT * 3],
        }, ensure_ascii=False))
        alerts += 1

    print("track: %d candidate(s) scored, %d paid subscriber(s), %d alert message(s) enqueued"
          % (len(scored), len(subs), alerts))
    return {"tracked": len(scored), "alerts": alerts}


def handler(event, context):
    mode = (event or {}).get("mode", "track")
    if mode == "discover":
        run_index = int(_now().timestamp() // (6 * 3600))
        return discover(run_index)
    return track()
