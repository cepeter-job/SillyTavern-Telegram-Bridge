"""Explicit, non-secret errors safe to present on the management surface."""

from __future__ import annotations


class MiniAppError(ValueError):
    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.status = status
        self.code = code
