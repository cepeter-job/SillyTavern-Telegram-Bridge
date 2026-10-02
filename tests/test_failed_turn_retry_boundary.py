"""Retry callback leaves must use explicit collaborators, never the root container."""

import inspect

from source_test_support import imported_modules


def test_retry_callback_does_not_import_application_container():
    assert "bridge.composition" not in imported_modules("failed_turn_retry_callbacks.py")


def test_retry_callback_declares_narrow_required_collaborators():
    from bridge.failed_turn_retry_callbacks import handle_failed_turn_retry_callback

    parameters = inspect.signature(handle_failed_turn_retry_callback).parameters
    assert "services" not in parameters
    for name in ("jobs", "telegram_request", "request_context"):
        assert name in parameters
        assert parameters[name].default is inspect.Parameter.empty
