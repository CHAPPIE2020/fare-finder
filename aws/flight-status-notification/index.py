"""Subscription status emails (M2) — the ONE consumer of flight-status-queue.

Producers stamp event_type on each message:
  "welcome" <- flight-ecpay-return (first charge succeeded)
  "cancel"  <- flight-cancel-subscription
Failure classes: 429/5xx/network -> retry that message (batchItemFailures);
403/422 -> permanent, log and drop (no DLQ, so never loop on them).
"""
import html
import json
import os
import urllib.error
import urllib.request

import boto3

SITE_URL = os.environ.get("SITE_URL", "https://fare-finder-two.vercel.app").strip().rstrip("/")
UA = "Mozilla/5.0 (compatible; flight-notifier/1.0)"  # Resend sits behind Cloudflare (error 1010)
ROUTE_NAMES = {"TPE-TYO": "台北 → 東京", "TPE-SEL": "台北 → 首爾"}

_sm = boto3.client("secretsmanager")
_resend = None


class Transient(Exception):
    pass


def _resend_cfg():
    global _resend
    if _resend is None:
        _resend = json.loads(_sm.get_secret_value(SecretId="flight/resend")["SecretString"])
    return _resend


def _route_name(route):
    return ROUTE_NAMES.get(route, route)


def _card(title, lines, button_text, button_url):
    body = "".join(f'<p style="margin:0 0 12px;font-size:15px;line-height:1.6">{line}</p>' for line in lines)
    return (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;max-width:520px;'
        'margin:0 auto;padding:24px;color:#111">'
        f'<h1 style="font-size:20px;margin:0 0 16px">{title}</h1>{body}'
        f'<p style="margin:24px 0"><a href="{button_url}" style="background:#6d28d9;color:#fff;'
        'padding:12px 20px;border-radius:8px;text-decoration:none;font-weight:600">'
        f'{button_text}</a></p>'
        '<p style="font-size:12px;color:#666;margin-top:32px">Flight Price Notifier · 機票降價通知</p>'
        '</div>'
    )


def render(msg):
    route = _route_name(msg.get("route", ""))
    safe_route = html.escape(route)
    end_date = msg.get("current_period_end_date", "")
    app_url = f"{SITE_URL}/app"

    if msg["event_type"] == "welcome":
        amount = msg.get("amount") or 0
        target = msg.get("target_price") or 0
        subject = f"✅ 訂閱成功：{route} 降價通知已啟用"
        lines = [
            f"你已成功訂閱 <b>{safe_route}</b> 的機票降價通知。",
            f"目標價：<b>NT${target:,}</b>。只要最低票價降到目標價以下，我們就會寄信通知你。",
            (f"月費 NT${amount:,}，本期有效至 <b>{html.escape(end_date)}</b>，之後每月自動續訂。"
             if amount else f"本期有效至 <b>{html.escape(end_date)}</b>，之後每月自動續訂。"),
            "想更改目標價或取消訂閱，隨時可以到儀表板操作。",
        ]
        text = (
            f"你已成功訂閱 {route} 的機票降價通知。\n"
            f"目標價：NT${target:,}\n"
            + (f"月費 NT${amount:,}，本期有效至 {end_date}，之後每月自動續訂。\n" if amount
               else f"本期有效至 {end_date}，之後每月自動續訂。\n")
            + f"管理訂閱：{app_url}\n"
        )
        return subject, _card("訂閱成功 🎉", lines, "前往儀表板", app_url), text

    if msg["event_type"] == "cancel":
        subject = f"已取消訂閱：{route}"
        lines = [
            f"你已取消 <b>{safe_route}</b> 的訂閱，之後不會再自動扣款。",
            f"已付費的這一期仍然有效：降價通知會持續寄送到 <b>{html.escape(end_date)}</b>。",
            "想恢復通知，隨時可以回到儀表板重新訂閱。",
        ]
        text = (
            f"你已取消 {route} 的訂閱，之後不會再自動扣款。\n"
            f"降價通知會持續寄送到 {end_date}。\n"
            f"重新訂閱：{app_url}\n"
        )
        return subject, _card("訂閱已取消", lines, "回到儀表板", app_url), text

    raise ValueError(f"unknown event_type {msg.get('event_type')!r}")


def _send(to, subject, html_body, text_body):
    cfg = _resend_cfg()
    payload = {"from": cfg["from"], "to": [to], "subject": subject, "html": html_body, "text": text_body}
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {cfg['api_key']}", "Content-Type": "application/json",
                 "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read()).get("id")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 429 or exc.code >= 500:
            raise Transient(f"{exc.code} {detail}")
        print(f"DROP permanent Resend error {exc.code}: {detail}")
        return None
    except (urllib.error.URLError, TimeoutError) as exc:
        raise Transient(str(exc))


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            msg = json.loads(record["body"])
            subject, html_body, text_body = render(msg)
            resend_id = _send(msg["email"], subject, html_body, text_body)
            if resend_id:
                print(f"sent {msg['event_type']} to {msg['email']}#{msg.get('route')} id={resend_id}")
        except Transient as exc:
            print(f"transient failure, will retry {record['messageId']}: {exc}")
            failures.append({"itemIdentifier": record["messageId"]})
        except (ValueError, KeyError) as exc:
            print(f"DROP malformed message {record.get('messageId')}: {exc}")
    return {"batchItemFailures": failures}
