"""Numeric loopback routing is prepared before work and remains stable during it."""

import os
from dataclasses import replace

import pytest
from settings_test_support import make_test_settings


def test_startup_proxy_preparation_preserves_entries_and_client_use_does_not_repeat_it(monkeypatch):
    from bridge import hindsight_endpoint, memory_backend

    monkeypatch.setenv("NO_PROXY", "example.test,127.0.0.1")
    monkeypatch.setenv("no_proxy", "other.test,example.test")
    settings = replace(make_test_settings(), environ={"HINDSIGHT_API_URL": "http://127.0.0.2:8888"})
    assert hindsight_endpoint.prepare_hindsight_endpoint(settings) == ("http://127.0.0.2:8888", "127.0.0.2")
    expected = "example.test,127.0.0.1,other.test,127.0.0.2,::1,[::1]"
    assert os.environ["NO_PROXY"] == os.environ["no_proxy"] == expected

    def repeated_preparation(_host):
        pytest.fail("Request path reparsed proxy configuration after startup preparation")

    monkeypatch.setattr(hindsight_endpoint, "_ensure_hindsight_loopback_proxy_bypass", repeated_preparation)
    for _ in range(3):
        with memory_backend.hindsight_client_scope(app_settings=settings):
            pass
    assert os.environ["NO_PROXY"] == os.environ["no_proxy"] == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8888",
        "http://192.168.1.1",
        "http://127.0.0.1/path",
        "http://user@127.0.0.1",
        "https://example.test",
    ],
)
def test_preparation_keeps_numeric_loopback_origin_boundary(url):
    from bridge.hindsight_endpoint import prepare_hindsight_endpoint

    with pytest.raises(ValueError, match="numeric loopback"):
        prepare_hindsight_endpoint(replace(make_test_settings(), environ={"HINDSIGHT_API_URL": url}))
