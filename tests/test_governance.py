"""Maintenance instructions and automation must match the executable repository."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]


def test_security_policy_has_real_disclosure_path_and_no_invented_sla():
    text = (ROOT / "SECURITY.md").read_text()
    assert "Security" in text and "private" in text.casefold()
    assert "main" in text and "preproduction" in text.casefold()
    assert "response-time" in text and "no guaranteed" in text.casefold()
    assert "security@" not in text
    assert "do not include" in text.casefold()


def test_contributor_commands_match_actual_tools():
    text = (ROOT / "CONTRIBUTING.md").read_text()
    for command in (
        "python -m ruff check .",
        "python -m ruff format --check .",
        "python tools/static_analysis.py",
        "python tools/check_dependency_lock.py",
        "python -m pytest",
        "--cov-report=json:coverage.json",
        "--require-hashes",
        "uv pip compile requirements.txt",
        "--generate-hashes",
        "requirements.lock",
        "git switch -c",
    ):
        assert command in text, command
    assert "custom requirements.lock" in text
    assert "do not assume" in text.casefold()
    assert "codeql" in text.casefold()


def test_dependabot_covers_pip_and_actions_without_unbounded_updates():
    config = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    assert config["version"] == 2
    updates = {item["package-ecosystem"]: item for item in config["updates"]}
    assert set(updates) == {"pip", "github-actions"}
    for item in updates.values():
        assert item["directory"] == "/"
        assert item["schedule"]["interval"] == "weekly"
        assert 1 <= item["open-pull-requests-limit"] <= 3
        assert item.get("insecure-external-code-execution") != "allow"
    assert not list((ROOT / ".github/workflows").glob("*codeql*"))


def test_ci_checks_dependency_manifest_and_lock_compatibility():
    assert "python tools/check_dependency_lock.py" in (ROOT / ".github/workflows/ci.yml").read_text()
    assert "packaging==" in (ROOT / "requirements-dev.txt").read_text()


def test_public_examples_match_original_reviewed_hashes():
    baseline = json.loads((ROOT / "tools/public_examples.json").read_text())
    paths = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "config/system_prompts.example").glob("*")
        if path.is_file()
    }
    assert paths == set(baseline["sha256"])
    for name, expected in baseline["sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name


def test_repository_explicitly_ignores_python_tool_caches():
    ignored = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {".pytest_cache/", ".mypy_cache/", ".ruff_cache/"} <= ignored


USER_ENVIRONMENT_VARIABLES = {
    "SILLYTAVERN_TELEGRAM_BOT_TOKEN",
    "SILLYTAVERN_TELEGRAM_ALLOWED_USERS",
    "SILLYTAVERN_DIR",
    "SILLYTAVERN_DEFAULT_CHARACTER",
    "SILLYTAVERN_MODEL",
    "SILLYTAVERN_DEFAULT_USER_NAME",
    "LLM_API_KEY",
    "SILLYTAVERN_ENV_FILE",
    "SILLYTAVERN_BRIDGE_HOME",
    "SILLYTAVERN_BRIDGE_SOURCE_DIR",
    "SILLYTAVERN_LIVE_BRIDGE_DIR",
    "SILLYTAVERN_PROVIDER_CONFIG",
    "SILLYTAVERN_MODEL_CACHE",
    "SILLYTAVERN_MODEL_REFRESH_SECONDS",
    "SILLYTAVERN_PROVIDER_ENDPOINT",
    "SILLYTAVERN_PROVIDER_TRANSPORT",
    "SILLYTAVERN_EXTRA_MODELS",
    "SILLYTAVERN_PROVIDER_ALLOWED_HOSTS",
    "SILLYTAVERN_PROVIDER_PRIVATE_HOSTS",
    "SILLYTAVERN_CONTEXT_WINDOW_TOKENS",
    "SILLYTAVERN_CONTEXT_OUTPUT_RESERVE_TOKENS",
    "SILLYTAVERN_CONTEXT_HISTORY_CANDIDATES",
    "SILLYTAVERN_CHARACTER_DIR",
    "SILLYTAVERN_CHARACTER_BACKUP_DIR",
    "SILLYTAVERN_WORLD_DIR",
    "SILLYTAVERN_SYSTEM_PROMPTS_DIR",
    "SILLYTAVERN_NATIVE_SETTINGS_FILE",
    "SILLYTAVERN_NATIVE_AVATAR_DIR",
    "SILLYTAVERN_ENFORCE_PROMPT_PERMISSIONS",
    "SILLYTAVERN_PERF_LOG",
    "SILLYTAVERN_MEMORY_DIAGNOSTICS",
    "ANTHROPIC_API_KEY",
    "SILLYTAVERN_CODEX_AUTH_FILE",
    "SILLYTAVERN_CODEX_CLIENT_VERSION",
    "HINDSIGHT_API_URL",
    "HINDSIGHT_API_KEY",
    "SILLYTAVERN_RAG_EMBEDDING_URL",
    "SILLYTAVERN_RAG_EMBEDDING_API_KEY",
    "SILLYTAVERN_RAG_ALLOWED_HOSTS",
    "SILLYTAVERN_RAG_PRIVATE_HOSTS",
    "SILLYTAVERN_RAG_EMBEDDING_MODEL",
    "SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS",
    "SILLYTAVERN_RAG_EMBEDDING_REVISION",
    "SILLYTAVERN_RAG_MAX_EXTRACTED_CHARS",
    "SILLYTAVERN_RAG_MAX_PDF_PAGES",
    "SILLYTAVERN_RAG_PDF_PARSE_TIMEOUT_SECONDS",
    "SILLYTAVERN_RAG_SEMANTIC_CANDIDATES",
    "SILLYTAVERN_SYNC_API_URL",
    "SILLYTAVERN_SYNC_API_HANDLE",
    "SILLYTAVERN_SYNC_API_PASSWORD",
    "SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS",
    "SILLYTAVERN_SYNC_API_INTERVAL_SECONDS",
    "SILLYTAVERN_STT_MODEL",
    "SILLYTAVERN_TTS_BIN",
    "SILLYTAVERN_TTS_VOICE",
    "OPENCODE_CLIENT_VERSION",
    "SILLYTAVERN_UPDATE_ALLOWED_SIGNERS",
    "SILLYTAVERN_UPDATE_PUBLIC_KEY",
    "SILLYTAVERN_UPDATE_SERVICE",
    "SILLYTAVERN_MINIAPP_PUBLIC_URL",
    "SILLYTAVERN_MINIAPP_PORT",
    "SILLYTAVERN_MINIAPP_AUTH_MAX_AGE",
}


def test_user_configuration_guide_and_env_example_cover_supported_environment_variables():
    configuration = (ROOT / "docs/configuration.md").read_text()
    example = (ROOT / ".env.example").read_text()
    missing_configuration = sorted(name for name in USER_ENVIRONMENT_VARIABLES if name not in configuration)
    missing_example = sorted(name for name in USER_ENVIRONMENT_VARIABLES if name not in example)
    assert missing_configuration == []
    assert missing_example == []


def test_public_docs_do_not_advertise_retired_hindsight_allowlist():
    documents = [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]
    offenders = [path for path in documents if "SILLYTAVERN_HINDSIGHT_ALLOWED_HOSTS" in path.read_text()]
    assert offenders == []


def test_configuration_manual_examples_do_not_clobber_private_files():
    text = (ROOT / "docs/configuration.md").read_text()
    assert "cp .env.example ~/.local/share/sillytavern-telegram/.env" not in text
    assert (
        "cp config/providers.example.yaml ~/.local/share/sillytavern-telegram/sillytavern_telegram_providers.yaml"
        not in text
    )


def test_user_configuration_guide_documents_provider_catalog_controls():
    readme = (ROOT / "docs/configuration.md").read_text()
    for field in (
        "name",
        "api_endpoint",
        "api_key_env",
        "transport",
        "adapter",
        "streaming",
        "models",
        "discover_models",
        "health_check",
        "extra_headers",
        "anthropic_version",
        "image_enabled",
        "image_endpoint",
        "image_edit_endpoint",
        "image_models",
        "image_model_capabilities",
        "image_auto",
        "edit_route",
    ):
        assert f"`{field}`" in readme, field
    provider_example = (ROOT / "config/providers.example.yaml").read_text()
    assert "image_default_size" not in provider_example
    assert "image_auto:" in provider_example
    assert "text_model: nano-gpt::chroma" in provider_example
    assert "reference_model: nano-gpt::step-image-edit-2" in provider_example
    assert "image_model_capabilities:" in provider_example
    assert "edit_route: openai" in provider_example


def test_user_facing_docs_keep_reasoning_under_providers():
    guide = (ROOT / "docs/user-guide.md").read_text()
    assert "| `/settings` | Open reasoning and generation controls |" not in guide
    assert "Both Story and Utility reasoning are configured from `/providers`" in guide


def test_readme_does_not_index_design_only_humanizer_spec():
    readme = (ROOT / "README.md").read_text()
    assert "docs/humanizer-weekly-sync.md" not in readme


def test_readme_is_a_compact_user_entrypoint_with_linked_guides():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert len(readme.splitlines()) <= 260
    for path in (
        "docs/installation.md",
        "docs/configuration.md",
        "docs/user-guide.md",
        "docs/operations.md",
        "docs/miniapp.md",
    ):
        assert (ROOT / path).is_file(), path
        assert f"]({path})" in readme, path
    assert "canonical command reference" in readme
    assert "/help scene refresh" in readme


def test_user_documentation_relative_links_resolve():
    import re

    documents = [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
            target = target.strip().split("#", 1)[0]
            if not target or "://" in target or target.startswith(("mailto:", "#")):
                continue
            resolved = (document.parent / target).resolve()
            assert resolved.exists(), f"{document.relative_to(ROOT)} -> {target}"


def test_completed_internal_planning_artifacts_are_not_shipped_as_user_docs():
    retired = (
        "docs/superpowers/plans/2026-10-02-callback-decomposition.md",
        "docs/superpowers/specs/2026-10-02-callback-decomposition.md",
        "docs/superpowers/plans/2026-10-02-imagine-character-reference-routing.md",
        "docs/superpowers/specs/2026-10-02-imagine-character-reference-routing-design.md",
        "docs/audit-repairs-2026-09-30.md",
        "docs/superpowers/plans/2026-09-26-light-novel-mode.md",
        "docs/superpowers/plans/2026-09-27-miniapp-delivery.md",
        "docs/superpowers/plans/2026-09-30-audit-repairs.md",
        "docs/superpowers/plans/2026-09-30-npc-bank-core.md",
        "docs/superpowers/plans/2026-09-30-provider-resilience.md",
        "docs/superpowers/plans/2026-10-01-memory-diagnostics.md",
        "docs/superpowers/plans/2026-10-01-miniapp-memory-monitoring.md",
        "docs/superpowers/specs/2026-09-26-light-novel-mode-design.md",
        "docs/superpowers/specs/2026-09-27-miniapp-design.md",
        "docs/superpowers/specs/2026-09-30-audit-repairs-design.md",
        "docs/superpowers/specs/2026-09-30-npc-bank-core-design.md",
        "docs/superpowers/specs/2026-09-30-provider-resilience-design.md",
        "docs/superpowers/specs/2026-10-01-memory-diagnostics-design.md",
        "docs/superpowers/specs/2026-10-01-miniapp-memory-monitoring-design.md",
    )
    assert all(not (ROOT / path).exists() for path in retired)


def test_release_signing_rotation_runbook_requires_overlap_and_revocation_steps():
    operations = (ROOT / "docs/operations.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    required = (
        "second offline signing key",
        "overlap period",
        "remove the old public key",
        "do not rewrite historical tags",
        "compromised",
    )
    for phrase in required:
        assert phrase in operations.casefold(), phrase
    assert "multiple public signer entries" in security.casefold()
    assert "private signing keys" in security.casefold()


def test_resource_leaks_cannot_escape_ci_as_unraisable_warnings():
    import tomllib

    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    filters = config["tool"]["pytest"]["ini_options"]["filterwarnings"]
    assert "error::pytest.PytestUnraisableExceptionWarning" in filters


@pytest.mark.parametrize(
    "module,name",
    [
        ("light_novel_repository", "fail_choice_generation"),
        ("response_delivery", "persist_assistant_delivery_ids"),
        ("light_novel_format", "parse_story_response"),
        ("npc_repository", "find_npc_exact"),
        ("image_generation", "handle_imagine_prompt"),
        ("update_ack", "acknowledge_pending_update"),
        ("episodic_memory", "list_episodic_memories"),
        ("character_quality", "store_character_rank"),
        ("npc_extraction", "parse_npc_extraction"),
        ("grounded_user_settings", "grounded_user_label"),
    ],
)
def test_retired_entrypoints_and_test_only_helpers_do_not_ship(module, name):
    import ast

    tree = ast.parse((ROOT / "bridge" / f"{module}.py").read_text())
    definitions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert name not in definitions
