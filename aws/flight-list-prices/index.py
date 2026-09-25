import json
import os

import boto3

_s3 = boto3.client("s3")
BUCKET = os.environ["CONFIG_BUCKET"]


def read_json(key):
    try:
        obj = _s3.get_object(Bucket=BUCKET, Key=key)
    except Exception as err:
        print("could not read %s: %s" % (key, err))
        return None
    return json.loads(obj["Body"].read())


def handler(event, context):
    routes = read_json("flight-routes.json") or []
    prices = []
    for r in routes:
        route = "%s-%s" % (r["origin"], r["destination"])
        doc = read_json("prices/%s.json" % route)
        if doc:
            doc["plan_name"] = r.get("plan")
            prices.append(doc)
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json", "Cache-Control": "max-age=300"},
        "body": json.dumps({"prices": prices}),
    }
