"""Runtime entrypoint and composition invariants."""

from __future__ import annotations

import ast
import builtins
import sqlite3
import subprocess
import symtable
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest
from settings_test_support import SettingsTestCase

REPO_ROOT = Path(__file__).parents[1]
BRIDGE_DIR = REPO_ROOT / "bridge"


def _bound_names(source: str) -> set[str]:
    tree = ast.parse(source)
    names: set[str] = set()

    def bind(node):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for child in node.elts:
                bind(child)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                bind(target)
    return names


def _defined_names(source: str) -> set[str]:
    tree = ast.parse(source)
    result: set[str] = set()

    def bind(node):
        if isinstance(node, ast.Name):
            result.add(node.id)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for child in node.elts:
                bind(child)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                bind(target)
    return result


def _referenced_globals(source: str, filename: str) -> set[str]:
    table = symtable.symtable(source, filename, "exec")
    result: set[str] = set()

    def walk(node):
        for symbol in node.get_symbols():
            if symbol.is_referenced() and symbol.is_global():
                result.add(symbol.get_name())
        for child in node.get_children():
            walk(child)

    walk(table)
    return result


def _owner_index() -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for path in sorted(BRIDGE_DIR.glob("*.py")):
        if path.name in {"main.py", "runtime.py"}:
            continue
        source = path.read_text(encoding="utf-8")
        for name in _defined_names(source):
            owners.setdefault(name, []).append(path.name)
    return owners


class RuntimeEntrypointInvariantTests(SettingsTestCase):
    def _run_python(self, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-c", source],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_main_import_is_ordinary_and_does_not_import_runtime_or_loader(self):
        completed = self._run_python(
            "import sys\n"
            "import bridge.main\n"
            "assert 'bridge.runtime' not in sys.modules\n"
            "assert 'bridge.runtime_loader' not in sys.modules\n"
        )
        self.assertEqual(
            completed.returncode,
            0,
            completed.stdout + completed.stderr,
        )

    def test_both_entrypoints_use_one_runtime_environment_bootstrap(self):
        launcher = (REPO_ROOT / "sillytavern_telegram_bridge.py").read_text(encoding="utf-8")
        main_source = (BRIDGE_DIR / "main.py").read_text(encoding="utf-8")
        self.assertIn("from bridge.main import main", launcher)
        self.assertNotIn("bootstrap_environment()", launcher)
        tree = ast.parse(main_source)
        startup = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_main")
        calls = {
            node.func.id: node.lineno
            for node in ast.walk(startup)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertLess(calls["bootstrap_environment"], calls["_load_startup_config"])
        self.assertLess(calls["_load_startup_config"], calls["_build_startup_services"])
        self.assertNotIn("def bootstrap_env(", launcher)

    def test_entrypoint_imports_main_directly(self):
        source = (REPO_ROOT / "sillytavern_telegram_bridge.py").read_text(encoding="utf-8")
        self.assertIn("from bridge.main import main", source)
        self.assertNotIn("from bridge.runtime import main", source)

    def test_no_production_exec_calls_remain(self):
        offenders = []
        for path in sorted(BRIDGE_DIR.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "exec":
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_main_has_no_implicit_shared_runtime_globals(self):
        path = BRIDGE_DIR / "main.py"
        source = path.read_text(encoding="utf-8")
        bound = _bound_names(source)
        unresolved = sorted(
            _referenced_globals(source, "main.py")
            - bound
            - set(dir(builtins))
            - {"__file__", "__name__", "__package__"}
        )
        owners = _owner_index()
        detail = [name + (" <- " + ",".join(owners[name]) if owners.get(name) else "") for name in unresolved]
        self.assertEqual(detail, [], "\n".join(detail))

    def test_main_has_no_transitional_dependency_composition(self):
        source = (BRIDGE_DIR / "main.py").read_text(encoding="utf-8")
        self.assertFalse((BRIDGE_DIR / "ordinary_dependencies.py").exists())
        self.assertNotIn("ordinary_dependencies", source)
        self.assertNotIn("complete_application_dependencies", source)


if __name__ == "__main__":
    unittest.main()


def _exercise_runtime_memory_diagnostics(
    monkeypatch,
    tmp_path,
    diagnostics_type,
    injected=None,
    events=None,
    background_submit=None,
):
    import bridge.runtime_lifecycle as lifecycle
    from bridge.settings import load_app_settings

    events = [] if events is None else events
    database = sqlite3.connect(":memory:")
    database.execute("CREATE TABLE light_novel_choice_sets(generation_status TEXT, lease_token TEXT, lease_until REAL)")

    def request(_token, method, _payload=None):
        if method == "getUpdates":
            events.append("poll")
            lifecycle._SHUTDOWN_EVENT.set()
            return []
        return {}

    config = load_app_settings({}, home=tmp_path)
    services = SimpleNamespace(
        config=config,
        db_factory=lambda: database,
        telegram=SimpleNamespace(request=request),
        background=SimpleNamespace(
            begin_shutdown=lambda: None,
            register_backlog_dispatcher=lambda _callback: None,
            submit=background_submit or (lambda *_args, **_kwargs: True),
        ),
        jobs=SimpleNamespace(recover=lambda *_args, **_kwargs: None),
        sync=object(),
        health=None,
        memory_diagnostics=injected,
    )
    monkeypatch.setattr(lifecycle, "MemoryDiagnostics", diagnostics_type(events), raising=False)
    monkeypatch.setattr(lifecycle, "_queue_ending_recovery", lambda *_args: 0)
    monkeypatch.setattr(lifecycle, "install_bridge_signal_handlers", lambda *_args: None)
    monkeypatch.setattr(lifecycle, "start_live_sync_worker", lambda **_kwargs: None)
    monkeypatch.setattr(lifecycle, "stop_live_sync_worker", lambda **_kwargs: True)
    monkeypatch.setattr(lifecycle, "shutdown_background_executors", lambda **_kwargs: True)
    monkeypatch.setattr(lifecycle, "run_database_maintenance", lambda **_kwargs: None)
    monkeypatch.setattr(lifecycle, "get_meta", lambda *_args, **_kwargs: "0")
    monkeypatch.setattr(lifecycle, "pending_update_ack_path", lambda _config: tmp_path / "no-pending")
    monkeypatch.setattr(lifecycle, "make_durable_backlog_dispatcher", lambda *_args, **_kwargs: lambda: None)

    try:
        assert lifecycle.run_bridge_runtime(services, {}) == 0
    finally:
        lifecycle._SHUTDOWN_EVENT.clear()
    return events


def test_runtime_schedules_nonblocking_metadata_refresh_before_first_poll(monkeypatch, tmp_path):
    import bridge.provider_discovery as discovery

    events = []
    submitted = []

    def submit(label, function, *args, **kwargs):
        events.append("submit")
        submitted.append((label, function, args, kwargs))
        return True

    class Recorder:
        def start(self):
            return True

        def stop(self, timeout=1.0):
            return True

    def diagnostics_type(_events):
        return Recorder

    result = _exercise_runtime_memory_diagnostics(
        monkeypatch,
        tmp_path,
        diagnostics_type,
        injected=Recorder(),
        events=events,
        background_submit=submit,
    )

    assert result is events
    assert events.index("submit") < events.index("poll")
    assert len(submitted) == 1
    label, function, args, kwargs = submitted[0]
    assert label == "provider_catalog_refresh"
    assert function is discovery.refresh_model_catalog
    assert args == ()
    assert kwargs["metadata_only"] is True

    monkeypatch.setattr(
        discovery,
        "strict_urlopen",
        lambda *a, **kw: pytest.fail("legacy config attempted provider metadata network I/O"),
    )
    config, refreshed, failed = function(*args, **kwargs)
    assert config == {"providers": {}}
    assert (refreshed, failed) == (0, 0)


def test_runtime_memory_diagnostics_start_before_poll_and_stop_after(monkeypatch, tmp_path):
    events = []

    class Recorder:
        def start(self):
            events.append("start")
            return True

        def stop(self, timeout=1.0):
            events.append(("stop", timeout))
            return True

    def diagnostics_type(_events):
        class Forbidden:
            def __init__(self, *_args, **_kwargs):
                raise AssertionError("runtime must not construct a second MemoryDiagnostics")

        return Forbidden

    result = _exercise_runtime_memory_diagnostics(
        monkeypatch,
        tmp_path,
        diagnostics_type,
        injected=Recorder(),
        events=events,
    )

    assert result is events
    assert events.count("start") == 1
    assert events.count("poll") == 1
    assert events.count(("stop", 1.0)) == 1
    assert events.index("start") < events.index("poll") < events.index(("stop", 1.0))


def test_runtime_memory_diagnostics_start_stop_errors_do_not_escape(monkeypatch, tmp_path):
    events = []

    class Recorder:
        def start(self):
            events.append("start")
            raise RuntimeError("PRIVATE_START_DETAIL")

        def stop(self, timeout=1.0):
            events.append(("stop", timeout))
            raise RuntimeError("PRIVATE_STOP_DETAIL")

    def diagnostics_type(_events):
        class Forbidden:
            def __init__(self, *_args, **_kwargs):
                raise AssertionError("runtime must not construct a second MemoryDiagnostics")

        return Forbidden

    result = _exercise_runtime_memory_diagnostics(
        monkeypatch,
        tmp_path,
        diagnostics_type,
        injected=Recorder(),
        events=events,
    )

    assert result is events
    assert "poll" in events
    assert ("stop", 1.0) in events


def test_runtime_reuses_injected_memory_diagnostics_without_constructing(monkeypatch, tmp_path):
    events = []

    class Recorder:
        def start(self):
            events.append("start")
            return True

        def stop(self, timeout=1.0):
            events.append(("stop", timeout))
            return True

    def forbidden_type(_events):
        class Forbidden:
            def __init__(self, *_args, **_kwargs):
                raise AssertionError("runtime must not construct a second MemoryDiagnostics")

        return Forbidden

    injected = Recorder()
    result = _exercise_runtime_memory_diagnostics(
        monkeypatch,
        tmp_path,
        forbidden_type,
        injected=injected,
        events=events,
    )

    assert result is events
    assert events.count("start") == 1
    assert events.count("poll") == 1
    assert events.count(("stop", 1.0)) == 1
    assert events.index("start") < events.index("poll") < events.index(("stop", 1.0))
