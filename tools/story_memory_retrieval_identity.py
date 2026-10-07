"""Read-only source identity and explicitly bounded resource measurements."""

from __future__ import annotations

import importlib.metadata
import platform
import shutil
import sqlite3
import subprocess
from dataclasses import asdict
from pathlib import Path

from story_memory_retrieval_artifacts import json_hash
from story_memory_retrieval_fixture import require, sha256

ROOT = Path(__file__).resolve().parents[1]


def _git(*arguments):
    # Fixed read-only commands; paths come only from this repository's git inventory.
    result = subprocess.run(  # noqa: S603
        [shutil.which("git") or "/usr/bin/git", "-C", str(ROOT), *arguments],
        capture_output=True,
        check=True,
        timeout=10,
    )
    return result.stdout


def source_identity():
    paths = _git("ls-files", "--cached", "--others", "--exclude-standard", "-z").decode().split("\0")
    files = {}
    for name in sorted(set(paths)):
        path = ROOT / name
        if path.is_file() and (
            path.suffix == ".py"
            or name
            in {
                "requirements.txt",
                "requirements-dev.txt",
                "requirements.lock",
                "requirements-dev.lock",
                "pyproject.toml",
                "tests/fixtures/story_memory/retrieval_v1.json",
                "tests/fixtures/story_memory/retrieval_vectors_v1.json",
            }
        ):
            files[name] = sha256(path.read_bytes())
    return {
        "revision": _git("rev-parse", "HEAD").decode().strip(),
        "tree": _git("rev-parse", "HEAD^{tree}").decode().strip(),
        "dirty": bool(_git("status", "--porcelain", "--untracked-files=normal").strip()),
        "source_sha256": json_hash(files),
        "source_file_hashes": files,
    }


def runtime_identity():
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "aiohttp": importlib.metadata.version("aiohttp"),
        "installed_hindsight_sdk": importlib.metadata.version("hindsight-client"),
        "hindsight_sdk_used": False,
    }


def corpus_identity(corpus):
    cases = corpus.cases
    positive = [case for case in cases if case.relevant_fact_keys]
    counts = [len(corpus.eligible(case)) for case in cases]
    return {
        "fixture_sha256": corpus.fixture_sha256,
        "fact_count": len(corpus.facts),
        "query_count": len(cases),
        "distinct_summaries": len({stored.fact.summary for stored in corpus.facts.values()}),
        "distinct_query_texts": len({case.text for case in cases}),
        "positive_query_count": len(positive),
        "positive_relevance_annotations": sum(len(case.relevant_fact_keys) for case in positive),
        "macro_recall_at_1_ceiling": sum(1 / len(case.relevant_fact_keys) for case in positive) / len(positive),
        "no_answer_query_count": len(cases) - len(positive),
        "eligible_min": min(counts),
        "eligible_max": max(counts),
        "fact_bindings": corpus.fact_bindings(),
        "events": corpus.events,
        "queries": [
            {
                **asdict(case),
                "scope": asdict(corpus.scopes[case.query_id]),
                "eligible_fact_keys": list(corpus.eligible(case)),
            }
            for case in cases
        ],
        "branch": {
            "checkpoint_id": corpus.checkpoint.checkpoint_id,
            "through_rowid": corpus.checkpoint.through_rowid,
            "prefix_fact_count": corpus.branch_prefix_count,
            "local_memory_status": corpus.branch.memory_status,
            "main_closed": corpus.ending_result.completed,
            "epilogue_delivered": corpus.ending_result.delivered,
        },
        "derived_fact_sql_inserts": 0,
    }


def sqlite_footprint(db):
    databases = []
    for _, name, filename in db.execute("PRAGMA database_list"):
        require(name in {"main", "temp"}, "Unexpected attached study database")
        pages = db.execute(f"PRAGMA {name}.page_count").fetchone()[0]
        page_size = db.execute(f"PRAGMA {name}.page_size").fetchone()[0]
        free = db.execute(f"PRAGMA {name}.freelist_count").fetchone()[0]
        sidecars = {
            suffix or "database": Path(filename + suffix).stat().st_size
            for suffix in ("", "-wal", "-shm")
            if filename and Path(filename + suffix).is_file()
        }
        databases.append(
            {
                "database": name,
                "page_count": pages,
                "page_size": page_size,
                "page_bytes": pages * page_size,
                "free_page_bytes": free * page_size,
                "filesystem_bytes": sidecars,
            }
        )
    table = db.execute("SELECT name FROM sqlite_temp_master WHERE name='evaluation_vectors'").fetchone()
    vector_bytes = (
        db.execute("SELECT coalesce(sum(length(vector)),0) FROM evaluation_vectors").fetchone()[0] if table else 0
    )
    return {
        "databases": databases,
        "vector_payload_bytes": vector_bytes,
        "boundary": "SQLite logical pages and observed database/WAL/SHM files; not service RAM or RSS",
    }
