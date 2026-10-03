# Provider Model Context Metadata Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let public-install users keep provider model lists pinned while the bridge safely discovers context-window metadata for configured models and falls back to 32K when metadata is unavailable.

**Architecture:** Extend the existing provider catalog/cache path rather than creating a new registry or scheduler. One bounded provider catalog response may update model IDs and/or metadata depending on independent flags; cached metadata is advisory, explicit YAML wins, startup schedules metadata-only refresh through the existing bounded background executor, and generation remains network-free for metadata resolution.

**Tech Stack:** Python 3.11+, pytest, PyYAML, urllib.request, existing `strict_urlopen`/provider endpoint validation, existing bounded JSON model cache, existing background thread pool.

**Spec:** `docs/superpowers/specs/2026-10-03-provider-model-context-metadata-discovery-design.md`

## Global Constraints

- Fresh installs must not ask users for context-window size.
- `discover_models: false` must remain compatible with `discover_model_metadata: true`.
- Metadata discovery alone must never add, remove, reorder, or switch routed models.
- Explicit `model_context_window_tokens` overrides provider-wide `context_window_tokens`, which overrides discovered metadata, which overrides the global fallback.
- Unknown/unavailable metadata must retain the existing 32,768-token global fallback.
- Metadata discovery must never run synchronously in story generation.
- No new cron, timer, daemon, database table, or external model registry.
- Discovery must use the existing provider credential boundary, `strict_urlopen`, URL validation, response-size limits, and bounded cache.
- Supported discovered context values remain bounded to 4,096..1,000,000 tokens.
- A failed provider/transport refresh preserves last-known metadata; a successful authoritative response clears stale discovered values for configured models that no longer publish a valid context field.

## Review Focus

1. **Catalog contains thousands of unrelated models:** metadata-only discovery must cache context only for explicitly configured model IDs and must not expand routing. Covered in Task 2.
2. **Concurrent refresh writers update different dimensions:** model-list freshness and metadata freshness must not overwrite each other or regress to older results. Covered in Task 1.
3. **Provider returns HTTP 200 but removes/invalidates a context field:** stale discovered context must be cleared so the planner does not overestimate the window. Covered in Task 2.
4. **Manually-authored legacy provider config omits the new flag:** no new catalog network call may occur solely because the feature exists; 32K fallback remains. Covered in Tasks 2 and 5.
5. **Startup refresh is slow/offline:** Telegram polling must start immediately while metadata refresh runs only in the existing bounded background executor, and shutdown must still track/drain it. Covered in Task 5.

---

### Task 1: Extend the bounded provider cache with context metadata

**Files:**
- Modify: `bridge/provider_catalog_cache.py`
- Test: `tests/test_provider_maintenance.py`

**Interfaces:**
- Produces: `context_window_value(raw: object) -> int | None`
- Produces: `model_context_windows(raw: object) -> dict[str, int]`
- Extends cache entries with `model_context_window_tokens`, `metadata_refreshed_at`, `metadata_last_attempt_at`, and `metadata_last_error`.
- Preserves existing `read_model_cache(*, app_settings: AppSettings) -> dict[str, dict[str, object]]` and `update_model_cache(updates, *, app_settings) -> None` signatures.

- [ ] **Step 1: Write failing cache-validation tests**

Add tests to `tests/test_provider_maintenance.py`:

```python
def test_cache_sanitizes_model_context_metadata_and_metadata_timestamps(configured): ...


def test_older_metadata_cache_writer_does_not_replace_newer_metadata(configured): ...


def test_model_and_metadata_cache_writers_do_not_clobber_each_other(configured): ...
```

Assertions must pin:
- valid values 4,096 and 1,000,000 survive;
- booleans, non-numeric values, values below 4,096, and values above 1,000,000 are dropped;
- invalid model IDs are dropped using the existing model-ID rules;
- an incoming older `metadata_last_attempt_at` cannot replace a newer metadata map;
- an older model-list write cannot roll back newer metadata and vice versa.

- [ ] **Step 2: Run the cache tests and verify failure**

Run:

```bash
pytest -q tests/test_provider_maintenance.py -k "cache_sanitizes_model_context or older_metadata_cache_writer or model_and_metadata_cache_writers"
```

Expected: FAIL because metadata fields/helpers are not yet preserved.

- [ ] **Step 3: Implement cache metadata sanitization and dimension-aware merge**

In `bridge/provider_catalog_cache.py`:
- add `context_window_value(raw: object) -> int | None` using the exact 4,096..1,000,000 bounds;
- add `model_context_windows(raw: object) -> dict[str, int]` using existing model-ID validation and a hard maximum of 500 entries, matching the installer/provider configured-model limit;
- teach `read_model_cache()` to sanitize metadata fields and metadata timestamps/errors;
- teach `update_model_cache()` to compare `last_attempt_at` for model-list fields and `metadata_last_attempt_at` for metadata fields independently so concurrent writers cannot regress either dimension.

Do not change cache permissions, atomic write behavior, or `MAX_CACHE_BYTES`.

- [ ] **Step 4: Run focused and existing cache tests**

Run:

```bash
pytest -q tests/test_provider_maintenance.py -k "cache or concurrent_cache or older_cache"
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/provider_catalog_cache.py tests/test_provider_maintenance.py
git commit -m "feat: cache provider context metadata safely"
```

---

### Task 2: Discover configured-model context metadata without changing routing

**Files:**
- Modify: `bridge/provider_discovery.py`
- Modify: `bridge/provider_catalog_cache.py` only if a small shared helper adjustment is required by the tests
- Test: `tests/test_provider_maintenance.py`
- Test: `tests/test_provider_ingestion_safety.py`

**Interfaces:**
- Consumes: `context_window_value()` and `model_context_windows()` from Task 1.
- Produces: `refresh_model_catalog(force: bool = False, provider_id: str | None = None, *, metadata_only: bool = False, app_settings: AppSettings) -> tuple[dict, int, int]`.
- Produces internal `_catalog_endpoint(spec: Mapping[str, object], *, app_settings: AppSettings) -> str`.
- Existing callers that omit `metadata_only` retain current model-discovery behavior.

- [ ] **Step 1: Write failing metadata-discovery tests**

Add to `tests/test_provider_maintenance.py`:

```python
def test_metadata_only_refresh_caches_context_without_adding_remote_models(configured, monkeypatch): ...


def test_combined_model_and_metadata_discovery_reuses_one_catalog_response(configured, monkeypatch): ...


def test_custom_models_endpoint_is_used_for_refresh(configured, monkeypatch): ...


def test_successful_metadata_refresh_clears_stale_context_for_missing_or_invalid_model(configured, monkeypatch): ...


def test_failed_metadata_refresh_preserves_last_good_context(configured, monkeypatch): ...


def test_provider_without_metadata_flag_does_not_request_metadata(configured, monkeypatch): ...


def test_failed_metadata_refresh_is_throttled_by_metadata_attempt_timestamp(configured, monkeypatch): ...
```

Pin these exact behaviors:
- metadata-only provider has `discover_models: false`, `discover_model_metadata: true`, configured `models: ["seed"]`; a response containing `seed` plus `remote` caches only `seed` context and routing remains `["seed"]`;
- when both flags are true, exactly one HTTP request serves both ID and metadata updates;
- `models_endpoint` replaces the derived `<api_endpoint>/models` URL;
- a successful response that omits `seed` or gives it invalid context removes stale discovered `seed` context;
- timeout/429/503 preserves prior discovered context;
- no flag means no metadata request;
- metadata retry throttling uses `metadata_last_attempt_at` independently of model-list `last_attempt_at`.

- [ ] **Step 2: Write failing endpoint-security tests**

In `tests/test_provider_ingestion_safety.py`, add coverage that an explicit `models_endpoint`:
- passes through the same endpoint validation path;
- rejects credentials embedded in the URL;
- cannot bypass the configured host/network restrictions.

- [ ] **Step 3: Run discovery/security tests and verify failure**

Run:

```bash
pytest -q tests/test_provider_maintenance.py tests/test_provider_ingestion_safety.py -k "metadata or models_endpoint"
```

Expected: FAIL because metadata-only discovery, `models_endpoint`, and `metadata_only` are not implemented.

- [ ] **Step 4: Implement one-request dual-purpose discovery**

In `bridge/provider_discovery.py`:
- add `_catalog_endpoint()`: use validated `models_endpoint` when configured; otherwise derive `_endpoint(spec) + "/models"`;
- extend refresh eligibility to providers with either discovery flag;
- when `metadata_only=True`, skip providers without `discover_model_metadata: true` and never alter discovered model IDs;
- parse only the fixed top-level context field order from the spec: `context_length`, `context_window_tokens`, `context_window`, `max_context_length`, `max_position_embeddings`;
- cache metadata only for explicitly configured model IDs;
- when a successful response is authoritative, replace the discovered metadata map for configured models with only valid values from that response, thereby clearing stale missing/invalid values;
- on transport/provider failure, preserve the previous metadata map and record metadata-specific attempt/error state;
- preserve the existing `(config, refreshed, failed)` return contract and count each provider once per refresh call;
- update catalog health probing to use `_catalog_endpoint()` for non-inference catalog probes.

Do not introduce recursive metadata scanning or external registry calls.

- [ ] **Step 5: Run provider discovery and security suites**

Run:

```bash
pytest -q tests/test_provider_maintenance.py tests/test_provider_ingestion_safety.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/provider_discovery.py bridge/provider_catalog_cache.py tests/test_provider_maintenance.py tests/test_provider_ingestion_safety.py
git commit -m "feat: discover provider context metadata independently"
```

---

### Task 3: Feed discovered metadata into context planning with strict precedence

**Files:**
- Modify: `bridge/provider_catalog.py`
- Modify: `bridge/context_diagnostics.py`
- Test: `tests/test_context_window_hardening.py`

**Interfaces:**
- Consumes metadata cache fields from Task 1.
- Produces existing `context_metadata_for_model(model_selection: str, *, app_settings: AppSettings) -> dict[str, object]` with new source value `"discovered-provider-model"`.
- Existing explicit source values `"provider-model"`, `"provider"`, and fallback `"global-fallback"` remain unchanged.

- [ ] **Step 1: Write failing context-precedence tests**

Add to `tests/test_context_window_hardening.py`:

```python
def test_discovered_context_metadata_overrides_global_fallback(self): ...


def test_explicit_provider_context_beats_discovered_metadata(self): ...


def test_explicit_model_context_beats_provider_and_discovered_metadata(self): ...


def test_discovered_metadata_is_ignored_when_flag_off_or_model_unconfigured(self): ...
```

Assertions must pin:
- discovered configured-model value resolves above the 32K global fallback with source `discovered-provider-model`;
- provider-wide explicit value wins over discovered metadata;
- per-model explicit value wins over provider-wide and discovered metadata;
- cached metadata cannot authorize or size an unconfigured model;
- cache is ignored when `discover_model_metadata` is not true.

- [ ] **Step 2: Add the new diagnostic source to persisted snapshot validation**

Extend the existing context diagnostic test or `test_context_window_hardening.py` so `context_diagnostics_snapshot()` accepts `discovered-provider-model` instead of discarding it back to the live profile source.

- [ ] **Step 3: Run context tests and verify failure**

Run:

```bash
pytest -q tests/test_context_window_hardening.py
```

Expected: FAIL on discovered-cache precedence/source assertions.

- [ ] **Step 4: Implement cache-aware context resolution**

In `bridge/provider_catalog.py`:
- read the provider's sanitized cache only when `discover_model_metadata is True`;
- only consider a cached model context when the model is explicitly in the provider's configured `models:` list;
- apply precedence exactly: explicit model → explicit provider → discovered configured-model → no metadata;
- return source `discovered-provider-model` only for the discovered case.

In `bridge/context_diagnostics.py`, allow `discovered-provider-model` in the saved-source whitelist.

Do not change the compaction algorithm, 2% safety margin, output reserve, or global 32K fallback.

- [ ] **Step 5: Run context and compaction regressions**

Run:

```bash
pytest -q tests/test_context_window_hardening.py tests/test_context_compaction.py tests/test_codex_context_variants.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/provider_catalog.py bridge/context_diagnostics.py tests/test_context_window_hardening.py
git commit -m "feat: resolve discovered model context safely"
```

---

### Task 4: Make fresh installs zero-knowledge for model context

**Files:**
- Modify: `bridge/install_support.py`
- Modify: `config/providers.example.yaml`
- Modify: `docs/configuration.md`
- Modify: `docs/user-guide.md`
- Test: `tests/test_installer.py`
- Test: `tests/test_install_discovery.py`
- Test: `tests/test_installation_docs.py`

**Interfaces:**
- Consumes the provider schema from Tasks 2–3.
- Installer-generated custom providers add `discover_model_metadata: true` while retaining `discover_models: false`.

- [ ] **Step 1: Write failing installer tests**

Update `test_provider_catalog_can_be_prepared_using_only_env_values` in `tests/test_installer.py` to parse the generated YAML and assert:

```python
assert provider["discover_models"] is False
assert provider["discover_model_metadata"] is True
```

Add a test in `tests/test_install_discovery.py` that records all `input_fn` prompts during successful guided setup and asserts no prompt contains `context` or asks for token/window size.

- [ ] **Step 2: Write failing documentation contract tests**

In `tests/test_installation_docs.py`, assert public docs:
- state that context metadata may be discovered automatically;
- state that explicit YAML wins;
- state that unknown metadata falls back to 32K;
- distinguish `discover_models` from `discover_model_metadata`.

- [ ] **Step 3: Run installer/docs tests and verify failure**

Run:

```bash
pytest -q tests/test_installer.py tests/test_install_discovery.py tests/test_installation_docs.py
```

Expected: FAIL because generated YAML/docs do not yet include metadata discovery.

- [ ] **Step 4: Enable metadata discovery in generated custom-provider YAML**

In `bridge/install_support.py::_provider_document()`, add exactly:

```yaml
discover_models: false
discover_model_metadata: true
```

Do not add an installer question or context environment variable.

- [ ] **Step 5: Update provider example and public docs**

Document:
- `discover_models` controls whether returned IDs can expand the model picker/router;
- `discover_model_metadata` only enriches explicitly configured models;
- optional `models_endpoint`;
- exact precedence chain;
- 32K conservative fallback;
- metadata refresh is best-effort and generation does not query model metadata.

- [ ] **Step 6: Run installer/docs suites**

Run:

```bash
pytest -q tests/test_installer.py tests/test_install_discovery.py tests/test_installation_docs.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bridge/install_support.py config/providers.example.yaml docs/configuration.md docs/user-guide.md tests/test_installer.py tests/test_install_discovery.py tests/test_installation_docs.py
git commit -m "feat: enable zero-knowledge context discovery on install"
```

---

### Task 5: Schedule non-blocking startup metadata refresh and broaden provider UI wording

**Files:**
- Modify: `bridge/composition.py`
- Modify: `bridge/main.py`
- Modify: `bridge/runtime_lifecycle.py`
- Modify: `bridge/provider_panels.py`
- Modify: `bridge/provider_callbacks.py`
- Modify: `bridge/help_details.json`
- Test: `tests/test_runtime_entrypoint_invariants.py`
- Test: `tests/test_composition.py`
- Test: `tests/test_job_service_workers.py`
- Test: `tests/test_provider_maintenance.py`
- Test: `tests/test_provider_panel_contract.py`
- Test: `tests/test_provider_diagnostics_panels.py`

**Interfaces:**
- Consumes: `refresh_model_catalog(..., metadata_only=True, app_settings=config)` from Task 2.
- Extends `BackgroundRuntime` with `submit: Callable[..., bool]`.
- Wires `bridge.background.submit_background` into `BackgroundRuntime.submit`.
- Startup background label: `provider_catalog_refresh`.

- [ ] **Step 1: Write failing runtime scheduling test**

In `tests/test_runtime_entrypoint_invariants.py`, extend the runtime fixture with a `background.submit` recorder and add:

```python
def test_runtime_schedules_nonblocking_metadata_refresh_before_first_poll(monkeypatch, tmp_path): ...
```

Pin:
- exactly one startup submission uses label `provider_catalog_refresh`;
- the callable is invoked with `metadata_only=True` when the test executor chooses to run it;
- Telegram's first `getUpdates` call occurs without waiting for refresh completion;
- shutdown still goes through the existing background executor lifecycle.

Add a second case proving a legacy provider without `discover_model_metadata` causes no network request when the submitted refresh function runs.

- [ ] **Step 2: Write failing UI wording/diagnostic tests**

Update provider panel/diagnostic tests to assert:
- button text is `Refresh provider catalog` (emoji prefix may remain);
- callback IDs remain unchanged (`provider:refresh` and `provider:maint:refresh:...`);
- completion text says `Provider catalog refreshed`, not `Model catalog refreshed`;
- empty-catalog guidance mentions configured models and provider-catalog refresh without implying metadata discovery can add models;
- prompt diagnostics display `discovered-provider-model` as the context source.

- [ ] **Step 3: Run runtime/UI tests and verify failure**

Run:

```bash
pytest -q tests/test_runtime_entrypoint_invariants.py tests/test_composition.py tests/test_job_service_workers.py tests/test_provider_panel_contract.py tests/test_provider_diagnostics_panels.py tests/test_provider_maintenance.py
```

Expected: FAIL because background runtime lacks general submission and UI strings still say model refresh.

- [ ] **Step 4: Wire the existing general background executor into composition**

In `bridge/composition.py` add:

```python
submit: Callable[..., bool]
```

to `BackgroundRuntime`.

In `bridge/main.py` import `submit_background` and pass it as `BackgroundRuntime.submit`.

Update every direct `BackgroundRuntime(...)` construction in `tests/test_composition.py` and `tests/test_job_service_workers.py` with a deterministic no-op/recorder `submit` callable; do not make the production field optional just to avoid updating fixtures.

Do not create a new executor or thread.

- [ ] **Step 5: Schedule metadata-only refresh during runtime startup**

In `bridge/runtime_lifecycle.py`, after background infrastructure is registered and before entering the polling loop, call:

```python
services.background.submit(
    "provider_catalog_refresh",
    refresh_model_catalog,
    metadata_only=True,
    app_settings=config,
)
```

Import `refresh_model_catalog` from `bridge.provider_discovery`.

This call must return immediately; the executor owns the network work and shutdown tracking.

- [ ] **Step 6: Broaden manual refresh wording without changing callback contracts**

In provider panels/callbacks/help:
- replace user-visible `Refresh models` with `Refresh provider catalog`;
- replace `Model catalog refreshed` with `Provider catalog refreshed`;
- keep callback data/action names unchanged for compatibility;
- do not change provider selection or routing behavior.

- [ ] **Step 7: Run runtime/UI/provider suites**

Run:

```bash
pytest -q tests/test_runtime_entrypoint_invariants.py tests/test_provider_panel_contract.py tests/test_provider_diagnostics_panels.py tests/test_provider_maintenance.py
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add bridge/composition.py bridge/main.py bridge/runtime_lifecycle.py bridge/provider_panels.py bridge/provider_callbacks.py bridge/help_details.json tests/test_runtime_entrypoint_invariants.py tests/test_composition.py tests/test_job_service_workers.py tests/test_provider_maintenance.py tests/test_provider_panel_contract.py tests/test_provider_diagnostics_panels.py
git commit -m "feat: refresh provider metadata nonblocking"
```

---

### Task 6: Run full regression, static checks, and verify no scheduler/routing regression

**Files:**
- Modify only if verification exposes a defect in the preceding tasks.
- Test: full repository suite and project-native quality commands.

**Interfaces:**
- Consumes all prior task interfaces.
- Produces a branch that satisfies the approved spec without adding a scheduler or hidden model-routing source.

- [ ] **Step 1: Run the focused feature matrix**

Run:

```bash
pytest -q   tests/test_provider_maintenance.py   tests/test_provider_ingestion_safety.py   tests/test_context_window_hardening.py   tests/test_context_compaction.py   tests/test_installer.py   tests/test_install_discovery.py   tests/test_installation_docs.py   tests/test_runtime_entrypoint_invariants.py   tests/test_provider_panel_contract.py   tests/test_provider_diagnostics_panels.py
```

Expected: PASS.

- [ ] **Step 2: Run the complete Python test suite**

Run:

```bash
pytest -q
```

Expected: PASS with no new failures.

- [ ] **Step 3: Run repository-native lint/security/static checks**

Inspect the current CI workflow/package configuration and run the same local commands CI uses for Python formatting/lint/security checks. Do not invent alternate tools or versions.

Expected: all checks PASS.

- [ ] **Step 4: Verify the no-scheduler and no-generation-I/O invariants**

Run source-level checks/tests confirming:
- no new systemd timer/cron/scheduler file was added;
- generation/context-compaction modules do not call `refresh_model_catalog` or perform metadata HTTP requests;
- `merge_model_catalog()` still adds cached model IDs only when `discover_models is True`;
- metadata-only cached model IDs cannot appear in the model picker/router.

Expected: all assertions PASS.

- [ ] **Step 5: Verify a representative zero-knowledge install end to end**

Using a temporary test home/provider fixture:
- generate provider config through the installer;
- confirm `discover_models: false`, `discover_model_metadata: true`;
- feed a synthetic `/models` response with a 262,144-token configured model and an unrelated remote model;
- confirm context profile resolves 262,144 with source `discovered-provider-model`;
- confirm routing contains only the configured model;
- repeat with no context metadata and confirm 32,768 fallback.

Expected: all assertions PASS.

- [ ] **Step 6: Commit any verification-only fixes**

If Step 1–5 required code changes, commit them separately:

```bash
git add <only files changed by verification fixes>
git commit -m "fix: close provider metadata discovery regressions"
```

If no changes were required, do not create an empty commit.
