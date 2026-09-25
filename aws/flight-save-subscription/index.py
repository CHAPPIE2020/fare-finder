"""POST /subscribe (M1.1 + M2 paywall).

Identity comes from the Supabase access token, never from the body.

M2 behaviour:
  - A paid subscriber (active, or cancelled but still inside the paid period) just updates the
    target price in place -> JSON response, no re-payment.
  - Everyone else (new, legacy M1 row without a status, pending_payment, expired) gets
    subscription_status=pending_payment + a fresh MerchantTradeNo, and the response is an
    auto-submit HTML form that sends the browser to ECPay's recurring-payment cashier.
Only the ECPay callbacks ever set "active".
"""
import html
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

UA = "Mozilla/5.0 (compatible; flight-notifier/1.0)"
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_PUBLISHABLE_KEY"]
SITE_URL = os.environ.get("SITE_URL", "https://fare-finder-two.vercel.app").strip().rstrip("/")
API_BASE_URL = os.environ.get("API_BASE_URL", "").strip().rstrip("/")  # optional override
# Billing cycle. For the next-day renewal test set PERIOD_TYPE=D, EXEC_TIMES=2 (no code change).
PERIOD_TYPE = os.environ.get("PERIOD_TYPE", "M").strip()
PERIOD_FREQUENCY = int(os.environ.get("PERIOD_FREQUENCY", "1"))
EXEC_TIMES = int(os.environ.get("EXEC_TIMES", "999"))
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
TW = timezone(timedelta(hours=8))

PLANS = {
    "tokyo": {"origin": "TPE", "destination": "TYO", "title": "台北-東京"},
    "seoul": {"origin": "TPE", "destination": "SEL", "title": "台北-首爾"},
}

ddb = boto3.resource("dynamodb").Table("subscriptions")
_sm = boto3.client("secretsmanager")
_ecpay = None


def _ecpay_config():
    global _ecpay
    if _ecpay is None:
        _ecpay = json.loads(_sm.get_secret_value(SecretId="flight/ecpay")["SecretString"])
    return _ecpay


def verified_email(event):
    headers = event.get("headers") or {}
    auth = headers.get("authorization") or headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    req = urllib.request.Request(
        SUPABASE_URL + "/auth/v1/user",
        headers={"Authorization": auth, "apikey": SUPABASE_KEY, "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            user = json.loads(r.read())
    except (urllib.error.HTTPError, urllib.error.URLError) as err:
        print("auth check failed: %s" % err)
        return None
    return user.get("email")


# ---- CheckMacValue (SHA256, AIO) — verified against ecpay/test-vectors/checkmacvalue.json
def _ecpay_url_encode(s):
    e = urllib.parse.quote_plus(str(s)).replace("~", "%7E").lower()
    for old, new in (("%2d", "-"), ("%5f", "_"), ("%2e", "."), ("%21", "!"),
                     ("%2a", "*"), ("%28", "("), ("%29", ")")):
        e = e.replace(old, new)
    return e


def _gen_cmv(params, hash_key, hash_iv):
    items = {k: v for k, v in params.items() if k != "CheckMacValue"}
    body = "&".join("%s=%s" % (k, items[k]) for k in sorted(items, key=str.lower))
    raw = "HashKey=%s&%s&HashIV=%s" % (hash_key, body, hash_iv)
    return hashlib.sha256(_ecpay_url_encode(raw).encode()).hexdigest().upper()


def _new_trade_no(now_tw):
    # <= 20 chars, letters/digits only: FF + yymmddHHMMSS + 6 hex
    return "FF" + now_tw.strftime("%y%m%d%H%M%S") + secrets.token_hex(3).upper()


def _api_base(event):
    if API_BASE_URL:
        return API_BASE_URL
    domain = (event.get("requestContext") or {}).get("domainName", "")
    return "https://" + domain


def _checkout_form(event, email, route, plan, trade_no, now_tw):
    cfg = _ecpay_config()
    amount = str(int(cfg["amount"]))
    api = _api_base(event)
    host = "payment.ecpay.com.tw" if cfg.get("env") == "prod" else "payment-stage.ecpay.com.tw"
    params = {
        "MerchantID": cfg["merchant_id"],
        "MerchantTradeNo": trade_no,
        "MerchantTradeDate": now_tw.strftime("%Y/%m/%d %H:%M:%S"),  # must be Taiwan time
        "PaymentType": "aio",
        "TotalAmount": amount,
        "TradeDesc": "Flight Price Notifier monthly plan",
        "ItemName": "機票降價通知 月訂閱 %s" % plan["title"],
        "ReturnURL": api + "/ecpay-return",
        "OrderResultURL": api + "/ecpay-result",
        "ClientBackURL": SITE_URL + "/app",
        "ChoosePayment": "Credit",
        "EncryptType": "1",
        "PeriodAmount": amount,  # must equal TotalAmount
        "PeriodType": PERIOD_TYPE,
        "Frequency": str(PERIOD_FREQUENCY),
        "ExecTimes": str(EXEC_TIMES),
        "PeriodReturnURL": api + "/ecpay-period",
        "CustomField1": email,  # join key back to the DynamoDB row
        "CustomField2": route,
    }
    params["CheckMacValue"] = _gen_cmv(params, cfg["hash_key"], cfg["hash_iv"])
    inputs = "".join(
        '<input type="hidden" name="%s" value="%s">' % (html.escape(k, quote=True), html.escape(v, quote=True))
        for k, v in params.items()
    )
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><title>前往綠界付款…</title></head>'
        '<body><p style="font-family:sans-serif">正在前往綠界付款頁面…</p>'
        '<form id="ecpay" action="https://%s/Cashier/AioCheckOut/V5" method="post">%s</form>'
        '<script>document.forms[0].submit()</script></body></html>' % (host, inputs)
    ), int(amount)


def _is_paid(item, now_str):
    status = item.get("subscription_status")
    if status == "active":
        return True
    return status == "cancelled" and (item.get("current_period_end") or "") >= now_str


def handler(event, context):
    email = verified_email(event)
    if not email:
        return _response(401, {"error": "not signed in"})
    if len(email) > 50:  # ECPay CustomField1 is String(50)
        return _response(400, {"error": "email too long for the payment system"})

    try:
        body = json.loads(event.get("body") or "{}")
    except (json.JSONDecodeError, TypeError):
        return _response(400, {"error": "invalid JSON body"})

    plan_name = body.get("plan_name")
    target_price = body.get("target_price")

    if plan_name not in PLANS:
        return _response(400, {"error": "plan_name must be one of: tokyo, seoul"})
    try:
        target_price_num = float(target_price)
        if target_price_num <= 0:
            raise ValueError()
    except (TypeError, ValueError):
        return _response(400, {"error": "target_price must be a positive number"})

    plan = PLANS[plan_name]
    origin = plan["origin"]
    destination = plan["destination"]
    route = origin + "-" + destination
    now_utc = datetime.now(timezone.utc)
    now = now_utc.isoformat()
    now_str = now_utc.strftime(TS_FMT)

    existing = ddb.get_item(Key={"email": email, "route": route}).get("Item")

    if existing and _is_paid(existing, now_str):
        # Paid (or cancelled-in-grace): in-place target update, status unchanged, no re-payment.
        result = ddb.update_item(
            Key={"email": email, "route": route},
            UpdateExpression="SET target_price=:t, updated_at=:u",
            ExpressionAttributeValues={":t": Decimal(str(target_price_num)), ":u": now},
            ReturnValues="ALL_NEW",
        )
        print("in-place target update %s#%s -> %s (%s)"
              % (email, route, target_price_num, existing.get("subscription_status")))
        return _response(200, {"subscription": result["Attributes"]})

    # Needs payment: write pending_payment + a fresh trade-no, return the ECPay checkout form.
    now_tw = now_utc.astimezone(TW)
    trade_no = _new_trade_no(now_tw)
    form_html, amount = _checkout_form(event, email, route, plan, trade_no, now_tw)
    ddb.update_item(
        Key={"email": email, "route": route},
        UpdateExpression="SET plan_name=:p, origin=:o, destination=:d, target_price=:t, "
                         "currency=:c, updated_at=:u, created_at=if_not_exists(created_at, :u), "
                         "subscription_status=:s, merchant_trade_no=:m, amount=:a, "
                         "period_type=:pt, period_frequency=:pf",
        ExpressionAttributeValues={
            ":p": plan_name, ":o": origin, ":d": destination,
            ":t": Decimal(str(target_price_num)), ":c": "TWD", ":u": now,
            ":s": "pending_payment", ":m": trade_no, ":a": amount,
            ":pt": PERIOD_TYPE, ":pf": PERIOD_FREQUENCY,
        },
    )
    print("checkout %s#%s trade_no=%s amount=%s period=%s/%s/%s (was %s)"
          % (email, route, trade_no, amount, PERIOD_TYPE, PERIOD_FREQUENCY, EXEC_TIMES,
             existing.get("subscription_status") if existing else "new"))
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"},
        "body": form_html,
    }


def _json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(type(value))


def _response(status_code, body_obj):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body_obj, default=_json_default, ensure_ascii=False),
    }
