"""Function-scoped session/database fixtures without domain-specific autouse guards."""

import json

import pytest
from settings_test_support import make_test_settings

from bridge.session_core import create_session
from bridge.sqlite_store import db_connect


@pytest.fixture
def session_db(tmp_path):
    settings = make_test_settings(home=tmp_path, db_file=tmp_path / "db.sqlite3")
    settings.native_persona_settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings.native_persona_settings_file.write_text(
        json.dumps({"power_user": {"personas": {}, "persona_descriptions": {}}})
    )
    db = db_connect(app_settings=settings)
    session = create_session(db, "chat", "dummy::model", session_id="s1", app_settings=settings)
    yield settings, db, session
    db.close()
