"""Shared read-only source inspection helpers for architecture/boundary tests.

These helpers parse :mod:`bridge` source files with :mod:`ast` and :mod:`symtable`,
and read the GitHub Actions entry files as text. They never import application
modules and never mutate registry state, so any test module may use them without
disturbing the explicitly composed application graph.
"""

from __future__ import annotations

import ast
import symtable
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
BRIDGE = ROOT / "bridge"
WORKFLOWS = ROOT / ".github/workflows"


def workflow_paths() -> list[Path]:
    """Return every GitHub Actions entry file in stable file-name order."""
    return sorted(WORKFLOWS.glob("*.y*ml"))


def workflow_documents() -> dict[str, dict]:
    """Return parsed workflow documents keyed by file name."""
    return {path.name: yaml.safe_load(path.read_text(encoding="utf-8")) for path in workflow_paths()}


def workflow_text(name: str | None = None) -> str:
    """Return one workflow file, or every entry file concatenated in name order.

    A guard that protects a command rather than a file should read the
    concatenation, so moving a gate between files cannot drop the guard.
    """
    if name is not None:
        path = WORKFLOWS / name
        assert path.is_file(), f"missing workflow: {name}"
        return path.read_text(encoding="utf-8")
    return "\n".join(path.read_text(encoding="utf-8") for path in workflow_paths())


def workflow_jobs() -> dict[str, tuple[str, dict]]:
    """Map every declared job name to its workflow file and job definition."""
    jobs: dict[str, tuple[str, dict]] = {}
    for name, document in workflow_documents().items():
        for job_name, job in document.get("jobs", {}).items():
            assert job_name not in jobs, f"job {job_name} is declared in {jobs[job_name][0]} and {name}"
            jobs[job_name] = (name, job)
    return jobs


def job_text(job_name: str) -> str:
    """Return the full YAML text of one uniquely owned job.

    Guards read a job rather than a file so a workflow split or rename does not
    silently drop a gate, and a duplicated job name is rejected.
    """
    jobs = workflow_jobs()
    assert job_name in jobs, f"no workflow declares the job {job_name}"
    _, job = jobs[job_name]
    return yaml.safe_dump(job, sort_keys=False, width=4096, default_flow_style=False)


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


def referenced_globals(source: str, filename: str) -> set[str]:
    table = symtable.symtable(source, filename, "exec")
    result: set[str] = set()

    def walk(node):
        for symbol in node.get_symbols():
            if symbol.is_referenced() and symbol.is_global():
                result.add(symbol.get_name())
        for child in node.get_children():
            walk(child)

    walk(table)
    return result
