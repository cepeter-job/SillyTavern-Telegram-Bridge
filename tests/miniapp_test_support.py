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


def card_bytes(name="Example"):
    import base64
    import struct
    import zlib

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    value = {
        "name": name,
        "description": "A thoughtful companion.",
        "personality": "Curious",
        "scenario": "A quiet afternoon",
        "first_mes": "Hello.",
        "mes_example": "",
    }
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"tEXt", b"chara\x00" + base64.b64encode(json.dumps(value).encode()))
        + chunk(b"IDAT", zlib.compress(b"\x00\x55\x88\xbb"))
        + chunk(b"IEND", b"")
    )


def make_services(tmp_path):
    from application_test_setup import ensure_application_extensions, make_test_application_services

    from bridge.settings import load_app_settings
    from bridge.sqlite_store import db_connect

    ensure_application_extensions()
    settings = load_app_settings(
        {
            "SILLYTAVERN_BRIDGE_HOME": str(tmp_path),
            "SILLYTAVERN_DIR": str(tmp_path / "native"),
            "SILLYTAVERN_MODEL": "test::model",
            "SILLYTAVERN_DEFAULT_CHARACTER": "Default.png",
            "SILLYTAVERN_TELEGRAM_BOT_TOKEN": TOKEN,
            "SILLYTAVERN_TELEGRAM_ALLOWED_USERS": "12345,67890",
            "SILLYTAVERN_MINIAPP_PUBLIC_URL": "https://bridge.example/miniapp/",
        },
        home=tmp_path,
    )
    settings.character_dir.mkdir(parents=True, exist_ok=True)
    settings.world_dir.mkdir(parents=True, exist_ok=True)
    settings.native_persona_settings_file.write_text(
        json.dumps({"power_user": {"personas": {}, "persona_descriptions": {}}})
    )
    settings.native_persona_avatar_dir.mkdir(parents=True, exist_ok=True)
    (settings.native_persona_avatar_dir / "user-default.png").write_bytes(card_bytes("User"))
    settings.card_file.write_bytes(card_bytes("Default"))
    (settings.character_dir / "Alice.png").write_bytes(card_bytes("Alice"))
    services = make_test_application_services(app_settings=settings)
    services.db_factory = lambda: db_connect(app_settings=settings)
    return services


def identity(user_id="12345"):
    from bridge.miniapp_auth import MiniAppIdentity

    return MiniAppIdentity(user_id, "Test", int(time.time()))
