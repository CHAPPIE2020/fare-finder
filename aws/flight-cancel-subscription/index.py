"""POST /cancel (M2) — stop future ECPay charges, keep the paid-through period.

Identity comes from the Supabase access token (Authorization: Bearer ...), never from the body.
Cancel = "don't renew": ECPay CreditCardPeriodAction Action=Cancel, then the row becomes
"cancelled" with current_period_end preserved; the parser keeps alerting until that date
and later flips the row to "expired".
"""
import calendar
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

SUPABASE_URL = os.environ["SUPABASE_URL"].strip().rstrip("/")
SUPABASE_PUBLISHABLE_KEY = os.environ["SUPABASE_PUBLISHABLE_KEY"].strip()
STATUS_QUEUE_URL = os.environ["STATUS_QUEUE_URL"]
UA = "Mozilla/5.0 (compatible; flight-notifier/1.0)"
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
TW = timezone(timedelta(hours=8))
PLANS = {"tokyo": "TPE-TYO", "seoul": "TPE-SEL", "radar": "RADAR"}

_sm = boto3.client("secretsmanager")
_sqs = boto3.client("sqs")
_table = boto3.resource("dynamodb").Table("subscriptions")
_cfg = None


def _config():
    global _cfg
    if _cfg is None:
        _cfg = json.loads(_sm.get_secret_value(SecretId="flight/ecpay")["SecretString"])
    return _cfg


def _json(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, ensure_ascii=False, default=_default)}


def _default(value):
    from decimal import Decimal
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(type(value))


def _authenticated_email(event):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    auth = headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    req = urllib.request.Request(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={"Authorization": auth, "apikey": SUPABASE_PUBLISHABLE_KEY, "User-Agent": UA},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read()).get("email") or None
    except urllib.error.HTTPError as exc:
        print(f"Supabase rejected token: {exc.code}")
        return None


# ---- CheckMacValue (same algorithm as the callbacks)
def _ecpay_url_encode(s):
    e = urllib.parse.quote_plus(str(s)).replace("~", "%7E").lower()
    for old, new in (("%2d", "-"), ("%5f", "_"), ("%2e", "."), ("%21", "!"),
                     ("%2a", "*"), ("%28", "("), ("%29", ")")):
        e = e.replace(old, new)
    return e


def _gen_cmv(params, hash_key, hash_iv):
    items = {k: v for k, v in params.items() if k != "CheckMacValue"}
    body = "&".join(f"{k}={items[k]}" for k in sorted(items, key=str.lower))
    raw = f"HashKey={hash_key}&{body}&HashIV={hash_iv}"
    return hashlib.sha256(_ecpay_url_encode(raw).encode()).hexdigest().upper()


def _ecpay_cancel(merchant_trade_no):
    """Returns (rtn_code, rtn_msg). Raises on network/5xx so we don't cancel only locally."""
    cfg = _config()
    host = "payment.ecpay.com.tw" if cfg.get("env") == "prod" else "payment-stage.ecpay.com.tw"
    params = {"MerchantID": cfg["merchant_id"], "MerchantTradeNo": merchant_trade_no,
              "Action": "Cancel", "TimeStamp": str(int(time.time()))}
    params["CheckMacValue"] = _gen_cmv(params, cfg["hash_key"], cfg["hash_iv"])
    req = urllib.request.Request(
        f"https://{host}/Cashier/CreditCardPeriodAction",
        data=urllib.parse.urlencode(params).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8", "replace")
    result = {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
    print(f"ECPay CreditCardPeriodAction Cancel {merchant_trade_no}: {raw[:300]}")
    return result.get("RtnCode", ""), result.get("RtnMsg", "")


def _add_month(dt):
    month_index = dt.month
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    return dt.replace(year=year, month=month, day=min(dt.day, calendar.monthrange(year, month)[1]))


def handler(event, context):
    email = _authenticated_email(event)
    if not email:
        return _json(401, {"error": "請先登入"})

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _json(400, {"error": "invalid JSON"})
    route = body.get("route") or PLANS.get(body.get("plan_name", ""))
    if route not in PLANS.values():
        return _json(400, {"error": "unknown route"})

    item = _table.get_item(Key={"email": email, "route": route}).get("Item")
    if not item:
        return _json(404, {"error": "找不到這筆訂閱"})
    status = item.get("subscription_status")
    if status == "cancelled":
        return _json(200, {"subscription": item})  # idempotent
    if status != "active":
        return _json(409, {"error": "這筆訂閱目前沒有生效中的扣款，不需要取消"})

    mtn = item.get("merchant_trade_no")
    if mtn:
        try:
            rtn, msg = _ecpay_cancel(mtn)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"ECPay cancel call failed, not cancelling locally: {exc}")
            return _json(502, {"error": "暫時無法連線綠界，請稍後再試"})
        if rtn != "1":
            # e.g. 90100150 (unknown order) for a never-scheduled/synthetic order: log and still
            # cancel locally — the local state is what gates alerts.
            print(f"ECPay cancel returned {rtn} {msg}; cancelling locally anyway")
    else:
        print("row has no merchant_trade_no; cancelling locally only")

    now = datetime.now(timezone.utc)
    fallback_end = _add_month(now)  # rows activated before period tracking existed
    try:
        result = _table.update_item(
            Key={"email": email, "route": route},
            UpdateExpression=(
                "SET subscription_status = :cancelled, cancelled_at = :now, updated_at = :now, "
                "current_period_end = if_not_exists(current_period_end, :fb), "
                "current_period_end_date = if_not_exists(current_period_end_date, :fb_date)"
            ),
            ConditionExpression="subscription_status = :active",
            ExpressionAttributeValues={
                ":cancelled": "cancelled", ":active": "active", ":now": now.strftime(TS_FMT),
                ":fb": fallback_end.strftime(TS_FMT),
                ":fb_date": fallback_end.astimezone(TW).strftime("%Y-%m-%d"),
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            latest = _table.get_item(Key={"email": email, "route": route}).get("Item")
            return _json(200, {"subscription": latest})
        raise

    updated = result["Attributes"]
    _sqs.send_message(QueueUrl=STATUS_QUEUE_URL, MessageBody=json.dumps({
        "event_type": "cancel", "email": email, "route": route, "plan_name": updated.get("plan_name"),
        "current_period_end_date": updated.get("current_period_end_date"),
    }, ensure_ascii=False))
    print(f"cancelled {email}#{route}, still served until {updated.get('current_period_end')}")
    return _json(200, {"subscription": updated})
