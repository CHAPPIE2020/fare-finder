"""OrderResultURL target (M2).

ECPay returns the shopper's browser with a POST. A static SPA answers that POST with 405,
so this Lambda turns it into a 302 redirect back to the app. It never changes subscription
state — activation is ReturnURL's job (flight-ecpay-return).
"""
import base64
import os
import urllib.parse

SITE_URL = os.environ.get("SITE_URL", "https://fare-finder-two.vercel.app").strip().rstrip("/")


def handler(event, context):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", "replace")
    params = {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
    rtn = params.get("RtnCode", "")
    result = "failed" if rtn and rtn != "1" else "success"
    print(f"browser return mtn={params.get('MerchantTradeNo', '')} rtn={rtn} -> {result}")
    return {
        "statusCode": 302,
        "headers": {"Location": f"{SITE_URL}/app?purchase={result}", "Cache-Control": "no-store"},
        "body": "",
    }
