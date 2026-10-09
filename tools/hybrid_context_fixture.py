"""Native source/checkpoint fixtures; no provider/network or fabricated receipts."""

import json
import sqlite3
from pathlib import Path

from bridge.memory import generate_session_summary_result
from bridge.memory_scope_store import resolve_memory_scope
from bridge.provider_port import ProviderPort
from bridge.schema import initialize_database_schema
from bridge.settings import load_app_settings
from bridge.user_dialogue import format_user_dialogue_action

MARKER = "_context_history_index"
ANCHORS = {
    5: "I promise not to open the northern gate until dawn.",
    10: "I refuse to lend my compass; that refusal still holds.",
    16: "We chose the south ferry because the flood broke the northern bridge. The debt remains unpaid.",
    22: "Rowan has never read Mira's sealed dispatch and does not know its password.",
}
PUBLIC = {
    "text": "Rowan is examining the archive; the gate remains closed before dawn.",
    "visibility": "shared",
    "known_by": [],
}
PRIVATE = {"text": "PRIVATE_HYBRID_CANARY is known only to Mira.", "visibility": "restricted", "known_by": ["Mira"]}


def native_fixture(home: Path, *, count=44, critical=True, db=None, variant="unique_dialogue"):
    db = db or sqlite3.connect(":memory:")
    initialize_database_schema(db)
    for sid, created in (("s", 1.0), ("other", 2.0)):
        db.execute(
            "INSERT INTO sessions(chat_id,session_id,title,character_file,model_id,persona_id,world_file,"
            "created_at,updated_at) VALUES('c',?,'Story','','m','','',?,?)",
            (sid, created, created),
        )
    db.commit()
    settings = load_app_settings({}, home=home)
    rows = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        text = (
            f"At display {i}, Rowan examines a copper exhibit labelled {i * 7 + 3}. "
            f"The painted trim has {i + 2} tiny triangular details and a pencilled number {1000 + i}. "
            f"The corresponding catalogue describes sample {300 + i} beside alcove {20 + i}. "
            f"A visitor traces diagram {900 - i} while the amber reflection reaches pane {i + 8}."
        )
        if critical and i in ANCHORS:
            text = ANCHORS[i]
        if i == 27:
            text += " The turquoise astrolabe has a fractured rim."
        if variant == "commitment_dense":
            text += f" I promise to preserve item {i} until dawn and refuse any transfer before then."
        elif variant == "indonesian":
            text = (
                f"Pada etalase {i}, Rowan memeriksa benda tembaga dengan label {i * 7 + 3}. "
                f"Pinggirannya memiliki {i + 2} segitiga kecil dan nomor pensil {1000 + i}. "
                f"Katalog mencatat sampel {300 + i} di sebelah ruang {20 + i}. "
                f"Pengunjung menggambar diagram {900 - i} saat pantulan mencapai kaca {i + 8}."
            )
            if critical and i in ANCHORS:
                text = "Aku berjanji tidak membuka gerbang sebelum fajar; penolakan itu masih berlaku."
            if i == 27:
                text += " Astrolab pirus memiliki pinggiran retak."
        elif variant == "unsupported_script" and i == 15:
            text = "秘密を守る約束です。"
        cur = db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('c','s',?,?,?)",
            (role, text, 3.0 + i),
        )
        rows.append((int(cur.lastrowid), role, text))
    db.commit()

    def generate(_key, _model, messages, **_kwargs):
        return json.dumps({"blocks": [PUBLIC, PRIVATE]})

    accepted = generate_session_summary_result(
        db,
        "c",
        {"session_id": "s", "model_id": "m"},
        force=True,
        durable=True,
        max_segments=count,
        provider_port=ProviderPort(generate),
        app_settings=settings,
    )
    for _ in range(count):
        if accepted.complete:
            break
        prior = accepted.covered_until_rowid
        accepted = generate_session_summary_result(
            db,
            "c",
            {"session_id": "s", "model_id": "m"},
            force=True,
            durable=True,
            max_segments=count,
            provider_port=ProviderPort(generate),
            app_settings=settings,
        )
        if accepted.covered_until_rowid <= prior:
            raise ValueError("hybrid_fixture_summary_did_not_advance")
    if not accepted.complete or accepted.covered_until_rowid != rows[-1][0]:
        raise ValueError("hybrid_fixture_summary_incomplete")
    messages = [{"role": "system", "content": "CHARACTER_CARD WORLD_SETTING SYSTEM_RULES: user owns their choices."}]
    messages += [
        {"role": role, "content": format_user_dialogue_action(text) if role == "user" else text, MARKER: i}
        for i, (_, role, text) in enumerate(rows)
    ]
    messages += [
        {"role": "system", "content": "POST_HISTORY_DIRECTIVE preserve voice."},
        {"role": "user", "content": "Describe the turquoise astrolabe without choosing my next action."},
    ]
    scope = resolve_memory_scope(db, "c", {"session_id": "s"}, {"name": "Rowan"})
    if scope is None:
        raise ValueError("hybrid_fixture_scope_missing")
    return db, scope, messages, rows
