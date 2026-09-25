import html
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

UA = "Mozilla/5.0 (compatible; flight-notifier/1.0)"
NOTIFY_FLOOR_HOURS = float(os.environ.get("NOTIFY_FLOOR_HOURS", "24"))
REALERT_PCT = float(os.environ.get("REALERT_PCT", "20"))
REALERT_ABS_TWD = float(os.environ.get("REALERT_ABS_TWD", "2000"))

CITY = {"TPE": "\u53f0\u5317", "TYO": "\u6771\u4eac", "SEL": "\u9996\u723e"}

_sm = boto3.client("secretsmanager")
_hist = boto3.resource("dynamodb").Table("notification_history")
_resend_cfg = None


class TransientError(Exception):
    pass


def resend_config():
    global _resend_cfg
    if _resend_cfg is None:
        secret = _sm.get_secret_value(SecretId="flight/resend")
        _resend_cfg = json.loads(secret["SecretString"])
    return _resend_cfg


def city_pair(route):
    origin, _, dest = route.partition("-")
    return CITY.get(origin, origin), CITY.get(dest, dest)


def ddmm(iso):
    if not iso or len(iso) < 10:
        return ""
    return iso[8:10] + iso[5:7]


def booking_url(route, fare, marker=None):
    origin, _, dest = route.partition("-")
    path = origin + ddmm(fare.get("depart_date")) + dest + ddmm(fare.get("return_date")) + "1"
    url = "https://www.aviasales.com/search/" + path
    if marker:
        url += "?marker=" + str(marker)
    return url


def fmt_twd(n):
    return "NT$" + format(int(n), ",")


def subject(route, fare):
    a, b = city_pair(route)
    return "\u2708\ufe0f %s \u2192 %s \u964d\u50f9\u901a\u77e5\uff01%s \u5df2\u9054\u6a19" % (a, b, fmt_twd(fare["price"]))


def trip_line(fare):
    parts = []
    if fare.get("airline"):
        parts.append("\u822a\u7a7a\u516c\u53f8 " + fare["airline"])
    if fare.get("depart_date"):
        parts.append("\u53bb\u7a0b " + fare["depart_date"][:10])
    if fare.get("return_date"):
        parts.append("\u56de\u7a0b " + fare["return_date"][:10])
    return " \u00b7 ".join(parts)


def render_html(route, fare, target_price, usd_price=None, marker=None):
    a, b = city_pair(route)
    e = html.escape
    usd = ""
    if usd_price is not None:
        usd = '<p style="margin:0 0 16px;color:#555;font-size:15px;">\u7d04 US$%s</p>' % e(str(usd_price))
    return (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'max-width:480px;margin:0 auto;padding:24px;color:#111;">'
        '<p style="margin:0 0 4px;color:#555;font-size:15px;">%s \u2192 %s</p>'
        '<p style="margin:0 0 4px;font-size:32px;font-weight:700;">%s</p>'
        '%s'
        '<p style="margin:0 0 8px;font-size:15px;">\u5df2\u4f4e\u65bc\u4f60\u8a2d\u5b9a\u7684\u76ee\u6a19\u50f9 %s\u3002</p>'
        '<p style="margin:0 0 24px;color:#555;font-size:14px;">%s</p>'
        '<p style="margin:0 0 24px;"><a href="%s" style="display:inline-block;background:#7c3aed;'
        'color:#ffffff;padding:12px 22px;border-radius:8px;text-decoration:none;font-weight:600;">'
        '\u7acb\u5373\u8a02\u8cfc</a></p>'
        '<p style="margin:0;color:#888;font-size:12px;line-height:1.5;">'
        '\u7968\u50f9\u4f86\u81ea\u822a\u73ed\u6bd4\u50f9\u8cc7\u6599\u7684\u5feb\u53d6,\u5be6\u969b\u50f9\u683c\u4ee5\u8a02\u7968\u7db2\u7ad9\u70ba\u6e96\u3002'
        '\u4f60\u6703\u6536\u5230\u9019\u5c01\u4fe1,\u662f\u56e0\u70ba\u4f60\u5728 Flight Price Notifier \u8a02\u95b1\u4e86\u9019\u689d\u822a\u7dda\u3002</p>'
        '</div>'
    ) % (
        e(a), e(b), e(fmt_twd(fare["price"])), usd, e(fmt_twd(target_price)),
        e(trip_line(fare)), e(booking_url(route, fare, marker)),
    )


def render_text(route, fare, target_price, usd_price=None, marker=None):
    a, b = city_pair(route)
    lines = ["%s \u2192 %s \u964d\u50f9\u901a\u77e5" % (a, b), "", "\u76ee\u524d\u6700\u4f4e\u50f9:%s" % fmt_twd(fare["price"])]
    if usd_price is not None:
        lines.append("\u7d04 US$%s" % usd_price)
    lines.append("\u4f60\u7684\u76ee\u6a19\u50f9:%s" % fmt_twd(target_price))
    info = trip_line(fare)
    if info:
        lines.append(info)
    lines += ["", "\u7acb\u5373\u8a02\u8cfc:" + booking_url(route, fare, marker), "",
              "\u7968\u50f9\u4f86\u81ea\u822a\u73ed\u6bd4\u50f9\u8cc7\u6599\u7684\u5feb\u53d6,\u5be6\u969b\u50f9\u683c\u4ee5\u8a02\u7968\u7db2\u7ad9\u70ba\u6e96\u3002"]
    return "\n".join(lines)


def last_notification(pk):
    resp = _hist.query(KeyConditionExpression=Key("pk").eq(pk), ScanIndexForward=False, Limit=1)
    items = resp.get("Items", [])
    return items[0] if items else None


def should_send(last, new_price, now):
    if last is None:
        return True, "first alert"
    sent_at = datetime.fromisoformat(last["sent_at"])
    age_h = (now - sent_at).total_seconds() / 3600
    if age_h >= NOTIFY_FLOOR_HOURS:
        return True, "last alert %.1fh ago" % age_h
    last_price = Decimal(str(last["price"]))
    new = Decimal(str(new_price))
    if new <= last_price * (Decimal(1) - Decimal(str(REALERT_PCT)) / Decimal(100)):
        return True, "re-alert: >=%s%% below last %s" % (REALERT_PCT, last_price)
    if last_price - new >= Decimal(str(REALERT_ABS_TWD)):
        return True, "re-alert: >=%s TWD below last %s" % (REALERT_ABS_TWD, last_price)
    return False, "last alert %.1fh ago at %s" % (age_h, last_price)


def send_email(cfg, to, subj, html_body, text_body):
    payload = {"from": cfg["from"], "to": [to], "subject": subj, "html": html_body, "text": text_body}
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + cfg["api_key"],
            "Content-Type": "application/json",
            "User-Agent": UA,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode("utf-8", "replace")
    except urllib.error.URLError as err:
        raise TransientError("network error: %s" % err)


def process(msg):
    email = msg["email"]
    route = msg["route"]
    fare = msg["cheapest"]
    target = msg["target_price"]
    usd = msg.get("cheapest_usd")
    pk = "%s#%s" % (email, route)
    now = datetime.now(timezone.utc)

    ok, reason = should_send(last_notification(pk), fare["price"], now)
    if not ok:
        print("skipped (deduped) %s %s TWD: %s" % (pk, fare["price"], reason))
        return

    usd_price = usd["price"] if usd else None
    cfg = resend_config()
    marker = cfg.get("marker")
    status, body = send_email(
        cfg, email, subject(route, fare),
        render_html(route, fare, target, usd_price, marker),
        render_text(route, fare, target, usd_price, marker),
    )
    if 200 <= status < 300:
        _hist.put_item(Item={
            "pk": pk,
            "sent_at": now.isoformat(),
            "email": email,
            "route": route,
            "price": Decimal(str(fare["price"])),
            "currency": "TWD",
        })
        print("sent %s %s TWD (%s): %s" % (pk, fare["price"], reason, body))
    elif status == 429 or status >= 500:
        raise TransientError("resend %s: %s" % (status, body))
    else:
        print("dropped (permanent resend %s) %s: %s" % (status, pk, body))


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            process(json.loads(record["body"]))
        except TransientError as err:
            print("transient failure, will retry: %s" % err)
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
