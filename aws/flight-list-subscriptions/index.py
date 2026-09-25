import json
import os
import urllib.error
import urllib.request
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

UA = "Mozilla/5.0 (compatible; flight-notifier/1.0)"
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_PUBLISHABLE_KEY"]

ddb = boto3.resource("dynamodb").Table("subscriptions")


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


def handler(event, context):
    email = verified_email(event)
    if not email:
        return _response(401, {"error": "not signed in"})

    resp = ddb.query(KeyConditionExpression=Key("email").eq(email))
    items = resp.get("Items", [])
    # M2 rows carry subscription_status, current_period_end(_date) and other numeric fields.
    return _response(200, {"subscriptions": items})


def _json_default(value):
    # DynamoDB numbers come back as Decimal (target_price, amount, total_success_times, ...)
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    raise TypeError(type(value))


def _response(status_code, body_obj):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body_obj, default=_json_default, ensure_ascii=False),
    }
