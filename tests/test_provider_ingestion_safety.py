"""Regressions for credentials, bounded parsing and durable interaction ownership."""

from application_test_setup import ensure_application_extensions, make_test_application_services
from settings_test_support import make_test_settings

ensure_application_extensions()

import base64
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from bridge import callback_dispatch, callbacks, document_extraction, image_generation, native_imports, world_callbacks
from bridge.image_reference import ImageReference
from bridge.image_routing import ImageRoute
from bridge.limits import IMAGE_MAX_BYTES
from bridge.metadata import get_meta, set_meta
from bridge.network_security import EndpointPolicyError
from bridge.panel_bindings import bind_panel_session
from bridge.request_types import RequestContext
from bridge.sqlite_store import db_connect, write_transaction


class RecordingResponse:
    def __init__(self, raw):
        self.raw = raw
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        self.read_sizes.append(size)
        return self.raw if size < 0 else self.raw[:size]


def image_settings(tmp_path, environ, key_source="IMAGE_KEY"):
    catalog = tmp_path / "providers.yaml"
    source = f"    api_key_env: {key_source}\n" if key_source else ""
    catalog.write_text(
        "providers:\n  image:\n    api_endpoint: https://images.example/v1\n"
        + source
        + "    image_enabled: true\n    image_models: [draw]\n"
    )
    return make_test_settings(
        environ={"SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "images.example", **environ},
        home=tmp_path,
        provider_config_file=catalog,
    )


def test_explicit_missing_image_key_never_uses_default(tmp_path, monkeypatch):
    settings = image_settings(tmp_path, {"LLM_API_KEY": "text-secret"})
    calls = []
    monkeypatch.setattr(image_generation, "strict_urlopen", lambda *a, **k: calls.append(a) or RecordingResponse(b"{}"))
    with pytest.raises(ValueError, match=r"credential is missing.*IMAGE_KEY"):
        image_generation.generate_image("image::draw", "a moon", app_settings=settings)
    assert calls == []


def test_empty_image_key_never_uses_default(tmp_path, monkeypatch):
    settings = image_settings(tmp_path, {"IMAGE_KEY": "", "LLM_API_KEY": "text-secret"})
    calls = []
    monkeypatch.setattr(image_generation, "strict_urlopen", lambda *a, **k: calls.append(a) or RecordingResponse(b"{}"))
    with pytest.raises(ValueError, match=r"credential is missing.*IMAGE_KEY"):
        image_generation.generate_image("image::draw", "a moon", app_settings=settings)
    assert calls == []


@pytest.mark.parametrize(
    ("key_source", "environ", "authorization"),
    [
        ("IMAGE_KEY", {"IMAGE_KEY": "image-secret", "LLM_API_KEY": "text-secret"}, "Bearer image-secret"),
        ("", {"LLM_API_KEY": "text-secret"}, "Bearer text-secret"),
    ],
)
def test_image_credential_success_routes(tmp_path, monkeypatch, key_source, environ, authorization):
    settings = image_settings(tmp_path, environ, key_source)
    requests = []
    response = RecordingResponse(b'{"data":[{"b64_json":"UE5H"}]}')
    monkeypatch.setattr(image_generation, "strict_urlopen", lambda request, **k: requests.append(request) or response)
    assert image_generation.generate_image("image::draw", "a moon", app_settings=settings) == (
        b"PNG",
        "",
        "image::draw",
    )
    assert requests[0].get_header("Authorization") == authorization
    assert json.loads(requests[0].data)["model"] == "draw"
    assert response.read_sizes == [4 * ((IMAGE_MAX_BYTES + 2) // 3) + 65536 + 1]


def test_image_json_response_over_limit_is_rejected_before_decode(tmp_path, monkeypatch):
    settings = image_settings(tmp_path, {"IMAGE_KEY": "image-secret"})
    limit = 4 * ((IMAGE_MAX_BYTES + 2) // 3) + 65536
    response = RecordingResponse(b" " * (limit + 2))
    monkeypatch.setattr(image_generation, "strict_urlopen", lambda *a, **k: response)
    with pytest.raises(ValueError, match=r"Image provider response.*limit"):
        image_generation.generate_image("image::draw", "a moon", app_settings=settings)
    assert response.read_sizes == [limit + 1]


def test_decoded_image_limit_remains_independent(tmp_path, monkeypatch):
    settings = image_settings(tmp_path, {"IMAGE_KEY": "image-secret"})
    response = RecordingResponse(
        json.dumps({"data": [{"b64_json": base64.b64encode(b"a" * (IMAGE_MAX_BYTES + 1)).decode()}]}).encode()
    )
    monkeypatch.setattr(image_generation, "strict_urlopen", lambda *a, **k: response)
    with pytest.raises(ValueError, match=r"Generated image.*limit"):
        image_generation.generate_image("image::draw", "a moon", app_settings=settings)


def _reference_route(*, api_key_env: str = "IMAGE_KEY", endpoint: str = "https://images.example/v1"):
    return ImageRoute(
        selection="image::step-image-edit-2",
        provider_id="image",
        model="step-image-edit-2",
        transport="reference",
        edit_route="openai",
        spec={
            "api_endpoint": endpoint,
            "api_key_env": api_key_env,
            "image_enabled": True,
            "image_models": ["step-image-edit-2"],
        },
    )


def test_edit_missing_explicit_key_never_uses_default(tmp_path, monkeypatch):
    settings = image_settings(tmp_path, {"LLM_API_KEY": "text-secret"})
    calls = []
    monkeypatch.setattr(image_generation, "strict_urlopen", lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError, match=r"credential is missing.*IMAGE_KEY"):
        image_generation.edit_image(
            _reference_route(),
            "portrait",
            ImageReference(b"PNG", "image/png", "Mira.png"),
            app_settings=settings,
        )
    assert calls == []


def test_edit_endpoint_host_is_subject_to_provider_allowlist(tmp_path, monkeypatch):
    settings = image_settings(tmp_path, {"IMAGE_KEY": "image-secret"})
    calls = []
    monkeypatch.setattr(image_generation, "strict_urlopen", lambda *a, **k: calls.append(a))
    route = _reference_route(endpoint="https://untrusted.example/v1")
    with pytest.raises(EndpointPolicyError, match="explicitly listed"):
        image_generation.edit_image(
            route,
            "portrait",
            ImageReference(b"PNG", "image/png", "Mira.png"),
            app_settings=settings,
        )
    assert calls == []


def test_edit_oversized_reference_fails_before_network(tmp_path, monkeypatch):
    settings = image_settings(tmp_path, {"IMAGE_KEY": "image-secret"})
    calls = []
    monkeypatch.setattr(image_generation, "strict_urlopen", lambda *a, **k: calls.append(a))
    with pytest.raises(ValueError, match=r"reference image.*limit"):
        image_generation.edit_image(
            _reference_route(),
            "portrait",
            ImageReference(b"x" * (IMAGE_MAX_BYTES + 1), "image/png", "Mira.png"),
            app_settings=settings,
        )
    assert calls == []


def test_edit_json_response_over_limit_is_rejected_before_decode(tmp_path, monkeypatch):
    settings = image_settings(tmp_path, {"IMAGE_KEY": "image-secret"})
    limit = 4 * ((IMAGE_MAX_BYTES + 2) // 3) + 65536
    response = RecordingResponse(b" " * (limit + 2))
    monkeypatch.setattr(image_generation, "strict_urlopen", lambda *a, **k: response)
    with pytest.raises(ValueError, match=r"Image provider response.*limit"):
        image_generation.edit_image(
            _reference_route(),
            "portrait",
            ImageReference(b"PNG", "image/png", "Mira.png"),
            app_settings=settings,
        )
    assert response.read_sizes == [limit + 1]


def test_edit_decoded_output_limit_remains_independent(tmp_path, monkeypatch):
    settings = image_settings(tmp_path, {"IMAGE_KEY": "image-secret"})
    response = RecordingResponse(
        json.dumps({"data": [{"b64_json": base64.b64encode(b"a" * (IMAGE_MAX_BYTES + 1)).decode()}]}).encode()
    )
    monkeypatch.setattr(image_generation, "strict_urlopen", lambda *a, **k: response)
    with pytest.raises(ValueError, match=r"Generated image.*limit"):
        image_generation.edit_image(
            _reference_route(),
            "portrait",
            ImageReference(b"PNG", "image/png", "Mira.png"),
            app_settings=settings,
        )


def test_edit_errors_do_not_expose_credentials_or_reference_bytes(tmp_path):
    settings = image_settings(tmp_path, {"IMAGE_KEY": "super-secret"})
    route = _reference_route(endpoint="https://untrusted.example/v1")
    reference = ImageReference(b"sensitive-reference", "image/png", "Mira.png")
    with pytest.raises(EndpointPolicyError) as caught:
        image_generation.edit_image(route, "portrait", reference, app_settings=settings)
    message = str(caught.value)
    assert "super-secret" not in message
    assert "sensitive-reference" not in message


def test_unterminated_markup_has_bounded_processing(tmp_path):
    script = (
        "from pathlib import Path\n"
        "from bridge.settings import load_app_settings\n"
        "from bridge.document_extraction import extract_data_bank_text\n"
        "text = extract_data_bank_text('hostile.html', b'<' * 200000, "
        "app_settings=load_app_settings({}, home=Path('.').resolve()))\n"
        "assert text == '<' * 200000\n"
    )
    try:
        result = subprocess.run([sys.executable, "-c", script], timeout=3, capture_output=True, check=False)
    except subprocess.TimeoutExpired:
        pytest.fail("unterminated markup extraction exceeded the bounded 3-second subprocess deadline")
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.parametrize("suffix", ["html", "htm", "xml"])
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"<p>Hello &amp; world</p>\r\n<b> next </b>", "Hello & world\nnext"),
        (b"a<>b", "a<>b"),
        (b"a<unterminated", "a<unterminated"),
        (b"a<<<<", "a<<<<"),
        (b"a<<tag> b &#60;x&#62;", "a b <x>"),
        (b"<p></p>", ""),
    ],
)
def test_markup_extraction_preserves_existing_text_contract(tmp_path, suffix, raw, expected):
    assert (
        document_extraction.extract_data_bank_text(
            f"fixture.{suffix}", raw, app_settings=make_test_settings(home=tmp_path)
        )
        == expected
    )


@pytest.fixture
def interaction(tmp_path, monkeypatch):
    settings = make_test_settings(home=tmp_path, db_file=tmp_path / "bridge.sqlite3", world_dir=tmp_path / "worlds")
    settings.native_persona_settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings.native_persona_settings_file.write_text("{}")
    services = make_test_application_services(app_settings=settings)
    db = db_connect(app_settings=settings)
    session = services.session.ensure(db, "chat", settings.default_model)
    messages = []
    monkeypatch.setattr(native_imports, "send_text", lambda *a, **k: messages.append(a) or [])
    monkeypatch.setattr(world_callbacks, "send_text", lambda *a, **k: messages.append(a) or [])
    monkeypatch.setattr(callback_dispatch, "answer_callback", lambda *a, **k: messages.append(a))
    monkeypatch.setattr(callbacks, "telegram_request", lambda *a, **k: {})
    monkeypatch.setattr("bridge.enum_callbacks.send_memory_menu", lambda *a, **k: None)
    raw = b'{"entries":{"0":{"key":["dragon"],"content":"A dragon."}}}'

    monkeypatch.setattr(native_imports, "download_telegram_file", lambda *_args, **_kwargs: raw)
    yield db, settings, services, session, raw
    db.close()


def open_world_upload(db, settings, services, session, actor="owner"):
    bind_panel_session(db, "chat", 12, session["session_id"], actor)
    callback_dispatch.process_callback(
        db,
        "token",
        {
            "id": "cb",
            "from": {"id": actor},
            "data": "world:upload",
            "message": {"message_id": 12, "chat": {"id": "chat"}},
        },
        services=services,
    )


def upload_document(db, settings, services, session_id, actor, filename):
    native_imports.import_telegram_document(
        db,
        "token",
        "chat",
        {"file_name": filename, "file_id": "synthetic", "file_size": 10},
        settings.default_model,
        api_key="",
        process_image=lambda *a, **k: None,
        memory_service=services.memory,
        persona_service=services.persona,
        group_director_service=services.group_director,
        app_settings=settings,
        rag_service=services.rag,
        provider_port=services.provider,
        request_context=RequestContext(db, session_id, actor, app_settings=settings),
    )


def test_character_upload_rejects_png_without_chara_metadata_and_stays_active(interaction, monkeypatch):
    db, settings, services, session, _raw = interaction
    set_meta(
        db,
        "character_upload:chat:owner",
        json.dumps(
            {
                "session_id": session["session_id"],
                "actor_id": "owner",
                "expires_at": time.time() + 900,
            }
        ),
    )
    monkeypatch.setattr(native_imports, "download_telegram_file", lambda *_args, **_kwargs: b"not-a-card")
    delivered = []
    image_calls = []
    monkeypatch.setattr(native_imports, "send_text", lambda _token, _chat, text: delivered.append(text) or [])

    native_imports.import_telegram_document(
        db,
        "token",
        "chat",
        {"file_name": "invalid.png", "file_id": "synthetic", "file_size": 10},
        settings.default_model,
        api_key="",
        process_image=lambda *args, **kwargs: image_calls.append((args, kwargs)),
        memory_service=services.memory,
        persona_service=services.persona,
        group_director_service=services.group_director,
        app_settings=settings,
        rag_service=services.rag,
        provider_port=services.provider,
        request_context=RequestContext(db, session["session_id"], "owner", app_settings=settings),
        character_upload=True,
    )

    assert image_calls == []
    assert delivered == ["This PNG is not a valid SillyTavern character card; chara metadata was not found."]
    assert get_meta(db, "character_upload:chat:owner")


def test_world_upload_requires_initiating_actor_and_session(interaction):
    db, settings, services, session, raw = interaction
    open_world_upload(db, settings, services, session)
    pending = get_meta(db, "world_upload:chat")
    upload_document(db, settings, services, session["session_id"], "other", "unrelated.txt")
    assert get_meta(db, "world_upload:chat") == pending
    upload_document(db, settings, services, session["session_id"], "other", "stolen.json")
    assert get_meta(db, "world_upload:chat") == pending
    assert not (settings.world_dir / "stolen.json").exists()
    wrong_session = services.session.create(db, "chat", settings.default_model)
    upload_document(db, settings, services, wrong_session["session_id"], "owner", "stale.json")
    assert get_meta(db, "world_upload:chat") == pending
    assert not (settings.world_dir / "stale.json").exists()
    # The initiating session stays authoritative even after the active session changes.
    upload_document(db, settings, services, session["session_id"], "owner", "owned.json")
    assert (settings.world_dir / "owned.json").read_bytes() == raw
    assert get_meta(db, "world_upload:chat") == ""


def test_world_upload_prompt_records_actor_from_request_context(interaction):
    db, settings, services, session, _raw = interaction
    open_world_upload(db, settings, services, session)
    assert json.loads(get_meta(db, "world_upload:chat"))["actor_id"] == "owner"


def test_document_import_rejects_caller_transaction_before_consumption_or_external_work(interaction, monkeypatch):
    db, settings, services, session, raw = interaction
    open_world_upload(db, settings, services, session)
    pending = get_meta(db, "world_upload:chat")
    boundaries = []
    original_install = native_imports.install_world_info_document

    def download(*_args, **_kwargs):
        boundaries.append(("download", db.in_transaction))
        return raw

    def install(*args, **kwargs):
        boundaries.append(("install", db.in_transaction))
        return original_install(*args, **kwargs)

    monkeypatch.setattr(native_imports, "download_telegram_file", download)
    monkeypatch.setattr(native_imports, "install_world_info_document", install)
    db.execute("BEGIN IMMEDIATE")
    set_meta(db, "caller_uncommitted", "preserve")
    try:
        with pytest.raises(RuntimeError, match=r"document import.*transaction"):
            upload_document(db, settings, services, session["session_id"], "owner", "in-transaction.json")
        assert boundaries == []
        assert get_meta(db, "world_upload:chat") == pending
        assert db.in_transaction
        assert get_meta(db, "caller_uncommitted") == "preserve"
        assert not (settings.world_dir / "in-transaction.json").exists()
    finally:
        db.rollback()
    assert get_meta(db, "caller_uncommitted") == ""
    assert get_meta(db, "world_upload:chat") == pending


@pytest.mark.parametrize("kind", ["expired", "legacy", "malformed", "nonobject", "invalid_expiry"])
def test_world_upload_invalid_pending_state_fails_closed_and_can_reopen(interaction, kind):
    db, settings, services, session, raw = interaction
    state = {"session_id": session["session_id"], "actor_id": "owner", "expires_at": time.time() + 900}
    if kind == "expired":
        state["expires_at"] = 0
    elif kind == "legacy":
        del state["actor_id"]
    elif kind == "invalid_expiry":
        state["expires_at"] = "invalid"
    value = "{" if kind == "malformed" else "[]" if kind == "nonobject" else json.dumps(state)
    set_meta(db, "world_upload:chat", value)
    upload_document(db, settings, services, session["session_id"], "owner", "old.json")
    assert not (settings.world_dir / "old.json").exists()
    open_world_upload(db, settings, services, session)
    upload_document(db, settings, services, session["session_id"], "owner", "reopened.json")
    assert (settings.world_dir / "reopened.json").read_bytes() == raw


def test_competing_world_documents_consume_pending_state_once(interaction, monkeypatch):
    db, settings, services, session, raw = interaction
    open_world_upload(db, settings, services, session)
    original_get_meta = native_imports.get_meta

    def delayed_read(*args, **kwargs):
        result = original_get_meta(*args, **kwargs)
        if args[1] == "world_upload:chat":
            time.sleep(0.05)  # Let an unprotected competing read observe the same durable prompt.
        return result

    monkeypatch.setattr(native_imports, "get_meta", delayed_read)
    barrier = threading.Barrier(2)

    def upload(filename):
        connection = db_connect(app_settings=settings)
        try:
            barrier.wait(timeout=5)
            upload_document(connection, settings, services, session["session_id"], "owner", filename)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(upload, ["first.json", "second.json"]))
    files = list(settings.world_dir.glob("*.json"))
    assert len(files) == 1
    assert files[0].read_bytes() == raw
    assert get_meta(db, "world_upload:chat") == ""


@pytest.mark.parametrize("data", ["enum:memory:off", "enum:memoryscope:session"])
def test_expired_memory_panel_does_not_lose_actor_protection(interaction, data):
    db, _settings, services, session, _raw = interaction
    set_meta(db, "memory_mode:chat", "on")
    set_meta(db, "memory_scope:chat", "unchanged")
    bind_panel_session(db, "chat", 20, session["session_id"], "owner")
    with write_transaction(db):
        db.execute("UPDATE panel_sessions SET expires_at=0 WHERE message_id='20'")
    callback_dispatch.process_callback(
        db,
        "token",
        {"id": "expired", "from": {"id": "other"}, "data": data, "message": {"message_id": 20, "chat": {"id": "chat"}}},
        services=services,
    )
    assert get_meta(db, "memory_mode:chat") == "on"
    assert get_meta(db, "memory_scope:chat") == "unchanged"


def test_live_memory_panel_preserves_owner_access(interaction):
    db, _settings, services, session, _raw = interaction
    set_meta(db, "memory_mode:chat", "on")
    bind_panel_session(db, "chat", 20, session["session_id"], "owner")
    callback = {
        "id": "live",
        "from": {"id": "other"},
        "data": "enum:memory:off",
        "message": {"message_id": 20, "chat": {"id": "chat"}},
    }
    callback_dispatch.process_callback(db, "token", callback, services=services)
    assert get_meta(db, "memory_mode:chat") == "on"
    callback["from"]["id"] = "owner"
    callback_dispatch.process_callback(db, "token", callback, services=services)
    assert get_meta(db, "memory_mode:chat") == "off"


@pytest.mark.parametrize(
    "models_endpoint",
    [
        "https://user:pass@provider.example/v1/models",
        "https://catalog.example/v1/models",
    ],
)
def test_models_endpoint_obeys_provider_endpoint_policy(tmp_path, monkeypatch, models_endpoint):
    from bridge import provider_discovery as discovery

    catalog = tmp_path / "providers.yaml"
    catalog.write_text(
        "providers:\n"
        "  alpha:\n"
        "    api_endpoint: https://provider.example/v1\n"
        "    api_key_env: LLM_API_KEY\n"
        "    transport: chat_completions\n"
        "    models: [seed]\n"
        "    discover_models: false\n"
        "    discover_model_metadata: true\n"
        f"    models_endpoint: {models_endpoint}\n",
        encoding="utf-8",
    )
    settings = make_test_settings(
        environ={
            "LLM_API_KEY": "fixture-key",
            "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS": "provider.example",
        },
        home=tmp_path,
        provider_config_file=catalog,
    )
    monkeypatch.setattr(
        discovery,
        "strict_urlopen",
        lambda *a, **kw: pytest.fail("rejected models endpoint reached the network"),
    )

    _config, refreshed, failed = discovery.refresh_model_catalog(
        force=True,
        metadata_only=True,
        app_settings=settings,
    )

    assert (refreshed, failed) == (0, 1)
