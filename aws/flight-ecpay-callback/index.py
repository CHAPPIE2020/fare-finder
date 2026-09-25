"""ECPay recurring-payment callbacks (M2).

The same file is deployed twice:
  flight-ecpay-return  (CALLBACK_KIND=return)  <- ReturnURL, first charge
  flight-ecpay-period  (CALLBACK_KIND=period)  <- PeriodReturnURL, 2nd charge onward

These callbacks are the ONLY writers of subscription_status=active.
ECPay needs the plain-text reply "1|OK" (HTTP 200) or it resends up to 4 times.
"""
import base64
import calendar
import hashlib
import hmac
import json
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import ClientError

KIND = os.environ.get("CALLBACK_KIND", "return")
STATUS_QUEUE_URL = os.environ["STATUS_QUEUE_URL"]
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"  # fixed-width UTC: current_period_end is compared as a string
TW = timezone(timedelta(hours=8))
FAIL_LIMIT = 6  # ECPay auto-terminates the series after 6 consecutive failed charges

_sm = boto3.client("secretsmanager")
_sqs = boto3.client("sqs")
_table = boto3.resource("dynamodb").Table("subscriptions")
_lambda = boto3.client("lambda")
RADAR_SCAN_FUNCTION = os.environ.get("RADAR_SCAN_FUNCTION", "radar-scan")
_cfg = None


def _config():
    global _cfg
    if _cfg is None:
        _cfg = json.loads(_sm.get_secret_value(SecretId="flight/ecpay")["SecretString"])
    return _cfg


# ---- CheckMacValue (SHA256, AIO) — verified against ecpay/test-vectors/checkmacvalue.json
def _ecpay_url_encode(s):
    e = urllib.parse.quote_plus(str(s)).replace("~", "%7E").lower()
    for old, new in (("%2d", "-"), ("%5f", "_"), ("%2e", "."), ("%21", "!"),
                     ("%2a", "*"), ("%28", "("), ("%29", ")")):
        e = e.replace(old, new)
    return e


def _gen_cmv(params, hash_key, hash_iv):
    # Keep empty-string fields (ECPay signs CustomField3= / CustomField4=); drop only CheckMacValue.
    items = {k: v for k, v in params.items() if k != "CheckMacValue"}
    body = "&".join(f"{k}={items[k]}" for k in sorted(items, key=str.lower))
    raw = f"HashKey={hash_key}&{body}&HashIV={hash_iv}"
    return hashlib.sha256(_ecpay_url_encode(raw).encode()).hexdigest().upper()


def _verify_cmv(params, hash_key, hash_iv):
    got = params.get("CheckMacValue", "").upper()
    return bool(got) and hmac.compare_digest(got, _gen_cmv(params, hash_key, hash_iv))


# ---- helpers
def _text(body, status=200):
    return {"statusCode": status, "headers": {"Content-Type": "text/plain; charset=utf-8"}, "body": body}


def _parse_form(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}


def _add_period(dt, period_type, frequency):
    if period_type == "D":
        return dt + timedelta(days=frequency)
    if period_type == "Y":
        frequency *= 12
    month_index = dt.month - 1 + frequency
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _parse_ts(value):
    try:
        return datetime.strptime(value, TS_FMT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _kickoff_radar(keywords):
    """Start a Viral Radar search right away instead of waiting up to 6h for the schedule."""
    try:
        _lambda.invoke(FunctionName=RADAR_SCAN_FUNCTION, InvocationType="Event",
                       Payload=json.dumps({"mode": "kickoff", "keywords": list(keywords)},
                                          ensure_ascii=False).encode("utf-8"))
        print("radar kickoff requested for %s" % list(keywords))
    except Exception as err:  # never fail the payment/settings flow because of this
        print("radar kickoff failed (the 6h schedule will still pick it up): %s" % err)


def _enqueue(message):
    _sqs.send_message(QueueUrl=STATUS_QUEUE_URL, MessageBody=json.dumps(message, ensure_ascii=False))


def _int(value, default=0):
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


# ---- handler
def handler(event, context):
    params = _parse_form(event)
    cfg = _config()

    if not _verify_cmv(params, cfg["hash_key"], cfg["hash_iv"]):
        print(f"[{KIND}] CheckMacValue INVALID for {params.get('MerchantTradeNo')} "
              f"received={params.get('CheckMacValue')} fields={sorted(params)}")
        return _text("0|CheckMacValueError", 400)
    if params.get("MerchantID") != cfg["merchant_id"]:
        print(f"[{KIND}] MerchantID mismatch: {params.get('MerchantID')}")
        return _text("0|MerchantIDMismatch", 400)

    mtn = params.get("MerchantTradeNo", "")
    rtn = params.get("RtnCode", "")
    email = params.get("CustomField1", "")
    route = params.get("CustomField2", "")
    print(f"[{KIND}] CMV verified mtn={mtn} rtn={rtn} msg={params.get('RtnMsg')} "
          f"simulate={params.get('SimulatePaid', '')} success_times={params.get('TotalSuccessTimes', '')} "
          f"key={email}#{route}")

    if params.get("SimulatePaid") == "1":
        print(f"[{KIND}] SimulatePaid=1 -> ack only, status NOT changed")
        return _text("1|OK")
    if not email or not route:
        print(f"[{KIND}] missing CustomField1/2 -> cannot map to a subscription, ack")
        return _text("1|OK")

    item = _table.get_item(Key={"email": email, "route": route}).get("Item")
    if not item:
        print(f"[{KIND}] no subscription row for {email}#{route}, ack")
        return _text("1|OK")
    if item.get("merchant_trade_no") and item.get("merchant_trade_no") != mtn:
        print(f"[{KIND}] WARNING callback trade-no {mtn} != row trade-no {item.get('merchant_trade_no')}")

    now = datetime.now(timezone.utc)
    period_type = item.get("period_type", "M")
    frequency = _int(item.get("period_frequency"), 1) or 1

    if KIND == "return":
        return _handle_first_charge(params, item, email, route, mtn, rtn, now, period_type, frequency)
    return _handle_renewal(params, item, email, route, mtn, rtn, now, period_type, frequency)


def _handle_first_charge(params, item, email, route, mtn, rtn, now, period_type, frequency):
    if rtn != "1":
        # A failed first authorisation never enters ECPay's schedule; the row stays pending_payment.
        print(f"[return] first charge FAILED ({rtn} {params.get('RtnMsg')}), row stays "
              f"{item.get('subscription_status')}")
        return _text("1|OK")

    end = _add_period(now, period_type, frequency)
    try:
        _table.update_item(
            Key={"email": email, "route": route},
            UpdateExpression=(
                "SET subscription_status = :active, merchant_trade_no = :mtn, "
                "current_period_end = :end, current_period_end_date = :end_date, "
                "activated_at = :now, last_charged_at = :now, updated_at = :now, "
                "total_success_times = :one, failed_attempts = :zero, ecpay_trade_no = :tno"
            ),
            # Exactly-once activation: a resend for the same trade-no finds the row already active.
            ConditionExpression=(
                "attribute_not_exists(subscription_status) OR subscription_status <> :active "
                "OR merchant_trade_no <> :mtn"
            ),
            ExpressionAttributeValues={
                ":active": "active", ":mtn": mtn,
                ":end": end.strftime(TS_FMT), ":end_date": end.astimezone(TW).strftime("%Y-%m-%d"),
                ":now": now.strftime(TS_FMT), ":one": 1, ":zero": 0,
                ":tno": params.get("TradeNo", ""),
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            print(f"[return] already active for {mtn} -> idempotent ack")
            return _text("1|OK")
        raise

    print(f"[return] {email}#{route} -> active until {end.strftime(TS_FMT)}")
    _enqueue({
        "event_type": "welcome", "email": email, "route": route,
        "plan_name": item.get("plan_name"), "target_price": _int(item.get("target_price")),
        "amount": _int(params.get("TradeAmt") or params.get("Amount")),
        "current_period_end_date": end.astimezone(TW).strftime("%Y-%m-%d"),
        # Viral Radar rows carry keywords + a threshold instead of a target price
        "keywords": [str(k) for k in (item.get("keywords") or [])],
        "min_ratio": float(item.get("min_ratio") or 0),
    })
    if route == "RADAR":
        _kickoff_radar(item.get("keywords") or [])
    return _text("1|OK")


def _handle_renewal(params, item, email, route, mtn, rtn, now, period_type, frequency):
    success_times = _int(params.get("TotalSuccessTimes"))
    status = item.get("subscription_status")

    if rtn == "1":
        current_end = _parse_ts(item.get("current_period_end"))
        base = max(now, current_end) if current_end else now
        end = _add_period(base, period_type, frequency)
        # A cancelled-in-grace row keeps "cancelled" (a charge after cancel means the ECPay
        # cancel failed; the user still paid, so extend the paid-through date).
        new_status = "cancelled" if status == "cancelled" else "active"
        try:
            _table.update_item(
                Key={"email": email, "route": route},
                UpdateExpression=(
                    "SET subscription_status = :st, current_period_end = :end, "
                    "current_period_end_date = :end_date, last_charged_at = :now, updated_at = :now, "
                    "total_success_times = :t, failed_attempts = :zero"
                ),
                # ECPay resends the same period result; only count each successful charge once.
                ConditionExpression="attribute_not_exists(total_success_times) OR total_success_times < :t",
                ExpressionAttributeValues={
                    ":st": new_status, ":end": end.strftime(TS_FMT),
                    ":end_date": end.astimezone(TW).strftime("%Y-%m-%d"),
                    ":now": now.strftime(TS_FMT), ":t": success_times, ":zero": 0,
                },
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                print(f"[period] charge #{success_times} already recorded -> idempotent ack")
                return _text("1|OK")
            raise
        print(f"[period] renewal #{success_times} OK {email}#{route} -> {new_status} "
              f"until {end.strftime(TS_FMT)}")
        return _text("1|OK")

    # Failed renewal: don't expire on the first miss — ECPay retries and only
    # terminates the series after 6 consecutive failures.
    failed = _int(item.get("failed_attempts")) + 1
    expire = failed >= FAIL_LIMIT
    _table.update_item(
        Key={"email": email, "route": route},
        UpdateExpression=(
            "SET failed_attempts = :f, last_failed_at = :now, last_fail_msg = :msg, updated_at = :now"
            + (", subscription_status = :expired" if expire else "")
        ),
        ExpressionAttributeValues={
            ":f": failed, ":now": now.strftime(TS_FMT), ":msg": params.get("RtnMsg", "")[:200],
            **({":expired": "expired"} if expire else {}),
        },
    )
    print(f"[period] renewal FAILED ({rtn} {params.get('RtnMsg')}) attempt {failed}/{FAIL_LIMIT}"
          + (" -> expired (series terminated)" if expire else " -> keep status, ECPay will retry"))
    return _text("1|OK")
