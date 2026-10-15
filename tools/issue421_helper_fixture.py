"""Synthetic, backend-free capture of native structured-helper request contracts."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

MODEL = "nano-gpt::z-ai/glm-5.2"
KINDS = ("episodes", "npc", "scene", "curator", "story")
SCENARIOS = (
    {
        "id": "gate_promise",
        "language": "en",
        "source": (
            "The current story is in the station office before dawn. Rowan is the guard; "
            "Ari is the visitor controlled by the user. Mira is a supporting character, an archivist. "
            "Mira still holds the brass cupboard key. Ari reached toward it but never received it. "
            "The cupboard and northern gate remain locked. Rowan promised Ari not to open the gate "
            "before dawn. Ari refused permission to borrow the brass compass. Rain continues outside. "
            "Mira privately sealed a dispatch; Rowan only saw its envelope, never its contents. "
            "Mira alone knows the dispatch password; the source does not disclose the password. "
            "The earlier alternative where Ari accepted an invitation was discarded. In the current "
            "branch Ari refused it. Nobody has changed that decision. No item changed hands. "
            "An inspection must be recorded before opening a sealed collection; it has not been recorded."
        ),
        "request": "I ask Rowan whether the northern gate can be opened now. Leave my next action to me.",
        "required_facts": [
            "It is before dawn and the northern gate remains locked.",
            "Rowan promised not to open the gate before dawn.",
            "Ari refused use of the compass; Mira retains the cupboard key.",
            "Rowan does not know the sealed dispatch password.",
        ],
        "forbidden_inferences": ["Ari accepts the invitation", "Ari receives the key", "Rowan knows the password"],
    },
    {
        "id": "ferry_branch",
        "language": "id",
        "source": (
            "Adegan saat ini berlangsung di dermaga selatan sebelum fajar. Rowan adalah penjaga "
            "dan Ari adalah pengunjung yang dikendalikan pengguna. Mira, tokoh pendukung, bekerja "
            "sebagai arsiparis. Banjir telah merusak penyangga jembatan utara sehingga tidak aman. "
            "Ari memilih kapal feri selatan karena jembatan tersebut rusak. Utang tiket feri belum "
            "dibayar. Rute alternatif melalui jembatan utara sudah dibatalkan dan bukan cabang aktif. "
            "Rowan berjanji menjaga peti tetap tertutup sampai fajar; fajar belum tiba. Ari menolak "
            "menyerahkan kunci. Kunci tetap di tangan Ari. Mira hanya melihat amplop tertutup, bukan "
            "isi suratnya. Tidak ada tokoh yang memperoleh pengetahuan dari pikiran pribadi tokoh lain. "
            "Hujan masih turun. Tidak ada pembayaran baru, perpindahan barang, persetujuan baru, atau "
            "tindakan pengguna lain yang telah terjadi. Rowan menunggu jawaban Ari di dekat dermaga."
        ),
        "request": "Aku bertanya kepada Rowan mengapa kita memilih feri selatan. Jangan tentukan tindakanku.",
        "required_facts": [
            "Ari memilih feri selatan karena jembatan utara rusak akibat banjir.",
            "Utang tiket feri belum dibayar.",
            "Peti tetap tertutup sampai fajar dan fajar belum tiba.",
            "Ari menolak menyerahkan kunci dan tetap memegangnya.",
        ],
        "forbidden_inferences": ["Utang sudah dibayar", "Ari memilih jembatan utara", "Peti telah dibuka"],
    },
)
EMPTY = {
    "episodes": '{"memories":[],"no_memory_reason":"Only transient fixture text was present."}',
    "npc": '{"npcs":[],"simulation":{}}',
    "scene": '{"state":{},"blocks":[]}',
    "curator": '{"memories":[]}',
    "story": '"The promise still holds," Rowan says.',
}


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def capture_case(directory: Path, scenario: dict, kind: str, *, generate=None) -> dict:
    """Capture with a fake provider, or execute via an explicitly injected bounded transport.

    Empty scripted extraction is only for capturing request shapes, never proof
    that the scenario has no facts. Native parsers and source/lease checks run.
    """
    from bridge import npc_extraction, session_core
    from bridge.application_composition import initialize_extensions
    from bridge.generation import build_chat_messages
    from bridge.memory_store import claim_jobs
    from bridge.memory_workers import run_memory_claim
    from bridge.provider_port import ProviderPort
    from bridge.settings import load_app_settings
    from bridge.sqlite_store import db_connect

    if kind not in KINDS:
        raise ValueError("invalid_helper_fixture_kind")
    initialize_extensions()
    directory.mkdir(parents=True, exist_ok=True)
    settings = replace(
        load_app_settings({}, home=directory), db_file=directory / "fixture.sqlite3", default_user_name="Ari"
    )
    settings.native_persona_settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings.native_persona_settings_file.write_text('{"power_user":{"personas":{},"persona_descriptions":{}}}')
    if settings.db_file.exists():
        raise ValueError("fixture_database_already_exists")
    calls = []

    def backend(_key, model, messages, **kwargs):
        request = {
            "model": model,
            "messages": json.loads(json.dumps(messages)),
            "settings": dict(kwargs["settings"]),
            "force_non_stream": kwargs.get("force_non_stream", False),
        }
        calls.append(request)
        return EMPTY[kind] if generate is None else generate(request)

    db = db_connect(app_settings=settings)
    try:
        with (
            patch.object(session_core, "default_persona_id", lambda **kwargs: ""),
            patch.object(session_core, "_native_default_world", lambda **kwargs: ""),
            patch.object(npc_extraction, "persona_name", lambda *args, **kwargs: "Ari"),
            patch.object(session_core.time, "time", return_value=1791633600.0),
        ):
            session = session_core.create_session(db, "evaluation", MODEL, session_id="main", app_settings=settings)
            db.execute(
                "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
                ("evaluation", "main", "assistant", scenario["source"], 1791633600.0),
            )
            db.commit()
        fields = dict.fromkeys(
            (
                "system_prompt",
                "description",
                "personality",
                "scenario",
                "first_mes",
                "mes_example",
                "post_history_instructions",
            ),
            "",
        )
        fields.update(name="Rowan", description="A precise, calm guard who respects promises and user agency.")
        port = ProviderPort(backend)
        if kind == "story":
            session.update(response_language=scenario["language"], persona_id="")
            messages = build_chat_messages(
                session,
                fields,
                scenario["request"],
                [("assistant", scenario["source"])],
                persona_service=SimpleNamespace(name=lambda _: "Ari", get=lambda _: None),
                app_settings=settings,
            )
            backend("", MODEL, messages, settings={"temperature": 0.7, "max_tokens": 1000}, force_non_stream=True)
            result = "complete"
        else:
            claim = claim_jobs(db, layers=(kind,))[0]
            result = run_memory_claim(db, claim, session, fields, provider_port=port, app_settings=settings)
        return {"status": result, "calls": calls, "source_sha256": digest(scenario["source"])}
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source_root.resolve()))
    if args.directory.exists():
        parser.error("capture directory must be new")
    args.directory.mkdir(parents=True, mode=0o700)
    result = {
        scenario["id"]: {kind: capture_case(args.directory / scenario["id"] / kind, scenario, kind) for kind in KINDS}
        for scenario in SCENARIOS
    }
    (args.directory / "captures.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"captured_cases": len(SCENARIOS) * len(KINDS), "provider_requests": 0}))


if __name__ == "__main__":
    main()
