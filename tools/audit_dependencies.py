"""Audit every locked dependency set independently and retain complete scan evidence."""

from __future__ import annotations

import argparse
import json
import math
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_scan(scan: tuple[str, str, list[str]], output_dir: Path, timeout: float) -> dict[str, object]:
    name, target, command = scan
    report = output_dir / f"{name}.txt"
    result: dict[str, object] = {
        "id": name,
        "target": target,
        "status": "error",
        "exit_code": None,
        "report": report.name,
        "error": None,
    }
    print(f"Auditing {target}", flush=True)
    with report.open("w", encoding="utf-8") as stream:
        stream.write(f"$ {shlex.join(command)}\n")
        stream.flush()
        header_size = report.stat().st_size
        try:
            completed = subprocess.run(  # noqa: S603 -- fixed scanner argument arrays, without a shell
                command,
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout,
            )
            result["exit_code"] = completed.returncode
            result["status"] = "success" if completed.returncode == 0 else "failure"
            if completed.returncode == 0 and report.stat().st_size == header_size:
                result.update(status="error", error="Scanner returned no result output")
        except subprocess.TimeoutExpired:
            result.update(status="timeout", error=f"Scanner exceeded {timeout:g} seconds")
        except OSError as error:
            result["error"] = str(error)
        stream.write(f"\nResult: {result['status']}; exit code: {result['exit_code']}\n")
        if result["error"]:
            stream.write(f"Error: {result['error']}\n")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("dependency-audit-reports"))
    parser.add_argument("--timeout", type=float, default=90, help="Maximum seconds per scan (default: 90)")
    arguments = parser.parse_args(argv)
    if not math.isfinite(arguments.timeout) or arguments.timeout <= 0:
        parser.error("--timeout must be a finite positive number of seconds")
    python = [sys.executable, "-m", "pip_audit", "--strict", "--require-hashes", "--disable-pip", "-r"]
    npm = [shutil.which("npm") or "npm", "audit", "--prefix"]
    scans = [
        ("python-runtime", "requirements.lock", [*python, "requirements.lock"]),
        ("python-development", "requirements-dev.lock", [*python, "requirements-dev.lock"]),
        ("npm-dom", "tests/miniapp-ui", [*npm, "tests/miniapp-ui", "--include=dev", "--json"]),
        ("npm-browser", "tests/miniapp-browser", [*npm, "tests/miniapp-browser", "--include=dev", "--json"]),
    ]
    try:
        arguments.output_dir.mkdir(parents=True, exist_ok=True)
        results = [run_scan(scan, arguments.output_dir, arguments.timeout) for scan in scans]
        success = all(result["status"] == "success" for result in results)
        summary = "\n".join(
            [
                "# Dependency audit",
                "",
                "| Scan | Target | Result | Exit code | Report |",
                "|---|---|---|---|---|",
                *(
                    f"| {r['id']} | `{r['target']}` | {r['status']} | {r['exit_code']} | `{r['report']}` |"
                    for r in results
                ),
                "",
            ]
        )
        (arguments.output_dir / "summary.json").write_text(
            json.dumps({"success": success, "scans": results}, indent=2) + "\n",
            encoding="utf-8",
        )
        (arguments.output_dir / "summary.md").write_text(summary, encoding="utf-8")
        print(summary)
    except OSError as error:
        print(f"Cannot write dependency audit evidence: {error}", file=sys.stderr)
        return 1
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
