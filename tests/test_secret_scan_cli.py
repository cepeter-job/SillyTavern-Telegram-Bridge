"""Exercise the pinned secret-scan wrapper without network or real credentials."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "scan_secrets.sh"


def run_scan(tmp_path, *, checksum_ok=True, scan_exit=0, download_exit=0):
    binary = tmp_path / "bin"
    binary.mkdir()
    record = tmp_path / "commands.log"
    scripts = {
        "git": '#!/bin/bash\nprintf "%s\\n" "$TEST_ROOT"\n',
        "curl": '#!/bin/bash\nprintf "curl %s\\n" "$*" >> "$TEST_RECORD"\nexit "$DOWNLOAD_EXIT"\n',
        "sha256sum": '#!/bin/bash\ncat >> "$TEST_RECORD"\nexit "$CHECKSUM_EXIT"\n',
        "tar": '''#!/bin/bash
printf "tar %s\\n" "$*" >> "$TEST_RECORD"
while (( $# )); do
  if [[ "$1" == "-C" ]]; then destination="$2"; shift; fi
  shift
done
cat > "$destination/gitleaks" <<'SCAN'
#!/bin/bash
if [[ "$1" == "version" ]]; then printf '8.30.1\\n'; exit 0; fi
printf "scan %s\\n" "$*" >> "$TEST_RECORD"
exit "$SCAN_EXIT"
SCAN
chmod 700 "$destination/gitleaks"
''',
    }
    for name, content in scripts.items():
        path = binary / name
        path.write_text(content)
        path.chmod(0o700)
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "TEST_ROOT": str(tmp_path),
            "TEST_RECORD": str(record),
            "CHECKSUM_EXIT": "0" if checksum_ok else "1",
            "SCAN_EXIT": str(scan_exit),
            "DOWNLOAD_EXIT": str(download_exit),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return result, record.read_text() if record.exists() else ""


def test_scanner_is_checksum_pinned_and_checks_all_history(tmp_path):
    result, commands = run_scan(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz" in commands
    assert "551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb" in commands
    assert "scan git --redact=100 --exit-code=1 --log-opts=--all ." in commands
    assert not list(tmp_path.glob("sttb-gitleaks.*"))


@pytest.mark.parametrize("scan_exit", [1, 2, 17])
def test_findings_and_scanner_errors_fail_closed(tmp_path, scan_exit):
    result, commands = run_scan(tmp_path, scan_exit=scan_exit)
    assert result.returncode == scan_exit
    assert "scan git" in commands
    assert not list(tmp_path.glob("sttb-gitleaks.*"))


def test_checksum_failure_never_extracts_or_executes_download(tmp_path):
    result, commands = run_scan(tmp_path, checksum_ok=False)
    assert result.returncode != 0
    assert "tar " not in commands
    assert "scan " not in commands
    assert not list(tmp_path.glob("sttb-gitleaks.*"))


def test_download_failure_never_verifies_or_extracts(tmp_path):
    result, commands = run_scan(tmp_path, download_exit=22)
    assert result.returncode == 22
    assert "551f6fc83" not in commands
    assert "tar " not in commands
    assert not list(tmp_path.glob("sttb-gitleaks.*"))
