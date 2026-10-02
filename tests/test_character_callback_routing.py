"""Character families have peer owners and explicit first-match routing."""

import ast
import importlib
from pathlib import Path

import pytest
from source_test_support import imported_modules

ROOT = Path(__file__).parents[1]
ROUTES = {
    "character_callbacks": (
        "handle_character_callback",
        {
            "character:protected",
            "character:menu",
            "character:info",
            "character:delete",
            "character:upload",
            "character:restore",
        },
        (
            "characterinfo:",
            "characterrestore:",
            "characterrestoreconfirm:",
            "characterdelete:",
            "characterdeleteconfirm:",
            "character:",
        ),
    ),
    "character_optimizer_callbacks": (
        "handle_character_optimizer_callback",
        {"character:optimize"},
        ("characteroptimizerefine:", "characteroptimizeauto:", "characteroptimizemanual:", "characteroptimize:"),
    ),
    "character_proposal_callbacks": (
        "handle_character_proposal_callback",
        set(),
        ("characterupload:", "characteroptimizeapply:", "characteroptimizecancel:", "characteroptimizepreview:"),
    ),
}


def owner(name):
    assert (ROOT / "bridge" / (name + ".py")).is_file(), "Missing canonical callback owner: " + name
    return importlib.import_module("bridge." + name)


def bound_routes(name, module):
    kwargs = {"provider_port": object(), "request_context": object()}
    if name == "character_callbacks":
        kwargs["group_service"] = object()
    return module._bind_routes(object(), "token", {}, lambda *a: None, "", "chat", {}, {}, "session", 1, **kwargs)


@pytest.mark.parametrize("name", ROUTES)
def test_character_family_has_small_explicit_dispatcher(name):
    module = owner(name)
    entry, expected_exact, expected_prefixes = ROUTES[name]
    exact, prefixes = bound_routes(name, module)
    assert set(exact) == expected_exact
    assert tuple(prefix for prefix, _handler in prefixes) == expected_prefixes
    tree = ast.parse((ROOT / "bridge" / (name + ".py")).read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == entry)
    assert function.end_lineno - function.lineno + 1 <= 80
    imports = imported_modules(name + ".py")
    assert not imports.intersection({"bridge." + other for other in ROUTES if other != name})
    assert "bridge.composition" not in imports


@pytest.mark.parametrize("name", ROUTES)
def test_each_character_route_dispatches_once_and_unknown_does_not(name, monkeypatch):
    module = owner(name)
    entry, _expected_exact, _expected_prefixes = ROUTES[name]
    exact, prefixes = bound_routes(name, module)
    called = []

    def target(route):
        return next(name for name in route.__code__.co_names if name.startswith("_handle_"))

    def spy(label):
        def handle(*args, **kwargs):
            called.append(label)
            return True

        return handle

    cases = [(key, target(route)) for key, route in exact.items()]
    cases += [(prefix + "value", target(route)) for prefix, route in prefixes]
    for _data, function_name in cases:
        monkeypatch.setattr(module, function_name, spy(function_name))
    kwargs = {"provider_port": object(), "request_context": object()}
    if name == "character_callbacks":
        kwargs["group_service"] = object()
    for data, expected in cases:
        called.clear()
        assert getattr(module, entry)(
            object(), "token", {}, lambda *a: None, data, "chat", {}, {}, "session", 1, **kwargs
        )
        assert called == [expected]
    called.clear()
    assert not getattr(module, entry)(
        object(), "token", {}, lambda *a: None, "unrelated:token", "chat", {}, {}, "session", 1, **kwargs
    )
    assert called == []


def test_root_router_orders_specific_owners_before_general_character():
    source = (ROOT / "bridge/panel_callback_routes.py").read_text()
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "handle_entity_panel_callback")
    calls = [n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "handle_character_optimizer_callback" in calls
    assert "handle_character_proposal_callback" in calls
    assert calls.index("handle_character_optimizer_callback") < calls.index("handle_character_callback")
    assert calls.index("handle_character_proposal_callback") < calls.index("handle_character_callback")


def test_extracted_operations_accept_only_used_collaborators():
    for name in ROUTES:
        tree = ast.parse((ROOT / "bridge" / (name + ".py")).read_text())
        for function in tree.body:
            if not isinstance(function, ast.FunctionDef) or not function.name.startswith("_handle_"):
                continue
            used = {n.id for n in ast.walk(function) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            parameters = {arg.arg for arg in (*function.args.args, *function.args.kwonlyargs)}
            assert parameters <= used, (name, function.name, parameters - used)
