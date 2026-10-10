"""Evaluation readers own and close real SQLite handles on every exit path."""

import importlib
import sqlite3
from contextlib import closing

import pytest


@pytest.mark.parametrize("name", ["evaluate_hybrid_context", "evaluate_statement_context"])
@pytest.mark.parametrize("valid_schema", [True, False])
def test_metadata_reader_closes_connection_on_success_and_error(tmp_path, monkeypatch, name, valid_schema):
    module = importlib.import_module(f"tools.{name}")
    database = tmp_path / "metadata.sqlite3"
    with closing(sqlite3.connect(database)) as db, db:
        if valid_schema:
            db.execute("CREATE TABLE memory_layer_state(chat_id,session_id,session_created_at,layer)")
            db.execute("CREATE TABLE sessions(chat_id,session_id,created_at,character_file)")
    original_connect = sqlite3.connect
    opened = []

    def connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(module.sqlite3, "connect", connect)
    try:
        if valid_schema:
            report = module.build_live_metadata(database, object())
            assert report["cases"] == []
            assert report["production_activation_allowed"] is False
        else:
            with pytest.raises(sqlite3.OperationalError, match="no such table"):
                module.build_live_metadata(database, object())
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            opened[0].execute("SELECT 1")
    finally:
        for connection in opened:
            connection.close()
