import pytest

from bridge.greeting_media import first_image_url


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Read https://example.org/story; see https://cdn.example.org/pic.webp?token=1",
            "https://cdn.example.org/pic.webp?token=1",
        ),
        ("![portrait](https://cdn.example.org/image?id=1)", "https://cdn.example.org/image?id=1"),
        ("<img src='https://cdn.example.org/image?id=2'>", "https://cdn.example.org/image?id=2"),
        ("[img]https://cdn.example.org/image?id=3[/img]", "https://cdn.example.org/image?id=3"),
        ("https://example.org/readme", None),
        ("http://cdn.example.org/pic.png", None),
        ("https://127.0.0.1/internal.png", None),
        ("https://169.254.169.254/metadata.png", None),
        ("https://example.local/photo.png", None),
        ("https://cdn.example.org:8080/photo.png", None),
        ("file:///etc/passwd", None),
        ("![portrait](https://user:secret@example.org/picture.png)", None),
    ],
)
def test_first_image_url(message, expected):
    assert first_image_url(message) == expected


def test_media_migration_removes_only_orphan_receipts():
    import sqlite3
    from contextlib import closing

    from bridge.migrations import run_migrations
    from bridge.schema import SCHEMA_MIGRATIONS

    with closing(sqlite3.connect(":memory:")) as db:
        run_migrations(db, SCHEMA_MIGRATIONS[:-1])
        db.execute(
            "INSERT INTO messages(chat_id,session_id,role,content,created_at) VALUES('chat','s','assistant','Hello',1)"
        )
        rowid = db.execute("SELECT rowid FROM messages").fetchone()[0]
        db.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?)",
            [
                (f"greeting_photo:{rowid}", "81"),
                ("greeting_photo:9000", "82"),
                ("greeting_photo_url:9000", "https://images.example/orphan.png"),
                ("unrelated", "keep"),
            ],
        )
        db.commit()
        run_migrations(db, SCHEMA_MIGRATIONS)
        assert db.execute("SELECT value FROM meta WHERE key=?", (f"greeting_photo:{rowid}",)).fetchone() == ("81",)
        assert db.execute("SELECT value FROM meta WHERE key='unrelated'").fetchone() == ("keep",)
        assert db.execute("SELECT value FROM meta WHERE key='greeting_photo:9000'").fetchone() is None
        assert db.execute("SELECT value FROM meta WHERE key='greeting_photo_url:9000'").fetchone() is None
