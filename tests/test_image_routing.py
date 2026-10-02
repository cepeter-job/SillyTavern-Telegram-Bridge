from __future__ import annotations

from pathlib import Path

import pytest
from settings_test_support import make_test_settings

from bridge.sqlite_store import db_connect


def _settings(tmp_path: Path, catalog_text: str):
    catalog = tmp_path / "providers.yaml"
    catalog.write_text(catalog_text, encoding="utf-8")
    return make_test_settings(
        home=tmp_path,
        db_file=tmp_path / "bridge.sqlite3",
        provider_config_file=catalog,
    )


def _legacy_catalog() -> str:
    return (
        "providers:\n"
        "  images:\n"
        "    api_endpoint: https://images.example/v1\n"
        "    image_enabled: true\n"
        "    image_models: [first, second]\n"
    )


def _auto_catalog(
    *,
    reference_target: str = "images::ref",
    reference_mode: str = "reference",
    text_target: str = "images::text",
) -> str:
    return (
        "image_auto:\n"
        f"  text_model: {text_target}\n"
        f"  reference_model: {reference_target}\n"
        "providers:\n"
        "  images:\n"
        "    api_endpoint: https://images.example/v1\n"
        "    image_enabled: true\n"
        "    image_models: [text, ref]\n"
        "    image_model_capabilities:\n"
        "      text:\n"
        "        mode: text\n"
        "      ref:\n"
        f"        mode: {reference_mode}\n"
        "        edit_route: openai\n"
    )


def test_legacy_image_catalog_without_capabilities_keeps_first_concrete_default(tmp_path):
    from bridge import image_routing

    settings = _settings(tmp_path, _legacy_catalog())
    db = db_connect(app_settings=settings)
    try:
        assert image_routing.session_image_settings(db, "chat", "session", app_settings=settings) == (
            "images::first",
            "1024x1024",
        )
        route = image_routing.resolve_image_route("images::first", reference_available=True, app_settings=settings)
        assert route.selection == "images::first"
        assert route.transport == "text"
    finally:
        db.close()


def test_valid_auto_defaults_to_auto_and_reference_target_when_reference_exists(tmp_path):
    from bridge import image_routing

    settings = _settings(tmp_path, _auto_catalog())
    db = db_connect(app_settings=settings)
    try:
        assert image_routing.session_image_settings(db, "chat", "session", app_settings=settings)[0] == "auto"
        route = image_routing.resolve_image_route("auto", reference_available=True, app_settings=settings)
        assert (route.selection, route.transport, route.edit_route) == ("images::ref", "reference", "openai")
    finally:
        db.close()


def test_auto_without_reference_resolves_only_configured_text_target(tmp_path):
    from bridge import image_routing

    settings = _settings(tmp_path, _auto_catalog())
    route = image_routing.resolve_image_route("auto", reference_available=False, app_settings=settings)
    assert (route.selection, route.transport) == ("images::text", "text")


def test_manual_text_selection_stays_text_with_reference_available(tmp_path):
    from bridge import image_routing

    settings = _settings(tmp_path, _auto_catalog())
    route = image_routing.resolve_image_route("images::text", reference_available=True, app_settings=settings)
    assert (route.selection, route.transport) == ("images::text", "text")


def test_manual_reference_selection_requires_reference(tmp_path):
    from bridge import image_routing

    settings = _settings(tmp_path, _auto_catalog())
    with pytest.raises(ValueError, match=r"requires.*reference"):
        image_routing.resolve_image_route("images::ref", reference_available=False, app_settings=settings)


def test_unqualified_auto_target_is_rejected_in_multi_provider_catalog(tmp_path):
    from bridge import image_routing

    catalog = _auto_catalog(text_target="text") + (
        "  other:\n    api_endpoint: https://other.example/v1\n    image_enabled: true\n    image_models: [text]\n"
    )
    settings = _settings(tmp_path, catalog)
    with pytest.raises(ValueError, match="fully-qualified"):
        image_routing.resolve_image_route("auto", reference_available=False, app_settings=settings)


def test_auto_reference_target_with_text_only_capability_fails_closed(tmp_path):
    from bridge import image_routing

    settings = _settings(tmp_path, _auto_catalog(reference_mode="text"))
    with pytest.raises(ValueError, match="reference"):
        image_routing.resolve_image_route("auto", reference_available=True, app_settings=settings)


def test_auto_text_target_for_undeclared_model_is_rejected(tmp_path):
    from bridge import image_routing

    settings = _settings(tmp_path, _auto_catalog(text_target="images::missing"))
    with pytest.raises(ValueError, match="declared"):
        image_routing.resolve_image_route("auto", reference_available=False, app_settings=settings)


def test_duplicate_model_ids_resolve_by_fully_qualified_provider_selection(tmp_path):
    from bridge import image_routing

    settings = _settings(
        tmp_path,
        (
            "providers:\n"
            "  first:\n"
            "    api_endpoint: https://first.example/v1\n"
            "    image_enabled: true\n"
            "    image_models: [same]\n"
            "  second:\n"
            "    api_endpoint: https://second.example/v1\n"
            "    image_enabled: true\n"
            "    image_models: [same]\n"
        ),
    )
    route = image_routing.resolve_image_route("second::same", reference_available=False, app_settings=settings)
    assert route.provider_id == "second"
    assert route.selection == "second::same"


def test_missing_auto_reference_target_falls_back_only_to_auto_text_target(tmp_path):
    from bridge import image_routing

    settings = _settings(tmp_path, _auto_catalog(reference_target="images::removed"))
    route = image_routing.resolve_image_route("auto", reference_available=True, app_settings=settings)
    assert (route.selection, route.transport) == ("images::text", "text")
