"""Read-only reference evidence, including installer and isolated-worker entrypoints.

Run with python -m tools.audit_module_references. An absent reference is a review
signal, never proof of dead code. Literal paths may be descriptive rather than
executed; callback registries and nonliteral dynamic dispatch still need review.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

from tools.static_analysis import _absolute_import_targets, _discover_modules


def _sources(root: Path) -> list[Path]:
    paths = list(root.glob("*.py")) + list(root.glob("*.sh")) + list(root.glob("*.service"))
    for directory in ("bridge", "tests", "tools", "config", ".github/workflows"):
        base = root / directory
        for suffix in ("*.py", "*.sh", "*.service", "*.yml", "*.yaml"):
            paths.extend(base.rglob(suffix))
    return sorted(set(path for path in paths if "__pycache__" not in path.parts and "node_modules" not in path.parts))


def collect_references(root: Path) -> dict[str, list[str]]:
    modules = _discover_modules(root / "bridge")
    references: dict[str, list[str]] = {name: [] for name in sorted(modules)}
    by_path = {path.relative_to(root).as_posix(): name for name, path in modules.items()}
    by_filename = {path.name: name for name, path in modules.items()}

    def add(target: str, source: str, line: int, kind: str) -> None:
        if target in references and by_path.get(source) != target:
            evidence = f"{kind}:{source}:{line}"
            if evidence not in references[target]:
                references[target].append(evidence)

    for path in _sources(root):
        source = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            tree = ast.parse(text, filename=source)
            current = by_path.get(source, source.removesuffix(".py").replace("/", "."))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for target in _absolute_import_targets(node, current_module=current, package_name="bridge"):
                        add(target, source, node.lineno, "import")
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    # Deliberately label weaker evidence instead of pretending literals prove execution.
                    value = node.value
                    target = by_path.get(value) or by_filename.get(value)
                    if target:
                        add(target, source, node.lineno, "literal-path")
                    if value in references:
                        add(value, source, node.lineno, "literal-module")
        for number, line in enumerate(text.splitlines(), 1):
            for match in re.finditer(r"(?:^|\s)-m\s+[\"']?(bridge(?:\.[A-Za-z_][A-Za-z_0-9]*)+)", line):
                add(match.group(1), source, number, "module-cli")
            for match in re.finditer(r"\b(bridge/[A-Za-z_0-9/]+\.py)\b", line):
                target = by_path.get(match.group(1))
                if target:
                    add(target, source, number, "path-reference")
    return references


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, help="Optional JSON evidence report")
    args = parser.parse_args()
    references = collect_references(args.root.resolve())
    candidates = [name for name, evidence in references.items() if not evidence]
    report = {
        "limitations": "No-reference candidates are not dead-code verdicts; inspect callback/dynamic use before deletion.",
        "modules": references,
        "review_candidates": candidates,
    }
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Reference audit: {len(references)} modules; {len(candidates)} require additional reference review")
    for name in candidates:
        print(f"  REVIEW, NOT DELETE: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
