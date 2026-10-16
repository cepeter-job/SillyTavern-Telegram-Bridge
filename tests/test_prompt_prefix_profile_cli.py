"""Only content-free observations may leave the explicitly supplied local input."""

import hashlib
import importlib.util
import json
import stat

import pytest
from test_prompt_prefix_profile import request, scope


def cli():
    assert importlib.util.find_spec("tools.profile_prompt_prefix"), "profiler CLI not implemented"
    from tools import profile_prompt_prefix

    return profile_prompt_prefix


def test_local_request_capture_is_read_only_and_report_never_contains_payload(tmp_path):
    source = tmp_path / "captured.jsonl"
    output = tmp_path / "profile.json"
    records = [{"scope": scope(), "request": request("SECRET_CANARY " * 100, suffix=f"q{i}")} for i in range(3)]
    source.write_text("\n".join(json.dumps(r) for r in records))
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    assert cli().main(["--samples", str(source), "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["cohorts"][0]["retained_samples"] == 3
    assert report["source"] == "explicit_local_request_capture"
    assert report["provider_requests_issued"] == 0
    assert report["prompt_mutations"] == 0
    assert "SECRET_CANARY" not in output.read_text()
    assert "chat/session/incarnation" not in output.read_text()
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_native_demo_uses_real_builder_and_observes_summary_variation(tmp_path):
    output = tmp_path / "demo.json"
    assert cli().main(["--demo", "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["source"] == "synthetic_native_builder"
    assert len(report["cohorts"]) >= 3
    assert any(g["stable_prefix_messages"] > 0 for g in report["cohorts"])
    assert any(g["stable_prefix_messages"] == 0 and g["stable_prefix_characters"] > 0 for g in report["cohorts"])
    assert any(g["latest_instruction_duplicates"] for g in report["cohorts"])
    assert report["provider_savings_fraction"] is None


@pytest.mark.parametrize("suffix", ["json", "sqlite3"])
def test_output_never_overwrites_source_or_an_existing_file(tmp_path, suffix):
    existing = tmp_path / ("existing." + suffix)
    existing.write_text("untouched")
    assert cli().main(["--demo", "--output", str(existing)]) != 0
    assert existing.read_text() == "untouched"


def test_symlink_output_is_not_followed(tmp_path):
    original = tmp_path / "original"
    original.write_text("untouched")
    output = tmp_path / "link"
    output.symlink_to(original)
    assert cli().main(["--demo", "--output", str(output)]) != 0
    assert original.read_text() == "untouched"


def test_invalid_json_never_echoes_private_content_or_leaves_partial_report(tmp_path, capsys):
    source = tmp_path / "source.jsonl"
    source.write_text("PRIVATE_JSON_ERROR NOT JSON")
    output = tmp_path / "out.json"
    assert cli().main(["--samples", str(source), "--output", str(output)]) != 0
    captured = capsys.readouterr()
    assert "PRIVATE_JSON_ERROR" not in captured.out + captured.err
    assert not output.exists()


def test_duplicate_json_keys_are_rejected(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text('{"scope": {}, "scope": {}, "request": {}}')
    assert cli().main(["--samples", str(source), "--output", str(tmp_path / "out")]) != 0


def test_empty_input_does_not_claim_analyzed_requests(tmp_path):
    source = tmp_path / "empty.jsonl"
    source.write_text("\n")
    assert cli().main(["--samples", str(source), "--output", str(tmp_path / "out")]) != 0


def test_request_line_count_is_bounded(tmp_path, monkeypatch):
    mod = cli()
    monkeypatch.setattr(mod, "MAX_INPUT_RECORDS", 2)
    source = tmp_path / "records.jsonl"
    line = json.dumps({"scope": scope(), "request": request()}) + "\n"
    source.write_text(line * 3)
    assert mod.main(["--samples", str(source), "--output", str(tmp_path / "out")]) != 0


def test_raw_json_request_input_does_not_get_loaded_via_symlink(tmp_path):
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps({"scope": scope(), "request": request()}))
    link = tmp_path / "link.jsonl"
    link.symlink_to(source)
    assert cli().main(["--samples", str(link), "--output", str(tmp_path / "out")]) != 0


def test_capture_cli_does_not_import_runtime_or_provider_stack():
    import subprocess
    import sys
    from pathlib import Path

    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import tools.profile_prompt_prefix; "
            "assert 'bridge.generation' not in sys.modules; "
            "assert 'bridge.provider_port' not in sys.modules",
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert probe.returncode == 0, probe.stderr
