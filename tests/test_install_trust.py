"""Isolated installer trust regressions: pinned signers and SSH server keys."""

import subprocess

from test_installer import ROOT, _bootstrap_installer_fixture, inputs


def test_install_signer_path_backfills_old_env_without_touching_secrets(tmp_path):
    from bridge.installer_trust import ensure_install_update_signer_path

    home, _source, env, _units = inputs(tmp_path)
    env.parent.mkdir(parents=True)
    original = "LLM_API_KEY='$(touch /tmp/install-must-not-execute)'\n# preserved\n"
    env.write_text(original, encoding="utf-8")
    env.chmod(0o600)
    signers = home / ".config/sillytavern-telegram/trusted-maintainers"

    result = ensure_install_update_signer_path(env, home, signers)
    assert result == signers
    updated = env.read_text(encoding="utf-8")
    assert original in updated
    assert f"SILLYTAVERN_UPDATE_ALLOWED_SIGNERS={signers}" in updated
    assert env.stat().st_mode & 0o077 == 0
    assert ensure_install_update_signer_path(env, home, signers) == signers
    assert env.read_text(encoding="utf-8") == updated


def test_install_signer_path_preserves_preconfigured_custom_trust(tmp_path):
    from bridge.installer_trust import ensure_install_update_signer_path

    home, _source, env, _units = inputs(tmp_path)
    env.parent.mkdir(parents=True)
    env.write_text("SILLYTAVERN_UPDATE_ALLOWED_SIGNERS=~/operator-keys\nSECRET=keep\n")
    original = env.read_bytes()
    assert ensure_install_update_signer_path(env, home, home / "default-signers") == home / "operator-keys"
    assert env.read_bytes() == original


def _use_real_ssh_keygen(tmp_path):
    import shlex
    import shutil

    real_keygen = shutil.which("ssh-keygen")
    assert real_keygen
    wrapper = tmp_path / "bin/ssh-keygen"
    wrapper.write_text("#!/bin/sh\nexec " + shlex.quote(real_keygen) + ' "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)


def test_install_adds_pinned_github_host_once_and_preserves_other_hosts(tmp_path):
    script, home, _git_log, env = _bootstrap_installer_fixture(tmp_path)
    _use_real_ssh_keygen(tmp_path)
    known_hosts = home / ".ssh/known_hosts"
    known_hosts.parent.mkdir()
    other_host = "example.test ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"
    known_hosts.write_text(other_host, encoding="utf-8")
    known_hosts.chmod(0o644)

    for _ in range(2):
        result = subprocess.run(
            ["/bin/bash", str(script), "--unsafe-main", "--no-deps", "--no-start"],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    content = known_hosts.read_text(encoding="utf-8")
    assert content.startswith(other_host)
    assert content.count("github.com ssh-ed25519 ") == 1
    assert "AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl" in content
    assert known_hosts.stat().st_mode & 0o077 == 0
    assert known_hosts.parent.stat().st_mode & 0o077 == 0


def test_installer_refuses_conflicting_github_ssh_host_without_overwrite(tmp_path):
    script, home, _git_log, env = _bootstrap_installer_fixture(tmp_path)
    _use_real_ssh_keygen(tmp_path)
    known_hosts = home / ".ssh/known_hosts"
    known_hosts.parent.mkdir()
    import re

    record = re.search(r"^TRUST_LINE='([^']+)'$", (ROOT / "install.sh").read_text(), re.MULTILINE)
    assert record is not None
    other_key = record.group(1).split()[3]
    content = f"github.com ssh-ed25519 {other_key}\n"
    known_hosts.write_text(content, encoding="utf-8")
    result = subprocess.run(
        ["/bin/bash", str(script), "--unsafe-main", "--no-deps", "--no-start"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "differs from the pinned key" in result.stderr
    assert known_hosts.read_text(encoding="utf-8") == content


def test_installer_does_not_follow_known_hosts_symlinks(tmp_path):
    script, home, _git_log, env = _bootstrap_installer_fixture(tmp_path)
    _use_real_ssh_keygen(tmp_path)
    target = home / "protected"
    target.write_text("do-not-overwrite", encoding="utf-8")
    ssh_dir = home / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "known_hosts").symlink_to(target)
    result = subprocess.run(
        ["/bin/bash", str(script), "--unsafe-main", "--no-deps", "--no-start"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unsafe SSH known-hosts file" in result.stderr
    assert target.read_text(encoding="utf-8") == "do-not-overwrite"


def test_installer_can_skip_github_known_hosts_setup(tmp_path):
    script, home, _git_log, env = _bootstrap_installer_fixture(tmp_path)
    result = subprocess.run(
        ["/bin/bash", str(script), "--unsafe-main", "--no-deps", "--no-start", "--no-github-known-hosts"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert not (home / ".ssh").exists()
    assert (home / ".config/sillytavern-telegram/trusted-maintainers").is_file()
