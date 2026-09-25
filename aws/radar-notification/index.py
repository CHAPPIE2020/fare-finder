"""Viral Radar alert email (consumer of radar-alert-queue).

Per message {email, keywords, min_ratio, videos:[...]}:
  1. drop videos this subscriber was already told about (notification_history pk=email#radar#video_id)
  2. keep the top MAX_VIDEOS_PER_EMAIL
  3. add a short AI breakdown per video (Claude, cached on radar_videos.analysis) — optional:
     skipped silently if the flight/anthropic secret doesn't exist yet
  4. send ONE digest email via Resend, then write history rows (only after a 2xx)
Failure classes (resend-best-practice Rule 3a): 429/5xx/network -> retry that message;
403/422 -> permanent, log and drop.
"""
import html
import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

import boto3

UA = "Mozilla/5.0 (compatible; flight-notifier-radar/1.0)"
SITE_URL = os.environ.get("SITE_URL", "https://fare-finder-two.vercel.app").strip().rstrip("/")
MAX_VIDEOS_PER_EMAIL = int(os.environ.get("MAX_VIDEOS_PER_EMAIL", "5"))
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001").strip()
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

_sm = boto3.client("secretsmanager")
_ddb = boto3.resource("dynamodb")
_history = _ddb.Table("notification_history")
_videos = _ddb.Table("radar_videos")
_subs = _ddb.Table("subscriptions")
_resend = None
_anthropic = "unset"


class Transient(Exception):
    pass


def _secret(name):
    return json.loads(_sm.get_secret_value(SecretId=name)["SecretString"])


def _resend_cfg():
    global _resend
    if _resend is None:
        _resend = _secret("flight/resend")
    return _resend


def _anthropic_key():
    global _anthropic
    if _anthropic == "unset":
        try:
            _anthropic = _secret("flight/anthropic")["api_key"]
        except Exception as err:  # secret not created yet -> run without AI breakdowns
            print("no flight/anthropic secret (%s) - sending alerts without AI analysis" % type(err).__name__)
            _anthropic = None
    return _anthropic


def _already_sent(email, video_id):
    resp = _history.query(
        KeyConditionExpression="pk = :p",
        ExpressionAttributeValues={":p": "%s#radar#%s" % (email, video_id)},
        Limit=1,
    )
    return bool(resp.get("Items"))


# ---------------------------------------------------------------- AI breakdown
def _analysis(video, keywords):
    item = _videos.get_item(Key={"video_id": video["video_id"]}).get("Item") or {}
    if item.get("analysis"):
        return json.loads(item["analysis"])
    key = _anthropic_key()
    if not key:
        return None
    prompt = (
        "你是短影音與 YouTube 內容策略顧問。以下這支影片發布後表現遠超過該頻道平常水準。\n"
        "請只根據提供的資訊分析，不要編造影片內容細節；資訊不足就說「可能」。\n\n"
        "訂閱者關注的領域：%s\n標題：%s\n頻道：%s\n影片長度（ISO 8601）：%s\n"
        "發布 %.1f 小時，觀看 %d（約為該頻道平均的 %.1f 倍）\n說明欄前段：%s\n\n"
        "請用繁體中文，只輸出 JSON，格式："
        '{"why": ["爆紅原因1", "爆紅原因2", "爆紅原因3"], '
        '"ideas": ["可以改編的題目1", "可以改編的題目2", "可以改編的題目3"]}'
        "。每點 40 字以內，題目要適合訂閱者的領域。"
    ) % ("、".join(keywords), video["title"], video["channel_title"], video.get("duration", ""),
         video.get("age_hours", 0), video["views"], video["ratio"], (item.get("description") or "")[:300])
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": ANTHROPIC_MODEL, "max_tokens": 600,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = json.loads(resp.read())
        text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
        match = re.search(r"\{.*\}", text, re.S)
        result = json.loads(match.group(0)) if match else None
        if not result or not isinstance(result.get("why"), list):
            print("AI answer not parseable for %s" % video["video_id"])
            return None
        result = {"why": [str(x) for x in result["why"][:3]], "ideas": [str(x) for x in result.get("ideas", [])[:3]]}
        _videos.update_item(Key={"video_id": video["video_id"]},
                            UpdateExpression="SET #a = :a",
                            ExpressionAttributeNames={"#a": "analysis"},
                            ExpressionAttributeValues={":a": json.dumps(result, ensure_ascii=False)})
        return result
    except urllib.error.HTTPError as err:
        print("Claude API %s: %s" % (err.code, err.read().decode("utf-8", "replace")[:300]))
    except (urllib.error.URLError, TimeoutError, ValueError) as err:
        print("Claude API failed: %s" % err)
    return None


# ---------------------------------------------------------------- email
def _hours(v):
    h = float(v.get("age_hours") or 0)
    return "%d 分鐘" % round(h * 60) if h < 1 else "%.0f 小時" % h


def render(keywords, videos):
    kw = "、".join(keywords)
    subject = "🔥 爆發雷達：「%s」有 %d 支影片正在爆" % (kw, len(videos))
    blocks, lines = [], ["🔥 爆發雷達：「%s」有 %d 支影片正在爆\n" % (kw, len(videos))]
    for i, v in enumerate(videos, 1):
        url = "https://www.youtube.com/watch?v=%s" % v["video_id"]
        stat = "發布 %s · 觀看 %s · 頻道平均的 %.1f 倍 · 每小時約 %s 次" % (
            _hours(v), format(v["views"], ","), v["ratio"], format(v["views_per_hour"], ","))
        ai = v.get("analysis")
        ai_html = ""
        ai_text = ""
        if ai:
            ai_html = (
                '<p style="margin:8px 0 4px;font-weight:600">為什麼會紅</p><ul style="margin:0 0 8px;padding-left:20px">'
                + "".join("<li>%s</li>" % html.escape(x) for x in ai["why"])
                + '</ul><p style="margin:8px 0 4px;font-weight:600">你可以這樣改編</p><ul style="margin:0;padding-left:20px">'
                + "".join("<li>%s</li>" % html.escape(x) for x in ai["ideas"]) + "</ul>"
            )
            ai_text = ("  為什麼會紅：\n" + "".join("   - %s\n" % x for x in ai["why"])
                       + "  你可以這樣改編：\n" + "".join("   - %s\n" % x for x in ai["ideas"]))
        blocks.append(
            '<div style="border:1px solid #ddd;border-radius:10px;padding:14px 16px;margin:0 0 14px">'
            '<p style="margin:0 0 4px;font-size:16px;font-weight:700">%d. <a href="%s" style="color:#6d28d9">%s</a></p>'
            '<p style="margin:0 0 6px;color:#555;font-size:13px">%s · %s</p>%s</div>'
            % (i, url, html.escape(v["title"]), html.escape(v["channel_title"]), html.escape(stat), ai_html)
        )
        lines.append("%d. %s\n  %s\n  %s · %s\n%s" % (i, v["title"], url, v["channel_title"], stat, ai_text))
    html_body = (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:600px;'
        'margin:0 auto;padding:24px;color:#111">'
        '<h1 style="font-size:20px;margin:0 0 6px">爆發雷達 🔥</h1>'
        '<p style="margin:0 0 18px;color:#555">你關注的「%s」領域，這幾支 YouTube 新影片表現遠超過頻道平常水準：</p>%s'
        '<p style="margin:20px 0"><a href="%s/app" style="background:#6d28d9;color:#fff;padding:10px 18px;'
        'border-radius:8px;text-decoration:none;font-weight:600">調整關鍵字 / 門檻 / 暫停通知</a></p>'
        '<p style="font-size:12px;color:#666">數據來源：YouTube Data API。AI 分析僅供參考。</p></div>'
        % (html.escape(kw), "".join(blocks), SITE_URL)
    )
    text_body = "\n".join(lines) + "\n調整關鍵字 / 門檻 / 暫停通知：%s/app\n" % SITE_URL
    return subject, html_body, text_body


def _send(to, subject, html_body, text_body):
    cfg = _resend_cfg()
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps({"from": cfg["from"], "to": [to], "subject": subject,
                         "html": html_body, "text": text_body}).encode(),
        headers={"Authorization": "Bearer %s" % cfg["api_key"], "Content-Type": "application/json",
                 "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("id")
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")[:300]
        if err.code == 429 or err.code >= 500:
            raise Transient("%s %s" % (err.code, detail))
        print("DROP permanent Resend error %s: %s" % (err.code, detail))
        return None
    except (urllib.error.URLError, TimeoutError) as err:
        raise Transient(str(err))


def _wants_alerts(email):
    """Re-check at send time: the subscriber may have paused notifications after the scan queued this."""
    row = _subs.get_item(Key={"email": email, "route": "RADAR"}).get("Item") or {}
    return not row.get("notifications_paused")


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            msg = json.loads(record["body"])
            email = msg["email"]
            if not _wants_alerts(email):
                print("skipped (notifications paused) %s" % email)
                continue
            fresh = [v for v in msg.get("videos", []) if not _already_sent(email, v["video_id"])]
            if not fresh:
                print("skipped (all already sent) %s" % email)
                continue
            fresh = fresh[:MAX_VIDEOS_PER_EMAIL]
            for v in fresh:
                v["analysis"] = _analysis(v, msg.get("keywords", []))
            subject, html_body, text_body = render(msg.get("keywords", []), fresh)
            resend_id = _send(email, subject, html_body, text_body)
            if not resend_id:
                continue
            now = datetime.now(timezone.utc).strftime(TS_FMT)
            for v in fresh:
                _history.put_item(Item={"pk": "%s#radar#%s" % (email, v["video_id"]), "sent_at": now,
                                        "email": email, "route": "RADAR", "video_id": v["video_id"],
                                        "views": v["views"]})
            print("sent radar digest to %s: %d video(s) id=%s" % (email, len(fresh), resend_id))
        except Transient as err:
            print("transient failure, will retry %s: %s" % (record["messageId"], err))
            failures.append({"itemIdentifier": record["messageId"]})
        except (ValueError, KeyError) as err:
            print("DROP malformed message %s: %s" % (record.get("messageId"), err))
    return {"batchItemFailures": failures}
