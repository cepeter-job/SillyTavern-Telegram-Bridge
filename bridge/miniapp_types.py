"""Transport-neutral values shared by Mini App route composition and handlers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from bridge.miniapp_auth import MiniAppIdentity

Handler = Callable[[Any, MiniAppIdentity, dict[str, Any]], Any]


@dataclass(frozen=True)
class ApiRoute:
    method: str
    path: str
    handler: Handler
    background_kind: str = ""


@dataclass(frozen=True)
class BinaryResult:
    content: bytes
    content_type: str
