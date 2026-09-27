"""Telegram-signed Mini App identity. No environment, transport or storage access."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


@dataclass(frozen=True)
class MiniAppIdentity:
    user_id: str
    name: str
    auth_date: int

    @property
    def chat_id(self) -> str:
        """This management surface supports private bot chats only."""
        return self.user_id


def authenticate(
    raw: str, bot_token: str, allowed_users: frozenset[str], *, now: float | None = None, max_age: int = 3600
) -> MiniAppIdentity:
    """Validate raw initData using Telegram's WebAppData HMAC, then authorize."""
    invalid = ValueError("Open the Mini App again from an authorized Telegram account.")
    if not raw or len(raw) > 8192 or not bot_token or re.search(r"%(?![0-9a-fA-F]{2})", raw):
        raise invalid
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True, errors="strict", max_num_fields=32)
        fields = dict(pairs)
        if len(fields) != len(pairs) or any(not re.fullmatch(r"[a-zA-Z0-9_]+", k) for k in fields):
            raise invalid
        if any("\n" in v or "\r" in v or "\x00" in v for v in fields.values()):
            raise invalid
        received = fields.pop("hash")
        if not re.fullmatch(r"[a-fA-F0-9]{64}", received):
            raise invalid
        check = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hmac.digest(b"WebAppData", bot_token.encode(), "sha256")
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received.lower()):
            raise invalid
        if not re.fullmatch(r"[0-9]{1,12}", fields["auth_date"]):
            raise invalid
        date = int(fields["auth_date"])
        age = (time.time() if now is None else now) - date
        if age < -30 or age > max_age:
            raise invalid
        user = json.loads(fields["user"])
        user_id = user.get("id")
        if type(user_id) is not int or not 0 < user_id < 2**53 or str(user_id) not in allowed_users:
            raise invalid
        name = user.get("first_name", "Telegram user")
        if not isinstance(name, str):
            raise invalid
        return MiniAppIdentity(str(user_id), name[:120], date)
    except (KeyError, TypeError, AttributeError, UnicodeError, ValueError):
        raise invalid from None
