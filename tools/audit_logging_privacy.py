"""Inventory production logging and reject preformatted/dynamic message expressions."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

_METHODS = frozenset({"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"})


def inventory_file(path: Path, root: Path) -> list[dict[str, object]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    aliases = {"logging"}
    direct: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            aliases.update(item.asname or item.name for item in node.names if item.name == "logging")
        elif isinstance(node, ast.ImportFrom) and node.module == "logging":
            direct.update({item.asname or item.name: item.name for item in node.names})
    # Resolve aliases of logging factories and instances, not only conventional names.
    logger_bindings: set[str] = set()
    changed = True
    while changed:
        previous = len(logger_bindings)
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
                targets, value = [node.target], node.value
            else:
                continue
            known = isinstance(value, ast.Name) and value.id in logger_bindings
            if isinstance(value, ast.Call):
                factory = value.func
                known |= isinstance(factory, ast.Name) and direct.get(factory.id) in {
                    "getLogger",
                    "LoggerAdapter",
                    "Logger",
                }
                known |= isinstance(factory, ast.Attribute) and (
                    (factory.attr in {"getLogger", "LoggerAdapter", "Logger"} and ast.unparse(factory.value) in aliases)
                    or (factory.attr == "getChild" and ast.unparse(factory.value) in logger_bindings)
                )
            if known:
                logger_bindings.update(ast.unparse(target) for target in targets)
        changed = len(logger_bindings) != previous
    scopes: dict[int, str] = {}

    def walk(node: ast.AST, scope: str = "") -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = node.name
        scopes[id(node)] = scope
        for child in ast.iter_child_nodes(node):
            walk(child, scope)

    walk(tree)
    rows: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        method, owner = "", ""
        if isinstance(node.func, ast.Attribute):
            method, owner = node.func.attr, ast.unparse(node.func.value)
        elif isinstance(node.func, ast.Name) and node.func.id in direct:
            method, owner = direct[node.func.id], "logging"
        elif (
            isinstance(node.func, ast.Call)
            and isinstance(node.func.func, ast.Name)
            and node.func.func.id == "getattr"
            and len(node.func.args) >= 2
        ):
            method, owner = "dynamic", ast.unparse(node.func.args[0])
        if method not in _METHODS | {"getLogger", "dynamic"}:
            continue
        normalized = owner.casefold().replace("_", "")
        logging_owner = (
            owner in aliases | logger_bindings or normalized.endswith(("logger", "log")) or "getLogger(" in owner
        )
        if method == "dynamic" and not logging_owner:
            continue
        index = 1 if method == "log" else 0
        message = (
            node.args[index]
            if len(node.args) > index
            else next((item.value for item in node.keywords if item.arg in {"msg", "message", "name"}), None)
        )
        constant = isinstance(message, ast.Constant) and isinstance(message.value, str)
        logger_name = method == "getLogger" and (
            message is None
            or (isinstance(message, ast.Constant) and message.value is None)
            or (isinstance(message, ast.Name) and message.id == "__name__")
        )
        checked_event = (
            path.relative_to(root).as_posix() == "diagnostic_events.py"
            and scopes[id(node)] == "event"
            and method == "log"
            and owner == "_LOG"
        )
        rows.append(
            {
                "file": path.relative_to(root).as_posix(),
                "line": node.lineno,
                "owner": owner[:120],
                "method": method,
                "scope": scopes[id(node)],
                "classification": "logging" if logging_owner else "nonlogging_candidate",
                "message_kind": (
                    "literal"
                    if constant
                    else "checked_event"
                    if checked_event
                    else "logger_name"
                    if logger_name
                    else "dynamic"
                ),
                "expression": ast.unparse(message)[:160] if message is not None else "",
                "unsafe": logging_owner and not (constant or logger_name or checked_event),
            }
        )
    return rows


def inventory(root: Path) -> dict[str, object]:
    paths = sorted(root.rglob("*.py"))
    rows = [row for path in paths for row in inventory_file(path, root)]
    return {"files": len(paths), "calls": rows, "unsafe": [row for row in rows if row["unsafe"]]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "bridge")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.root.is_dir():
        parser.error("Production source root does not exist")
    report = inventory(args.root)
    rendered = json.dumps(report, indent=2, ensure_ascii=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "files": report["files"],
                "logging_candidates": len(report["calls"]),
                "unsafe": report["unsafe"],
                "nonlogging_candidates": [row for row in report["calls"] if row["classification"] != "logging"],
            },
            indent=2,
        )
    )
    return int(bool(report["unsafe"]))


if __name__ == "__main__":
    raise SystemExit(main())
