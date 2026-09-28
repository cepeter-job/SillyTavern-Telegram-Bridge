from __future__ import annotations

import urllib.error
from email.message import Message
from pathlib import Path

import pytest

from bridge.provider_port import ProviderPort


def test_provider_port_normalizes_http_rate_limit_without_raw_details():
    from bridge.provider_errors import ProviderRequestError

    error = urllib.error.HTTPError(
        "https://provider.example/private",
        429,
        "secret upstream body",
        Message(),
        None,
    )

    def fail(*_args, **_kwargs):
        raise error

    with pytest.raises(ProviderRequestError) as raised:
        ProviderPort(fail).generate("key", "provider::model", [])

    assert raised.value.kind == "rate_limit"
    assert raised.value.status == 429
    assert raised.value.model == "provider::model"
    assert "secret upstream body" not in str(raised.value)
    assert "provider.example" not in str(raised.value)


def test_provider_port_does_not_relabel_unexpected_runtime_error():
    from bridge.provider_errors import ProviderRequestError

    unexpected = RuntimeError("programming defect")

    def fail(*_args, **_kwargs):
        raise unexpected

    with pytest.raises(RuntimeError) as raised:
        ProviderPort(fail).generate("key", "provider::model", [])

    assert raised.value is unexpected
    assert not isinstance(raised.value, ProviderRequestError)


def test_presentation_error_formatter_has_no_transport_dependencies():
    source = (Path(__file__).parents[1] / "bridge" / "provider_user_errors.py").read_text(encoding="utf-8")
    assert "urllib" not in source
    assert "socket" not in source
