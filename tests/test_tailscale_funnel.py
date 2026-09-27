import copy
import json

import pytest
from test_installer import inputs

from bridge.config_values import ConfigurationError
from bridge.miniapp_config import MiniAppConfig

HOST = "bridge.example.ts.net"
NODE = {"BackendState": "Running", "Self": {"DNSName": HOST + "."}}
TARGET = "http://127.0.0.1:8787"


def serving(port=443, *, target=TARGET, public=True):
    hostport = f"{HOST}:{port}"
    result = {"TCP": {str(port): {"HTTPS": True}}, "Web": {hostport: {"Handlers": {"/": {"Proxy": target}}}}}
    if public:
        result["AllowFunnel"] = {hostport: True}
    return result


def merged(*configs):
    output = {}
    for config in configs:
        for key, value in config.items():
            output.setdefault(key, {}).update(value)
    return output


def test_blank_url_chooses_a_free_port_without_touching_private_services():
    from bridge.tailscale_funnel import plan_funnel

    existing = merged(serving(443, target="http://127.0.0.1:8000", public=False), serving(8443, public=False))
    before = copy.deepcopy(existing)
    plan = plan_funnel(MiniAppConfig(), NODE, existing)
    assert plan.public_url == f"https://{HOST}:10000/miniapp/"
    assert plan.target == TARGET and plan.public_port == 10000 and not plan.reuse
    assert existing == before


@pytest.mark.parametrize("port", [443, 8443, 10000])
def test_reuses_only_exact_public_root_proxy(port):
    from bridge.tailscale_funnel import plan_funnel

    plan = plan_funnel(MiniAppConfig(), NODE, serving(port))
    assert plan.reuse and plan.public_port == port


@pytest.mark.parametrize("variant", ["private", "other_target", "extra_path", "foreground", "tcp", "other_host"])
def test_explicit_port_conflict_is_refused_not_replaced(variant):
    from bridge.tailscale_funnel import plan_funnel

    existing = serving()
    if variant == "private":
        existing.pop("AllowFunnel")
    elif variant == "other_target":
        existing["Web"][f"{HOST}:443"]["Handlers"]["/"]["Proxy"] = "http://127.0.0.1:8000"
    elif variant == "extra_path":
        existing["Web"][f"{HOST}:443"]["Handlers"]["/private"] = {"Proxy": "http://127.0.0.1:9000"}
    elif variant == "foreground":
        existing = {"Foreground": {"session": existing}}
    elif variant == "tcp":
        existing = {"TCP": {"443": {"TCPForward": "127.0.0.1:22"}}}
    elif variant == "other_host":
        existing["Web"]["different.example.ts.net:443"] = existing["Web"].pop(f"{HOST}:443")
    before = copy.deepcopy(existing)
    with pytest.raises(ValueError):
        plan_funnel(MiniAppConfig(public_url=f"https://{HOST}/miniapp/"), NODE, existing)
    assert existing == before


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/miniapp/",
        "https://other.example.ts.net/miniapp/",
        "https://bridge.example.ts.net:9443/miniapp/",
    ],
)
def test_foreign_hostname_or_unsupported_public_port_is_rejected(url):
    from bridge.tailscale_funnel import plan_funnel

    with pytest.raises(ValueError):
        plan_funnel(MiniAppConfig(public_url=url), NODE, {})


@pytest.mark.parametrize(
    "node",
    [
        {},
        {"BackendState": "NeedsLogin"},
        {"BackendState": "Running", "Self": {}},
        {"BackendState": "Running", "Self": {"DNSName": "bad name.ts.net"}},
    ],
)
def test_disconnected_or_ambiguous_node_never_produces_a_plan(node):
    from bridge.tailscale_funnel import plan_funnel

    with pytest.raises(ValueError):
        plan_funnel(MiniAppConfig(), node, {})


@pytest.mark.parametrize(
    "state", [[], {"TCP": []}, {"Web": False}, {"Unexpected": {}}, {"Foreground": {"invalid": []}}]
)
def test_unknown_config_shape_fails_closed(state):
    from bridge.tailscale_funnel import plan_funnel

    with pytest.raises(ValueError):
        plan_funnel(MiniAppConfig(), NODE, state)


def test_all_supported_ports_busy_is_refused():
    from bridge.tailscale_funnel import plan_funnel

    existing = merged(*(serving(port, public=False) for port in (443, 8443, 10000)))
    with pytest.raises(ValueError):
        plan_funnel(MiniAppConfig(), NODE, existing)


def fixture_cli(monkeypatch, module, initial=None):
    state = copy.deepcopy(initial or {})
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        if args == ("version",):
            return "1.102.4\n"
        if args == ("status", "--json"):
            return json.dumps(NODE)
        if args == ("funnel", "status", "--json"):
            return json.dumps(state)
        if args[:2] == ("funnel", "--bg"):
            port = int(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--https=")))
            state.update(merged(state, serving(port)))
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(module, "_tailscale", run)
    return state, calls


def test_prepare_fills_only_blank_url_then_enable_verifies_exact_mapping(tmp_path, monkeypatch):
    import bridge.tailscale_funnel as funnel
    from bridge.install_support import prepare_install

    home, source, env, units = inputs(tmp_path)
    prepare_install(source, home, env, units)
    original = env.read_text()
    existing, calls = fixture_cli(monkeypatch, funnel)
    plan = funnel.prepare_funnel(source, home, env)
    assert env.read_text() == original.replace(
        "SILLYTAVERN_MINIAPP_PUBLIC_URL=", f"SILLYTAVERN_MINIAPP_PUBLIC_URL={plan.public_url}", 1
    )
    assert env.stat().st_mode & 0o077 == 0
    assert not any(call[:2] == ("funnel", "--bg") for call in calls)
    monkeypatch.setattr(funnel, "verify_backend", lambda _config: None)
    funnel.enable_funnel(source, home, env)
    assert ("funnel", "--bg", "--https=443", TARGET) in calls
    assert existing == serving()
    count = len(calls)
    funnel.enable_funnel(source, home, env)
    assert not any(call[:2] == ("funnel", "--bg") for call in calls[count:])


def test_unhealthy_backend_or_concurrent_conflict_is_never_published(tmp_path, monkeypatch):
    import bridge.tailscale_funnel as funnel
    from bridge.install_support import prepare_install

    home, source, env, units = inputs(tmp_path)
    prepare_install(source, home, env, units)
    state, calls = fixture_cli(monkeypatch, funnel)
    funnel.prepare_funnel(source, home, env)

    def unavailable(_config):
        raise ValueError("not the bridge")

    monkeypatch.setattr(funnel, "verify_backend", unavailable)
    with pytest.raises(ValueError):
        funnel.enable_funnel(source, home, env)
    monkeypatch.setattr(funnel, "verify_backend", lambda _config: state.update(serving(public=False)))
    with pytest.raises(ValueError):
        funnel.enable_funnel(source, home, env)
    assert not any(call[:2] == ("funnel", "--bg") for call in calls)


def test_duplicate_or_symlink_environment_is_not_rewritten(tmp_path):
    from bridge.tailscale_funnel import store_public_url

    env = tmp_path / ".env"
    env.write_text("SILLYTAVERN_MINIAPP_PUBLIC_URL=\nSILLYTAVERN_MINIAPP_PUBLIC_URL=\n")
    env.chmod(0o600)
    before = env.read_bytes()
    with pytest.raises(ValueError):
        store_public_url(env, f"https://{HOST}/miniapp/")
    assert env.read_bytes() == before
    alias = tmp_path / "alias"
    alias.symlink_to(env)
    with pytest.raises(ConfigurationError):
        store_public_url(alias, f"https://{HOST}/miniapp/")
    assert env.read_bytes() == before


@pytest.mark.parametrize("valid_shell,auth_status", [(True, 401), (False, 401), (True, 200)])
def test_readiness_uses_real_loopback_http_and_requires_auth_rejection(valid_shell, auth_status):
    import threading
    from dataclasses import replace
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    import bridge.tailscale_funnel as funnel

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append((self.headers.get("Host"), self.headers.get("Origin")))
            if self.path == "/miniapp/":
                body = (
                    (Path(funnel.__file__).with_name("miniapp_assets") / "index.html").read_bytes()
                    if valid_shell
                    else b"other site"
                )
                status = 200
            else:
                status, body = auth_status, b'{"error":{"code":"auth"}}'
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        config = replace(MiniAppConfig(public_url=f"https://{HOST}:10000/miniapp/"), port=server.server_port)
        if valid_shell and auth_status == 401:
            funnel.verify_backend(config)
            assert received == [(f"{HOST}:10000", f"https://{HOST}:10000")] * 2
        else:
            with pytest.raises(ValueError):
                funnel.verify_backend(config)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_no_start_shell_installer_prepares_url_but_never_publishes(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    home, source, env, _units = inputs(tmp_path)
    shutil.copy2(root / "install.sh", source / "install.sh")
    shutil.copy2(root / "requirements.lock", source / "requirements.lock")
    (source / "bridge").symlink_to(root / "bridge", target_is_directory=True)
    (source / ".venv").symlink_to(sys.prefix, target_is_directory=True)
    env.parent.mkdir(parents=True)
    env.write_text(
        "SILLYTAVERN_TELEGRAM_BOT_TOKEN=123:synthetic\nSILLYTAVERN_TELEGRAM_ALLOWED_USERS=12345\nSILLYTAVERN_MODEL=test::sample\nLLM_API_KEY=fixture\nSILLYTAVERN_PROVIDER_ENDPOINT=https://provider.example/v1\nSILLYTAVERN_PROVIDER_ALLOWED_HOSTS=provider.example\nSILLYTAVERN_MINIAPP_PUBLIC_URL=\n"
    )
    env.chmod(0o600)
    binaries = home / "bin"
    binaries.mkdir()
    calls = home / "calls"
    for name in ("tailscale", "systemctl", "loginctl"):
        path = binaries / name
        path.write_text(
            f"#!{sys.executable}\n"
            + "import sys,json\nfrom pathlib import Path\n"
            + f"name={name!r}\nargs=sys.argv[1:]\n"
            + f"with Path({str(calls)!r}).open('a') as log: log.write(json.dumps([name,*args])+'\\n')\n"
            + f"node={NODE!r}\n"
            + "if name=='tailscale':\n"
            "    if args==['version']: print('1.102.4')\n"
            "    elif args==['status','--json']: print(json.dumps(node))\n"
            "    elif args==['funnel','status','--json']: print('{}')\n"
            "    else: sys.exit(9)\n"
            "elif name=='loginctl': print('no')\n"
        )
        path.chmod(0o700)
    process_env = {
        **os.environ,
        "HOME": str(home),
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "SILLYTAVERN_ENV_FILE": str(env),
        "XDG_CONFIG_HOME": str(home / ".config"),
    }
    result = subprocess.run(
        ["/bin/bash", str(source / "install.sh"), "--no-start", "--no-deps", "--with-tailscale-funnel"],
        env=process_env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    recorded = [json.loads(line) for line in calls.read_text().splitlines()]
    assert ["tailscale", "status", "--json"] in recorded
    assert not any(call[:3] == ["tailscale", "funnel", "--bg"] for call in recorded)
    assert not any(call[0] == "systemctl" and "restart" in call for call in recorded)
    assert f"SILLYTAVERN_MINIAPP_PUBLIC_URL=https://{HOST}/miniapp/" in env.read_text()


def test_old_tailscale_cli_is_rejected_without_rewriting_env(tmp_path, monkeypatch):
    import bridge.tailscale_funnel as funnel
    from bridge.install_support import prepare_install

    home, source, env, units = inputs(tmp_path)
    prepare_install(source, home, env, units)
    before = env.read_bytes()
    monkeypatch.setattr(
        funnel,
        "_tailscale",
        lambda *args: "1.50.0" if args == ("version",) else pytest.fail("must stop before configuration"),
    )
    with pytest.raises(ValueError, match=r"1\.52"):
        funnel.prepare_funnel(source, home, env)
    assert env.read_bytes() == before


def test_failed_publication_never_reports_success_or_resets_existing_routes(tmp_path, monkeypatch):
    import bridge.tailscale_funnel as funnel
    from bridge.install_support import prepare_install

    home, source, env, units = inputs(tmp_path)
    prepare_install(source, home, env, units)
    existing = serving(443, public=False)
    state, calls = fixture_cli(monkeypatch, funnel, existing)
    funnel.prepare_funnel(source, home, env)
    monkeypatch.setattr(funnel, "verify_backend", lambda _config: None)
    normal = funnel._tailscale

    def failed(*args, **kwargs):
        if args[:2] == ("funnel", "--bg"):
            raise ValueError("not authorized")
        return normal(*args, **kwargs)

    monkeypatch.setattr(funnel, "_tailscale", failed)
    with pytest.raises(ValueError, match="not authorized"):
        funnel.enable_funnel(source, home, env)
    assert state == existing
    assert not any("reset" in call for call in calls)


def test_success_exit_without_public_mapping_is_not_success(tmp_path, monkeypatch):
    import bridge.tailscale_funnel as funnel
    from bridge.install_support import prepare_install

    home, source, env, units = inputs(tmp_path)
    prepare_install(source, home, env, units)
    _state, _calls = fixture_cli(monkeypatch, funnel)
    funnel.prepare_funnel(source, home, env)
    monkeypatch.setattr(funnel, "verify_backend", lambda _config: None)
    normal = funnel._tailscale
    monkeypatch.setattr(
        funnel, "_tailscale", lambda *args, **kwargs: "" if args[:2] == ("funnel", "--bg") else normal(*args, **kwargs)
    )
    with pytest.raises(ValueError, match="confirm"):
        funnel.enable_funnel(source, home, env)
