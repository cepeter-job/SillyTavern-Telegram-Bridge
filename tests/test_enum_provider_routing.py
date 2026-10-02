"""Pin enum/provider route vocabulary and first-match behavior before extraction."""

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
EXPECTED = {
    "enum_callbacks": (
        "handle_enum_callback",
        [
            ("exact", "enum:close"),
            ("exact", "enum:stscript:cancel"),
            ("exact", "enum:stscript:reset"),
            ("prefix", "enum:settings:input:"),
            ("exact", "enum:settings:reset"),
            ("prefix", "enum:settings:reasoning:"),
            ("prefix", "enum:humanizer:"),
            ("prefix", "enum:grounded:"),
            ("prefix", "enum:stream:"),
            ("prefix", "enum:voice:"),
            ("exact", "enum:stt:language"),
            ("exact", "enum:stt:language_input"),
            ("prefix", "enum:sttlanguagepage:"),
            ("prefix", "enum:sttlanguage:"),
            ("exact", "enum:stt:model"),
            ("exact", "enum:stt:back"),
            ("prefix", "enum:stt:"),
            ("prefix", "enum:sttmodel:"),
            ("exact", "enum:memory:search"),
            ("exact", "enum:memory:scope"),
            ("exact", "enum:memory:back"),
            ("prefix", "enum:memory:"),
            ("prefix", "enum:memoryscope:"),
            ("exact", "enum:preset:save"),
            ("exact", "enum:preset:back"),
            ("exact", "enum:presetdelete"),
            ("prefix", "enum:presetpage:"),
            ("prefix", "enum:presetdeletepage:"),
            ("prefix", "enum:presetuse:"),
            ("prefix", "enum:presetdelconfirm:"),
            ("prefix", "enum:presetdel:"),
            ("exact", "enum:rag:search"),
            ("exact", "enum:rag:versions"),
            ("prefix", "enum:ragversionspage:"),
            ("prefix", "enum:ragversions:"),
            ("prefix", "enum:ragactivate:"),
            ("exact", "enum:rag:remove"),
            ("exact", "enum:rag:back"),
            ("prefix", "enum:ragremovepage:"),
            ("prefix", "enum:ragpage:"),
            ("prefix", "enum:ragremoveconfirm:"),
            ("prefix", "enum:ragremove:"),
            ("exact", "enum:rag:reindex"),
            ("prefix", "enum:rag:"),
        ],
    ),
    "provider_callbacks": (
        "handle_provider_model_callback",
        [
            ("prefix", "models:providers:"),
            ("prefix", "models:model:"),
            ("exact", "models:target"),
            ("exact", "models:cancel"),
            ("exact", "models:story-reasoning"),
            ("prefix", "storyreasoning:"),
            ("exact", "models:utility-reasoning"),
            ("prefix", "utilityreasoning:"),
            ("exact", "models:back"),
            ("exact", "provider:health"),
            ("exact", "provider:refresh"),
            ("exact", "provider:back"),
            ("prefix", "provider:health-page:"),
            ("prefix", "provider:maint:"),
            ("prefix", "provider:"),
            ("prefix", "unsupported:"),
            ("prefix", "modeltarget:"),
            ("prefix", "model:"),
        ],
    ),
}


def arguments(name):
    if name == "enum_callbacks":
        return (
            (object(), "token", "chat", {}, "", {}),
            {
                "input_flow_service": object(),
                "request_context": object(),
                "rag_service": object(),
            },
            4,
        )
    return (
        (object(), "token", {}, lambda *a: None, "", "chat", {}, {}, "session", 1),
        {
            "request_context": object(),
            "provider_policy": None,
            "provider_probes": None,
        },
        4,
    )


@pytest.mark.parametrize("name", EXPECTED)
def test_enum_provider_uses_bounded_dispatch_and_narrow_actions(name):
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
def test_enum_provider_dispatches_each_known_route_once(name, monkeypatch):
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
    assert result is None if name == "enum_callbacks" else result is False
    assert calls == []


def provider_call(monkeypatch, data):
    from bridge import provider_callbacks

    feedback = []
    messages = []
    closed = []
    monkeypatch.setattr(provider_callbacks, "send_text", lambda *a: messages.append(a))
    monkeypatch.setattr(provider_callbacks, "remove_inline_keyboard", lambda *a: closed.append(a))
    handled = provider_callbacks.handle_provider_model_callback(
        "db",
        "token",
        {"id": "callback"},
        lambda *a: feedback.append(a),
        data,
        "chat",
        {"message_id": 91},
        {},
        "session",
        7,
        request_context=object(),
    )
    return handled, feedback, messages, closed


def test_model_cancel_removes_keyboard_without_generating(monkeypatch):
    handled, feedback, messages, closed = provider_call(monkeypatch, "models:cancel")
    assert handled is True
    assert feedback == [("token", "callback", "Cancelled")]
    assert closed == [("db", "token", {"id": "callback"})]
    assert messages == []


def test_catalog_only_provider_reports_limitation_without_selection(monkeypatch):
    from bridge import provider_callbacks

    monkeypatch.setattr(provider_callbacks, "resolve_dynamic_callback_token", lambda *a, **kw: "catalog-provider")
    monkeypatch.setattr(
        provider_callbacks, "update_session", lambda *a, **kw: pytest.fail("Catalog entry changed selection")
    )
    handled, feedback, messages, closed = provider_call(monkeypatch, "unsupported:opaque-token")
    assert handled is True and closed == []
    assert feedback == [("token", "callback", "Catalog only: adapter not enabled")]
    assert messages == [
        (
            "token",
            "chat",
            "Provider 'catalog-provider' is visible in the bridge catalog, but its adapter is not enabled yet.",
        )
    ]


@pytest.mark.parametrize("data", ("storyreasoning:not-a-level", "utilityreasoning:not-a-level"))
def test_invalid_reasoning_selection_does_not_write_settings(monkeypatch, data):
    from bridge import provider_callbacks

    for name in ("set_meta", "update_generation_settings", "set_utility_reasoning"):
        monkeypatch.setattr(
            provider_callbacks, name, lambda *a, **kw: pytest.fail("Invalid reasoning selection wrote state")
        )
    handled, feedback, messages, closed = provider_call(monkeypatch, data)
    assert handled is True
    assert feedback == [("token", "callback", "Reasoning choice invalid")]
    assert messages == closed == []
