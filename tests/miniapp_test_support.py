"""Signed Telegram fixtures; never use a real bot token in tests."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

TOKEN = "123456:synthetic-miniapp-token"


def signed_data(*, user_id=12345, now=None, **extra):
    fields = {
        "auth_date": str(int(time.time() if now is None else now)),
        "user": json.dumps({"id": user_id, "first_name": "Test <user>"}, separators=(",", ":")),
        **extra,
    }
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    key = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(key, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)
