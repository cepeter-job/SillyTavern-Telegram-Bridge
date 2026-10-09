"""Diagnostic input is untrusted; reports may export only validated metadata."""

import hashlib
import importlib.util
import json
import stat

import pytest


def module():
    assert importlib.util.find_spec("tools.report_request_observations"), "observation report not implemented"
    from tools import report_request_observations

    return report_request_observations


def event(n, **kwargs):
    return {
        "event": "provider.request_observed",
        "profile_request_id": "wire-" + f"{n:024x}",
        "transport": "chat_completions",
        "purpose": "story",
        "phase": "initial",
        "status": "completed",
        "input_tokens": 10,
        "output_tokens": 2,
        "cached_tokens": 4,
        "usage_complete": True,
        "model_ref": "a" * 64,
        "provider_ref": "b" * 64,
        **kwargs,
    }


def test_local_log_report_is_readonly_private_and_exclusive(tmp_path):
    source, output = tmp_path / "runtime.jsonl", tmp_path / "report.json"
    lines = [
        event(1, text="PRIVATE_DIALOGUE", messages=["PRIVATE"]),
        event(2),
        {"event": "runtime.log", "message": "PRIVATE_ERROR"},
    ]
    source.write_text("\n".join(json.dumps(e) for e in lines))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    assert module().main(["--log", str(source), "--output", str(output)]) == 0
    report = json.loads(output.read_text())
    assert report["window_usage"]["known_input_tokens"] == 20
    assert report["optimization_authorized"] is False
    assert "PRIVATE" not in output.read_text()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert digest == hashlib.sha256(source.read_bytes()).hexdigest()
    assert module().main(["--log", str(source), "--output", str(output)]) != 0


def test_missing_usage_is_unknown_and_bad_fields_never_echo():
    report = module().summarize(
        [
            event(1),
            event(
                2,
                input_tokens=None,
                output_tokens=None,
                cached_tokens=None,
                usage_complete=False,
                purpose="PRIVATE",
                model_ref="PRIVATE",
            ),
        ]
    )
    assert report["window_usage"]["unknown_input_requests"] == 1
    assert report["window_usage"]["complete_input_tokens"] is None
    assert "PRIVATE" not in json.dumps(report)


def test_bounded_recent_window_uses_only_requested_completed_records():
    report = module().summarize([event(n) for n in range(30)], window=8)
    assert report["records_seen"] == 30
    assert len(report["records"]) == 8
    assert report["window_usage"]["known_input_tokens"] == 80


def test_invalid_json_and_symlinks_are_rejected_without_payload_errors(tmp_path, capsys):
    source = tmp_path / "log.jsonl"
    source.write_text('{"PRIVATE_BAD_JSON"')
    output = tmp_path / "report.json"
    assert module().main(["--log", str(source), "--output", str(output)]) != 0
    assert not output.exists()
    assert "PRIVATE" not in capsys.readouterr().err
    source.write_text(json.dumps(event(1)))
    alias = tmp_path / "alias"
    alias.symlink_to(source)
    assert module().main(["--log", str(alias), "--output", str(output)]) != 0
    assert not output.exists()


@pytest.mark.parametrize("bad", [-1, True, 1.5, "13", 2**62])
def test_malformed_counts_never_create_false_usage(bad):
    report = module().summarize([event(1, input_tokens=bad)])
    assert report["window_usage"]["complete_input_tokens"] is None
    assert report["window_usage"]["unknown_input_requests"] == 1
