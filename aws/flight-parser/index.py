import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr

UA = "Mozilla/5.0 (compatible; flight-notifier/1.0)"
QURL = os.environ["FARE_QUEUE_URL"]

_sm = boto3.client("secretsmanager")
_sqs = boto3.client("sqs")
_s3 = boto3.client("s3")
CONFIG_BUCKET = os.environ["CONFIG_BUCKET"]
_tbl = boto3.resource("dynamodb").Table("subscriptions")


def next_month():
    now = datetime.now(timezone.utc)
    if now.month == 12:
        return "%04d-01" % (now.year + 1)
    return "%04d-%02d" % (now.year, now.month + 1)


def get_token():
    secret = _sm.get_secret_value(SecretId="flight/travelpayouts")
    return json.loads(secret["SecretString"])["token"]


def fetch_cheapest(origin, destination, month, token, currency):
    q = urllib.parse.urlencode({
        "origin": origin,
        "destination": destination,
        "depart_date": month,
        "currency": currency,
        "token": token,
    })
    req = urllib.request.Request(
        "https://api.travelpayouts.com/v1/prices/cheap?" + q,
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print("travelpayouts HTTP %s for %s-%s %s" % (e.code, origin, destination, currency))
        return None
    except urllib.error.URLError as e:
        print("travelpayouts network error for %s-%s %s: %s" % (origin, destination, currency, e))
        return None
    if not body.get("success") or not body.get("data"):
        return None
    offers = body["data"].get(destination, {})
    if not offers:
        return None
    best = min(offers.values(), key=lambda o: o["price"])
    return {
        "price": best["price"],
        "currency": currency.upper(),
        "airline": best.get("airline"),
        "depart_date": best.get("departure_at"),
        "return_date": best.get("return_at"),
    }


def save_latest_price(route, month, tw, us):
    doc = {
        "route": route,
        "month": month,
        "price": tw["price"],
        "currency": "TWD",
        "airline": tw["airline"],
        "depart_date": tw["depart_date"],
        "return_date": tw["return_date"],
        "usd_price": us["price"] if us else None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _s3.put_object(
            Bucket=CONFIG_BUCKET,
            Key="prices/%s.json" % route,
            Body=json.dumps(doc).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception as err:
        print("could not save latest price for %s: %s" % (route, err))


def scan_route(route):
    items = []
    kwargs = {"FilterExpression": Attr("route").eq(route)}
    while True:
        resp = _tbl.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            return items
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


TS_FMT = "%Y-%m-%dT%H:%M:%SZ"  # same fixed-width UTC format the ECPay callbacks write


def gate(it, now_str):
    """M2 paywall. Returns "serve", "skip" or "expire".

    Serve active rows, and cancelled rows still inside their paid period (grace).
    A cancelled row whose period has passed is retired here (lazily) -> expired.
    pending_payment / expired / legacy M1 rows without a status are never served.
    """
    status = it.get("subscription_status")
    if status == "active":
        return "serve"
    if status == "cancelled":
        end = it.get("current_period_end") or ""
        return "serve" if end >= now_str else "expire"
    return "skip"


def expire_row(it, now_str):
    try:
        _tbl.update_item(
            Key={"email": it["email"], "route": it["route"]},
            UpdateExpression="SET subscription_status = :x, expired_at = :n, updated_at = :n",
            ConditionExpression="subscription_status = :c",
            ExpressionAttributeValues={":x": "expired", ":c": "cancelled", ":n": now_str},
        )
        print("grace over: %s#%s cancelled -> expired" % (it["email"], it["route"]))
    except Exception as err:
        print("could not expire %s#%s: %s" % (it.get("email"), it.get("route"), err))


def handler(event, context):
    origin = event["origin"]
    destination = event["destination"]
    route = event.get("route") or ("%s-%s" % (origin, destination))
    month = next_month()
    token = get_token()

    tw = fetch_cheapest(origin, destination, month, token, "twd")
    if not tw:
        print("no TWD fare for %s %s (empty/429) - skipping" % (route, month))
        return {"ok": True, "route": route, "matched": 0}
    print("%s %s cheapest %s TWD (%s)" % (route, month, tw["price"], tw["airline"]))

    us = fetch_cheapest(origin, destination, month, token, "usd")
    save_latest_price(route, month, tw, us)

    subscribers = scan_route(route)
    now_str = datetime.now(timezone.utc).strftime(TS_FMT)
    matched = 0
    counts = {"serve": 0, "skip": 0, "expire": 0}
    for it in subscribers:
        decision = gate(it, now_str)
        counts[decision] += 1
        if decision == "expire":
            expire_row(it, now_str)
        if decision != "serve":
            continue
        tp = it.get("target_price")
        if tp is None:
            continue
        if Decimal(str(tp)) >= Decimal(str(tw["price"])):
            body = {
                "email": it["email"],
                "route": route,
                "plan_name": it.get("plan_name"),
                "target_price": int(tp),
                "cheapest": {
                    "price": tw["price"],
                    "currency": "TWD",
                    "airline": tw["airline"],
                    "depart_date": tw["depart_date"],
                    "return_date": tw["return_date"],
                },
            }
            if us:
                body["cheapest_usd"] = {
                    "price": us["price"],
                    "currency": "USD",
                    "airline": us["airline"],
                    "depart_date": us["depart_date"],
                    "return_date": us["return_date"],
                }
            _sqs.send_message(QueueUrl=QURL, MessageBody=json.dumps(body))
            matched += 1

    print("%s: %d subscriber(s) [paid %d, unpaid %d, expired now %d], %d matched and enqueued"
          % (route, len(subscribers), counts["serve"], counts["skip"], counts["expire"], matched))
    return {"ok": True, "route": route, "matched": matched}
