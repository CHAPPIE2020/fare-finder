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
import re
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
    "tokyo": {"origin": "TPE", "destination": "TYO", "item_name": "機票降價通知 月訂閱 台北-東京"},
    "seoul": {"origin": "TPE", "destination": "SEL", "item_name": "機票降價通知 月訂閱 台北-首爾"},
    # Viral Radar (小眾爆發雷達): same paywall, different product. One row per user, route RADAR.
    "radar": {"route": "RADAR", "item_name": "爆發雷達 月訂閱 YouTube"},
}
RADAR_MAX_KEYWORDS = int(os.environ.get("RADAR_MAX_KEYWORDS", "3"))

ddb = boto3.resource("dynamodb").Table("subscriptions")
_lambda = boto3.client("lambda")
RADAR_SCAN_FUNCTION = os.environ.get("RADAR_SCAN_FUNCTION", "radar-scan")
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


def _checkout_form(event, email, route, plan_name, trade_no, now_tw):
    cfg = _ecpay_config()
    # optional per-plan price in the secret, e.g. "amount_radar": "199"; falls back to "amount"
    amount = str(int(cfg.get("amount_" + plan_name) or cfg["amount"]))
    plan = PLANS[plan_name]
    api = _api_base(event)
    host = "payment.ecpay.com.tw" if cfg.get("env") == "prod" else "payment-stage.ecpay.com.tw"
    params = {
        "MerchantID": cfg["merchant_id"],
        "MerchantTradeNo": trade_no,
        "MerchantTradeDate": now_tw.strftime("%Y/%m/%d %H:%M:%S"),  # must be Taiwan time
        "PaymentType": "aio",
        "TotalAmount": amount,
        "TradeDesc": "Flight Price Notifier monthly plan",
        "ItemName": plan["item_name"],
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


def _radar_settings(body):
    """keywords: list or comma/、 separated string (1..RADAR_MAX_KEYWORDS); min_ratio 1.5..50."""
    raw = body.get("keywords") or []
    if isinstance(raw, str):
        raw = re.split(r"[,，、;；\n]", raw)
    keywords, seen = [], set()
    for k in raw:
        k = " ".join(str(k).split())
        if k and k.lower() not in seen:
            seen.add(k.lower())
            keywords.append(k[:30])
    if not keywords:
        return None, "請至少輸入 1 個關鍵字"
    if len(keywords) > RADAR_MAX_KEYWORDS:
        return None, "關鍵字最多 %d 個" % RADAR_MAX_KEYWORDS
    try:
        min_ratio = float(body.get("min_ratio", 3))
    except (TypeError, ValueError):
        return None, "min_ratio 必須是數字"
    if not 1.5 <= min_ratio <= 50:
        return None, "爆發倍數門檻需介於 1.5 到 50"
    return {"keywords": keywords, "min_ratio": Decimal(str(min_ratio))}, None


def _kickoff_radar(keywords):
    """Start a Viral Radar search right away instead of waiting up to 6h for the schedule."""
    try:
        _lambda.invoke(FunctionName=RADAR_SCAN_FUNCTION, InvocationType="Event",
                       Payload=json.dumps({"mode": "kickoff", "keywords": list(keywords)},
                                          ensure_ascii=False).encode("utf-8"))
        print("radar kickoff requested for %s" % list(keywords))
    except Exception as err:  # never fail the payment/settings flow because of this
        print("radar kickoff failed (the 6h schedule will still pick it up): %s" % err)


def _plain(settings):
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in settings.items()}


def _update(email, route, set_values, set_if_missing=None, return_new=False):
    """UpdateItem with aliased attribute names (avoids DynamoDB reserved words)."""
    names, values, parts = {}, {}, []
    for i, (k, v) in enumerate(set_values.items()):
        names["#s%d" % i], values[":s%d" % i] = k, v
        parts.append("#s%d = :s%d" % (i, i))
    for i, (k, v) in enumerate((set_if_missing or {}).items()):
        names["#m%d" % i], values[":m%d" % i] = k, v
        parts.append("#m%d = if_not_exists(#m%d, :m%d)" % (i, i, i))
    kwargs = {"Key": {"email": email, "route": route}, "UpdateExpression": "SET " + ", ".join(parts),
              "ExpressionAttributeNames": names, "ExpressionAttributeValues": values}
    if return_new:
        kwargs["ReturnValues"] = "ALL_NEW"
    return ddb.update_item(**kwargs)


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
    if plan_name not in PLANS:
        return _response(400, {"error": "plan_name must be one of: " + ", ".join(PLANS)})
    plan = PLANS[plan_name]

    if plan_name == "radar":
        settings, error = _radar_settings(body)
        if error:
            return _response(400, {"error": error})
        route = plan["route"]
        record = {"plan_name": plan_name, "currency": "TWD", **settings}
    else:
        try:
            target_price_num = float(body.get("target_price"))
            if target_price_num <= 0:
                raise ValueError()
        except (TypeError, ValueError):
            return _response(400, {"error": "target_price must be a positive number"})
        settings = {"target_price": Decimal(str(target_price_num))}
        route = plan["origin"] + "-" + plan["destination"]
        record = {"plan_name": plan_name, "origin": plan["origin"], "destination": plan["destination"],
                  "currency": "TWD", **settings}

    now_utc = datetime.now(timezone.utc)
    now = now_utc.isoformat()
    now_str = now_utc.strftime(TS_FMT)

    existing = ddb.get_item(Key={"email": email, "route": route}).get("Item")

    if existing and _is_paid(existing, now_str):
        # Paid (or cancelled-in-grace): in-place settings update, status unchanged, no re-payment.
        result = _update(email, route, {**settings, "updated_at": now}, return_new=True)
        print("in-place update %s#%s -> %s (%s)"
              % (email, route, _plain(settings), existing.get("subscription_status")))
        if plan_name == "radar":
            before = {k.lower() for k in (existing.get("keywords") or [])}
            added = [k for k in settings["keywords"] if k.lower() not in before]
            if added:
                _kickoff_radar(added)
        return _response(200, {"subscription": result["Attributes"]})

    # Needs payment: write pending_payment + a fresh trade-no, return the ECPay checkout form.
    now_tw = now_utc.astimezone(TW)
    trade_no = _new_trade_no(now_tw)
    form_html, amount = _checkout_form(event, email, route, plan_name, trade_no, now_tw)
    _update(email, route, {
        **record, "updated_at": now, "subscription_status": "pending_payment",
        "merchant_trade_no": trade_no, "amount": amount,
        "period_type": PERIOD_TYPE, "period_frequency": PERIOD_FREQUENCY,
    }, set_if_missing={"created_at": now})
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
