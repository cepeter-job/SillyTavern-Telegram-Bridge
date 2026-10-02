"""Pin persona/session route vocabulary and first-match behavior before extraction."""

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
EXPECTED = {
    "persona_callbacks": (
        "handle_persona_callback",
        [
            ("prefix", "persona:delete_page:"),
            ("prefix", "persona:delete:"),
            ("prefix", "personadeleteconfirm:"),
            ("prefix", "persona:"),
        ],
    ),
    "session_callbacks": (
        "handle_session_callback",
        [
            ("exact", "session:protected"),
            ("prefix", "sessiondeleteconfirm:"),
            ("prefix", "sessiondelete:"),
            ("prefix", "session:"),
        ],
    ),
}


def arguments(name):
    kwargs = {"request_context": object()}
    if name == "persona_callbacks":
        kwargs["persona_service"] = object()
    else:
        kwargs.update(group_service=object(), memory_service=object())
    return (object(), "token", {}, lambda *a: None, "", "chat", {}, {}, "session", 1), kwargs, 4


@pytest.mark.parametrize("name", EXPECTED)
def test_persona_session_uses_bounded_dispatch_and_narrow_actions(name):
    module = importlib.import_module("bridge." + name)
    assert hasattr(module, "_bind_routes")
    args, kwargs, _ = arguments(name)
    exact, prefixes = module._bind_routes(*args, **kwargs)
    entry, routes = EXPECTED[name]
    assert set(exact) == {key for kind, key in routes if kind == "exact"}
    assert tuple(key for key, _ in prefixes) == tuple(key for kind, key in routes if kind == "prefix")
    tree = ast.parse((ROOT / "bridge" / (name + ".py")).read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == entry)
    assert fn.end_lineno - fn.lineno + 1 <= 90
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef) or not fn.name.startswith("_handle_"):
            continue
        used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        assert {a.arg for a in (*fn.args.args, *fn.args.kwonlyargs)} <= used


@pytest.mark.parametrize("name", EXPECTED)
def test_persona_session_dispatches_each_known_route_once(name, monkeypatch):
    module = importlib.import_module("bridge." + name)
    assert hasattr(module, "_bind_routes")
    args, kwargs, data_index = arguments(name)
    exact, prefixes = module._bind_routes(*args, **kwargs)
    calls = []

    def target(route):
        return next(name for name in route.__code__.co_names if name.startswith("_handle_"))

    def spy(label):
        def run(*args, **kwargs):
            calls.append(label)
            return True

        return run

    cases = [(key, target(route)) for key, route in exact.items()]
    cases += [(prefix + "value", target(route)) for prefix, route in prefixes]
    for _, name_to_patch in cases:
        monkeypatch.setattr(module, name_to_patch, spy(name_to_patch))
    for data, expected in cases:
        calls.clear()
        values = list(args)
        values[data_index] = data
        getattr(module, EXPECTED[name][0])(*values, **kwargs)
        assert calls == [expected]
    calls.clear()
    values = list(args)
    values[data_index] = "unrelated:unknown"
    result = getattr(module, EXPECTED[name][0])(*values, **kwargs)
    assert result is False
    assert calls == []
