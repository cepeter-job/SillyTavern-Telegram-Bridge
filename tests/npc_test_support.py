"""Shared NPC/session fixtures for integration-style tests."""

from __future__ import annotations

import sqlite3
import time

from bridge.schema import initialize_database_schema


def db():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    initialize_database_schema(connection)
    now = time.time()
    connection.execute(
        """
        INSERT INTO sessions(
            chat_id,session_id,title,character_file,model_id,persona_id,world_file,
            author_note,system_prompt,response_language,created_at,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        ("chat", "s1", "Session", "mira.png", "story::main", "", "", "", "", "auto", now, now),
    )
    connection.commit()
    return connection


def session():
    return {
        "chat_id": "chat",
        "session_id": "s1",
        "character_file": "mira.png",
        "model_id": "story::main",
        "persona_id": "",
        "world_file": "",
        "author_note": "",
        "system_prompt": "",
        "response_language": "auto",
    }


def fields():
    return {
        "name": "Mira",
        "system_prompt": "",
        "description": "",
        "personality": "",
        "scenario": "",
        "mes_example": "",
        "first_mes": "",
        "post_history_instructions": "",
        "alternate_greetings": "[]",
    }


def turn(connection, role, content, at):
    return int(
        connection.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES(?,?,?,?,?)",
            ("chat", "s1", role, content, at),
        ).lastrowid
    )
