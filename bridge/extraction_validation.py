"""Attach fixed schema metadata to parser errors without retaining rejected values."""

from __future__ import annotations

import json
from typing import TypedDict

from bridge.diagnostic_events import VALIDATION_CODES, VALIDATION_FIELDS


class ValidationDiagnostics(TypedDict, total=False):
    validation_field: str
    validation_code: str


class ExtractionValidationError(ValueError):
    """Keep legacy rejection classification while attaching only fixed diagnostic metadata."""

    def __init__(self, message: str, field: str, code: str) -> None:
        super().__init__(message)
        self.validation_field = field if field in VALIDATION_FIELDS else ""
        self.validation_code = code if code in VALIDATION_CODES else ""


def validation_diagnostics(error: Exception) -> ValidationDiagnostics:
    """Only use at the parser boundary; provider exceptions are not parser evidence."""
    if isinstance(error, json.JSONDecodeError):
        return {"validation_field": "output", "validation_code": "malformed_json"}
    if (
        isinstance(error, ExtractionValidationError)
        and error.validation_field in VALIDATION_FIELDS
        and error.validation_code in VALIDATION_CODES
    ):
        return {"validation_field": error.validation_field, "validation_code": error.validation_code}
    return {}
