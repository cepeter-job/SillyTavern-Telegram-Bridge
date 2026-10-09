"""Provider image HTTP failures must release owned response streams."""

from __future__ import annotations

import io
import urllib.error

import pytest
from settings_test_support import make_test_settings

from bridge import image_generation
from bridge.image_reference import ImageReference
from bridge.image_routing import ImageRoute
from bridge.provider_errors import ProviderRequestError


@pytest.fixture
def image_settings(tmp_path):
    catalog = tmp_path / "providers.yaml"
    catalog.write_text(
        "providers:\n"
        "  test-image:\n"
        "    name: Test Image\n"
        "    api_endpoint: https://images.example/v1\n"
        "    api_key_env: TEST_IMAGE_KEY\n"
        "    image_enabled: true\n"
        "    image_models: [test-model]\n",
        encoding="utf-8",
    )
    return make_test_settings(
        home=tmp_path,
        environ={"TEST_IMAGE_KEY": "test-key", "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example"},
        provider_config_file=catalog,
    )


@pytest.mark.parametrize("route_kind", ["generation", "edit"])
@pytest.mark.parametrize("http_status,expected", [(422, ValueError), (429, ProviderRequestError)])
def test_image_http_error_closes_original_stream(image_settings, monkeypatch, route_kind, http_status, expected):
    body = io.BytesIO(b'{"code":"prompt_too_long"}')
    errors = []

    def reject(request, timeout, *, environ=None):
        error = urllib.error.HTTPError(request.full_url, http_status, "private detail", {}, body)
        errors.append(error)
        raise error

    monkeypatch.setattr(image_generation, "strict_urlopen", reject)
    with pytest.raises(expected):
        if route_kind == "generation":
            image_generation.generate_image(
                "test-image::test-model", "portrait", "1024x1024", app_settings=image_settings
            )
        else:
            route = ImageRoute(
                selection="test-image::step-image-edit-2",
                provider_id="test-image",
                model="step-image-edit-2",
                transport="reference",
                edit_route="openai",
                spec={
                    "api_endpoint": "https://images.example/v1",
                    "api_key_env": "TEST_IMAGE_KEY",
                    "image_enabled": True,
                    "image_models": ["step-image-edit-2"],
                },
            )
            image_generation.edit_image(
                route,
                "portrait",
                ImageReference(b"PNG", "image/png", "Mira.png"),
                "1024x1024",
                app_settings=image_settings,
            )
    assert body.closed
    assert errors[0].fp is body
