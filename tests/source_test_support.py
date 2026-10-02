"""Shared read-only source inspection helpers for architecture/boundary tests.

These helpers parse :mod:`bridge` source files with :mod:`ast` only. They never
import application modules and never mutate registry state, so any test module
may use them without disturbing the explicitly composed application graph.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
BRIDGE = ROOT / "bridge"


def module_path(source: str | Path) -> Path:
    """Return the path of a ``bridge`` source file by name or by explicit path."""
    return BRIDGE / source if isinstance(source, str) else Path(source)


def parse_module(source: str | Path) -> ast.Module:
    """Parse a ``bridge`` source file without importing it."""
    return ast.parse(module_path(source).read_text(encoding="utf-8"))


def imported_modules(source: str | Path) -> set[str]:
    """Return every module name imported anywhere in a ``bridge`` source file."""
    result: set[str] = set()
    for node in ast.walk(parse_module(source)):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module)
    return result


def top_level_functions(source: str | Path) -> set[str]:
    """Return every top-level function name defined in a ``bridge`` source file."""
    return {
        node.name for node in parse_module(source).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
