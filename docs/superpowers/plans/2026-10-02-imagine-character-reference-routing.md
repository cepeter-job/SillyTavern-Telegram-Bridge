# Character-Reference /imagine Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/imagine` use the active native SillyTavern character PNG as a visual identity reference when the selected/Auto image route supports references, while preserving existing text-only generation, manual model authority, bounded provider calls, and image-only Telegram delivery.

**Architecture:** Add a focused image-routing module that parses capability metadata, persists/validates the Auto sentinel, and resolves one concrete route without touching files or the network. Add a focused native-reference loader that resolves the active character PNG through existing safe-path rules and validates the PNG structure in memory. Keep text-generation and image-edit HTTP transports plus Current Scene/Custom Prompt orchestration in `bridge/image_generation.py`; panel/callback code consumes the routing API rather than reimplementing selection policy.

**Tech Stack:** Python 3.11+, stdlib `urllib`/multipart encoding/`zlib`, PyYAML, SQLite metadata helpers, existing Telegram helpers, unittest/pytest, Ruff, repository static analysis.

**Spec:** `docs/superpowers/specs/2026-10-02-imagine-character-reference-routing-design.md`

## Global Constraints

- SillyTavern remains the source of truth for character cards; do not create persistent bridge-owned portrait copies.
- Existing catalogs that only declare string `image_models` must behave unchanged.
- Auto targets are catalog-level, fully-qualified `provider::model` selections.
- Manual concrete model selection always wins; provider failure must never trigger a second paid generation on another model.
- Reference-capable generation sends at most one active-character image in this feature.
- Keep existing provider endpoint/SSRF validation and credential/header policy.
- Keep existing generated-image and JSON byte ceilings; reference upload uses the stricter bridge/provider ceiling.
- Do not log reference bytes, base64/data URLs, credentials, raw sensitive upstream bodies, or local character paths.
- Current Scene remains a visualization of committed story state and must not mutate/advance the transcript.
- Telegram final output remains image-only; no source/revised prompt caption.
- No new runtime dependency is added for PNG validation.
- Step Image Edit 2 uses a 512-character prompt ceiling; Z Image Turbo keeps its existing 1,200-character ceiling.
- The first reference transport is OpenAI-compatible multipart `POST /images/edits` (NanoGPT also exposes `/api/v1/images/edits`).
- Scope is one active standard-session character reference; group/multi-character reference generation is out of scope.

## File Structure

- Create `bridge/image_routing.py` — provider image catalog parsing, capabilities, Auto validation/resolution, session image model/size metadata, prompt-limit lookup.
- Create `bridge/image_reference.py` — safe native character PNG resolution and bounded structural validation; returns in-memory reference bytes only.
- Modify `bridge/image_generation.py` — retain text transport, add edit transport, build identity-preserving reference prompts, route Current Scene/Custom Prompt, and centralize progress cleanup.
- Modify `bridge/image_panels.py` — expose Auto, show resolved route/reference status, and avoid promising unsupported edit sizes.
- Modify `bridge/feature_callbacks.py` — consume routing APIs and pass full session context for route-aware Current Scene/Custom Prompt handling.
- Modify `bridge/text_action_input.py` — submit custom image prompts through session-aware routing rather than directly calling a concrete text-only transport.
- Modify `config/providers.example.yaml`, `docs/configuration.md`, `docs/user-guide.md`, `bridge/help_details.json`, and `CHANGELOG.md` — document Auto/reference routing and config fields.
- Modify `tests/test_governance.py` — pin public configuration documentation.
- Create `tests/test_image_routing.py` — routing/capability/session-selection unit tests.
- Create `tests/test_image_reference.py` — safe-path and PNG-reference validation tests.
- Modify `tests/test_image_generation.py` — edit transport, orchestration, panel/callback, and progress lifecycle tests.
- Modify `tests/test_provider_ingestion_safety.py` — reference credential/endpoint/byte-bound security tests.

## Review Focus

These failure modes are easy to miss and each is explicitly pinned to a task below:

1. **Character file changes/disappears between panel display and generation:** the generation-time loader re-resolves the current session character and Auto uses text fallback; manual reference selection fails without a provider call.
2. **Duplicate model IDs across providers:** unqualified Auto targets are rejected rather than guessed; fully-qualified targets resolve exactly one provider.
3. **Oversized or corrupt character PNG:** Auto treats it as unavailable and may use only the configured Auto text target; manual reference mode fails before network I/O.
4. **Provider image-edit failure after a progress message is sent:** the progress message is cleaned up and no fallback generation request is issued.
5. **Telegram progress deletion itself fails after successful delivery:** the generated image remains successful and deletion failure is logged/best-effort rather than masking delivery.

---

### Task 1: Capability-aware image routing and Auto session selection

**Files:**
- Create: `bridge/image_routing.py`
- Create: `tests/test_image_routing.py`
- Modify: `bridge/image_panels.py`
- Modify: `bridge/feature_callbacks.py`
- Modify: `bridge/text_action_input.py`
- Modify: `bridge/image_generation.py` (remove/move the provider/session-selection helpers now owned by routing)

**Interfaces:**
- Produces: `AUTO_IMAGE_SELECTION = "auto"`.
- Produces: `ImageRoute` frozen dataclass with `selection: str`, `provider_id: str`, `model: str`, `transport: Literal["text", "reference"]`, `edit_route: str | None`, and `spec: dict[str, object]`.
- Produces: `image_model_options(*, app_settings: AppSettings, include_auto: bool = True) -> tuple[tuple[str, str], ...]`.
- Produces: `session_image_settings(db, chat_id: str, session_id: str, *, app_settings: AppSettings) -> tuple[str, str]`.
- Produces: `set_session_image_model(db, chat_id: str, session_id: str, selection: str, *, app_settings: AppSettings) -> str`.
- Produces: `set_session_image_size(db, chat_id: str, session_id: str, size: str) -> str`.
- Produces: `reset_session_image_settings(db, chat_id: str, session_id: str) -> None`.
- Produces: `resolve_image_route(selection: str, *, reference_available: bool, app_settings: AppSettings) -> ImageRoute`.
- Produces: `image_prompt_max_chars(route: ImageRoute) -> int`.
- Consumes: existing SQLite `get_meta`/`set_meta`, provider YAML path from `AppSettings`, existing `IMAGE_SIZE_PRESETS` values moved with image settings.

- [ ] **Step 1: Write routing tests for backward compatibility and Auto selection**

Add tests asserting:

```python
def test_legacy_image_catalog_without_capabilities_keeps_first_concrete_default(): ...
def test_valid_auto_defaults_to_auto_and_reference_target_when_reference_exists(): ...
def test_auto_without_reference_resolves_only_configured_text_target(): ...
def test_manual_text_selection_stays_text_with_reference_available(): ...
def test_manual_reference_selection_requires_reference(): ...
```

Assertions must prove legacy config still resolves its first concrete model, Auto resolves exact fully-qualified targets, and manual routes never silently switch models.

- [ ] **Step 2: Run the new routing tests and verify they fail**

Run: `python -m pytest tests/test_image_routing.py -q`
Expected: FAIL because `bridge.image_routing` and Auto capability handling do not exist.

- [ ] **Step 3: Implement catalog parsing, session persistence, and route resolution**

In `bridge/image_routing.py`, parse top-level `image_auto`, provider `image_models`, optional `image_model_capabilities`, and existing image endpoint/key fields. Treat absent capability metadata as `mode: text`. Validate Auto targets as fully-qualified and declared; `reference_model` must be `reference`/`both`, `text_model` must be `text`/`both`.

Default/reset selection is `auto` only when both configured Auto targets validate; otherwise preserve the existing concrete-first default. A stored invalid selection is discarded and re-resolved by the same rule.

- [ ] **Step 4: Add routing tests for ambiguous/invalid provider metadata**

Add:

```python
def test_unqualified_auto_target_is_rejected_in_multi_provider_catalog(): ...
def test_auto_reference_target_with_text_only_capability_fails_closed(): ...
def test_auto_target_for_undeclared_model_is_rejected(): ...
def test_duplicate_model_ids_resolve_by_fully_qualified_provider_selection(): ...
def test_missing_auto_reference_target_falls_back_only_to_auto_text_target(): ...
```

The last test covers a target removed after a session stored Auto; capability mismatch itself is a configuration error, not a reason to guess another reference model.

- [ ] **Step 5: Run routing tests**

Run: `python -m pytest tests/test_image_routing.py -q`
Expected: PASS.

- [ ] **Step 6: Update existing imports to consume `bridge.image_routing` without behavior changes yet**

Update `image_panels.py`, `feature_callbacks.py`, `text_action_input.py`, and `image_generation.py` to import image settings/routing functions from the new owner. Keep text-only behavior passing while later tasks add reference transport.

- [ ] **Step 7: Run existing image tests for regression**

Run: `python -m pytest tests/test_image_generation.py -q`
Expected: PASS for existing text-only cases.

- [ ] **Step 8: Commit**

```bash
git add bridge/image_routing.py bridge/image_generation.py bridge/image_panels.py bridge/feature_callbacks.py bridge/text_action_input.py tests/test_image_routing.py
git commit -m "feat: add capability-aware image routing"
```

### Task 2: Safe in-memory native character PNG references

**Files:**
- Create: `bridge/image_reference.py`
- Create: `tests/test_image_reference.py`
- Modify: `bridge/limits.py` only if a named reference-upload ceiling constant is needed; its value must not exceed existing `IMAGE_MAX_BYTES`.

**Interfaces:**
- Produces: `ImageReference` frozen dataclass with `data: bytes`, `mime_type: str`, and `filename: str`.
- Produces: `load_character_reference(character_file: str, *, app_settings: AppSettings) -> ImageReference | None`.
- Consumes: `safe_character_path()`, existing image-byte ceiling, stdlib `struct`/`zlib`.

- [ ] **Step 1: Write native-reference tests**

Add tests asserting:

```python
def test_valid_native_character_png_loads_reference_without_reading_chara_metadata(): ...
def test_valid_png_with_invalid_chara_metadata_is_still_a_reference(): ...
def test_traversal_character_name_returns_no_reference(): ...
def test_missing_character_file_returns_no_reference(): ...
def test_corrupt_or_truncated_png_returns_no_reference(): ...
def test_png_with_corrupt_chunk_crc_returns_no_reference(): ...
def test_oversized_reference_returns_no_reference_without_persisting_copy(): ...
```

Fixtures should use a minimal structurally valid PNG and separately corrupt the SillyTavern metadata chunk vs. image/container chunks. Assert no new file appears outside the fixture character directory.

- [ ] **Step 2: Run tests and verify they fail**

Run: `python -m pytest tests/test_image_reference.py -q`
Expected: FAIL because the loader does not exist.

- [ ] **Step 3: Implement bounded PNG reference loading**

Resolve only through `safe_character_path`. Read at most the allowed ceiling plus one byte. Validate PNG signature, required `IHDR`/`IDAT`/`IEND` structure, bounded chunk lengths, and CRCs using stdlib; do not require or parse the `chara` tEXt metadata. Return original in-memory PNG bytes and `image/png`; never write a derivative file.

- [ ] **Step 4: Add review-focus race test**

Add:

```python
def test_reference_loader_rechecks_file_at_generation_time_after_file_removed(): ...
```

The loader must return `None` when the path was previously valid but no longer exists; callers decide Auto fallback vs. manual failure.

- [ ] **Step 5: Run reference tests**

Run: `python -m pytest tests/test_image_reference.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/image_reference.py bridge/limits.py tests/test_image_reference.py
git commit -m "feat: load safe character image references"
```

### Task 3: OpenAI-compatible image-edit transport

**Files:**
- Modify: `bridge/image_generation.py`
- Modify: `tests/test_image_generation.py`
- Modify: `tests/test_provider_ingestion_safety.py`

**Interfaces:**
- Consumes: `ImageRoute`, `ImageReference`.
- Produces: `edit_image(route: ImageRoute, prompt: str, reference: ImageReference, size: str, *, app_settings: AppSettings) -> tuple[bytes, str, str]`.
- Produces: internal `_image_edit_endpoint(spec: dict, *, app_settings: AppSettings) -> str`.
- Keeps: existing `generate_image(...)` text-generation behavior and return contract.

- [ ] **Step 1: Write failing multipart edit-contract tests**

Add tests proving `edit_image`:

- sends one multipart `image` file plus `model`, `prompt`, and `n=1`;
- uses explicit `image_edit_endpoint` when configured;
- otherwise derives `<api_endpoint>/images/edits` without rewriting a custom `image_endpoint`;
- uses the same Authorization/extra-header policy as text generation;
- accepts the existing base64 response contract and returns the exact `provider::model` selection.

Run: `python -m pytest tests/test_image_generation.py -k "edit_image" -q`
Expected: FAIL because edit transport does not exist.

- [ ] **Step 2: Implement the edit endpoint and bounded multipart request**

Use stdlib multipart construction. The request must be one provider call, enforce the reference byte ceiling before network I/O, validate the endpoint with existing network-security helpers, and apply the existing response JSON/generated-image ceilings.

For `step-image-edit-2`, pass the exact session size only when it is a supported bridge/provider overlap (`1024x1024`); omit unsupported Landscape/Portrait bridge sizes so the provider uses its supported automatic/default edit sizing.

- [ ] **Step 3: Add prompt-limit and unsupported-size tests**

Add:

```python
def test_step_image_edit_2_rejects_prompt_over_512_before_request(): ...
def test_step_edit_square_passes_supported_size(): ...
def test_step_edit_unsupported_bridge_landscape_omits_size_instead_of_lying(): ...
```

Keep the existing Z Image Turbo 1,200-character regression test unchanged.

- [ ] **Step 4: Add provider-ingestion safety tests**

In `tests/test_provider_ingestion_safety.py`, add tests proving:

- missing explicit image credential fails before edit network I/O;
- edit endpoint host is subject to the same allowlist/private-host validation;
- oversized reference bytes fail before network I/O;
- oversized JSON/decoded output remains bounded for edit responses;
- raw reference bytes/credential values are not included in raised user-facing `ValueError` text.

- [ ] **Step 5: Run transport/security tests**

Run: `python -m pytest tests/test_image_generation.py tests/test_provider_ingestion_safety.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/image_generation.py tests/test_image_generation.py tests/test_provider_ingestion_safety.py
git commit -m "feat: add bounded image edit transport"
```

### Task 4: Route Current Scene and Custom Prompt through character references

**Files:**
- Modify: `bridge/image_generation.py`
- Modify: `bridge/feature_callbacks.py`
- Modify: `bridge/text_action_input.py`
- Modify: `tests/test_image_generation.py`

**Interfaces:**
- Produces: `handle_imagine_scene(db, token: str, chat_id: str, session: dict[str, str], *, provider_port: ProviderPort, app_settings: AppSettings) -> None` (remove the required pre-parsed `fields` argument).
- Produces: `handle_imagine_custom_prompt(db, token: str, chat_id: str, session: dict[str, str], prompt: str, *, app_settings: AppSettings) -> None`.
- Produces: internal `_reference_prompt(scene_prompt: str, *, max_chars: int) -> str` with the spec's identity-preservation semantics.
- Consumes: `load_character_reference`, `resolve_image_route`, `generate_image`, `edit_image`.

- [ ] **Step 1: Write failing route-orchestration tests**

Add tests for:

```python
def test_auto_scene_with_valid_character_png_uses_reference_edit_model(): ...
def test_auto_scene_without_character_png_uses_configured_text_model(): ...
def test_auto_scene_with_corrupt_png_uses_text_model(): ...
def test_manual_text_scene_ignores_available_reference(): ...
def test_manual_reference_scene_without_reference_fails_before_network(): ...
def test_character_switch_changes_reference_on_next_imagine(): ...
```

Capture provider requests to prove exactly one request is made and which model/reference bytes were used.

- [ ] **Step 2: Implement generation-time route resolution**

At the start of each Current Scene/Custom Prompt generation, re-load the active session character reference, then resolve Auto/manual routing. Do not cache reference bytes across requests.

For Current Scene, load card text only as optional visual context. If `card_fields_from_file` fails because SillyTavern metadata is corrupt, continue with a fallback name from the card filename and empty description so valid PNG pixels can still serve as the reference.

- [ ] **Step 3: Implement bounded identity-preserving reference prompt construction**

Prefix reference routes with the approved identity instruction and reserve its exact length before asking the Utility model for Current Scene prompt content. Assert the final Step Image Edit 2 prompt length is `<= 512`.

Custom Prompt uses the same reference prefix when Auto/manual reference routing selects the edit transport; if the user's custom text plus prefix exceeds the route limit, return the existing bounded prompt-length error without making a provider request.

- [ ] **Step 4: Route custom prompt input through session-aware orchestration**

Change `text_action_input.py` so `action == "imagine"` calls `handle_imagine_custom_prompt(..., session, value, ...)` rather than directly resolving a selection and calling the text-only handler.

Change `feature_callbacks._imagine_custom` to compute its displayed maximum through the current session's resolved route/reference availability, so Auto + active reference advertises the actual available custom-prompt budget instead of 4,000 characters.

- [ ] **Step 5: Add no-double-spend failure tests**

Add:

```python
def test_reference_provider_failure_does_not_call_auto_text_fallback(): ...
def test_manual_reference_provider_failure_does_not_call_any_other_model(): ...
```

Provider exceptions after request dispatch must propagate to the existing bounded callback error path; routing fallback happens only before a provider request when Auto reference is unavailable/misconfigured according to the spec.

- [ ] **Step 6: Run image orchestration tests**

Run: `python -m pytest tests/test_image_generation.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add bridge/image_generation.py bridge/feature_callbacks.py bridge/text_action_input.py tests/test_image_generation.py
git commit -m "feat: use character references for imagine"
```

### Task 5: Auto/reference panel UX and progress lifecycle

**Files:**
- Modify: `bridge/image_panels.py`
- Modify: `bridge/feature_callbacks.py`
- Modify: `bridge/image_generation.py`
- Modify: `tests/test_image_generation.py`

**Interfaces:**
- Consumes: `AUTO_IMAGE_SELECTION`, `session_image_settings`, `resolve_image_route`, `load_character_reference`.
- Keeps callback payload namespace `imagine:...` and existing dynamic callback-token ownership.

- [ ] **Step 1: Write failing panel tests**

Add assertions that:

- the model panel shows **Auto** and marks it selected when stored;
- options/current panel shows the resolved concrete model for Auto, e.g. `Auto → step-image-edit-2 · character reference`;
- manual text models remain visibly manual;
- a reference route with an unsupported preferred size displays provider-controlled/Auto sizing instead of promising the stored unsupported dimensions;
- no image models configured still shows existing setup guidance.

- [ ] **Step 2: Implement panel text and Auto selection**

Keep `auto` as a normal dynamically-tokenized model choice so stale/actor/session callback protections remain unchanged. Do not add a second callback system.

Rename the user-facing size line to a preference when appropriate and show `Auto (reference model)` for edit routes that cannot honor the selected exact bridge preset.

- [ ] **Step 3: Write progress cleanup tests**

Add:

```python
def test_imagine_provider_failure_removes_progress_message(): ...
def test_imagine_delivery_failure_removes_progress_message(): ...
def test_progress_delete_failure_does_not_mask_successful_image_delivery(): ...
def test_imagine_success_still_sends_image_without_caption(): ...
```

- [ ] **Step 4: Centralize progress cleanup with `try/finally`**

Create progress immediately before provider I/O. Attempt deletion in `finally` for provider or delivery failure and after success. Deletion remains best-effort/logged; it must not override the original provider/delivery error or a successful image delivery.

- [ ] **Step 5: Run panel/progress tests**

Run: `python -m pytest tests/test_image_generation.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/image_panels.py bridge/feature_callbacks.py bridge/image_generation.py tests/test_image_generation.py
git commit -m "feat: expose auto reference image routing"
```

### Task 6: Public configuration, help, and governance

**Files:**
- Modify: `config/providers.example.yaml`
- Modify: `docs/configuration.md`
- Modify: `docs/user-guide.md`
- Modify: `bridge/help_details.json`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_governance.py`

**Interfaces:**
- Documents catalog-level `image_auto.text_model` / `image_auto.reference_model`.
- Documents provider-level `image_edit_endpoint` and `image_model_capabilities.<model>.mode/edit_route`.
- Keeps secrets out of examples.

- [ ] **Step 1: Extend governance tests first**

Update `test_user_configuration_guide_documents_provider_catalog_controls` and/or add a focused image-provider documentation test asserting that the configuration guide and example contain:

```text
image_auto
image_edit_endpoint
image_model_capabilities
mode
edit_route
```

and that example Auto targets are fully-qualified `provider::model` values.

- [ ] **Step 2: Run governance test and verify failure**

Run: `python -m pytest tests/test_governance.py -k "provider_catalog" -q`
Expected: FAIL until docs/examples are updated.

- [ ] **Step 3: Update provider example and configuration guide**

Show a commented NanoGPT-style capability example using placeholder credentials/host-safe documentation, with `chroma` as text Auto target and `step-image-edit-2` as reference Auto target. Explain legacy string-only catalogs remain valid and that `image_edit_endpoint` defaults to `<api_endpoint>/images/edits`.

- [ ] **Step 4: Update user guide, canonical help detail, and changelog**

Explain:

- Auto uses the active native character PNG when a reference-capable Auto target is configured;
- manual model selection overrides Auto;
- reference images are read in memory and sent only to the selected image provider;
- Current Scene does not advance the story;
- reference models may use provider-controlled output sizing;
- final Telegram delivery remains image-only.

Do not claim multi-character/group reference support.

- [ ] **Step 5: Run governance/help/image documentation tests**

Run: `python -m pytest tests/test_governance.py tests/test_image_generation.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add config/providers.example.yaml docs/configuration.md docs/user-guide.md bridge/help_details.json CHANGELOG.md tests/test_governance.py
git commit -m "docs: document imagine character references"
```

### Task 7: Whole-branch verification

**Files:** No planned source changes unless verification finds a defect.

**Interfaces:** Verifies all previous tasks integrate without regressions.

- [ ] **Step 1: Run focused feature/security suite**

Run:

```bash
python -m pytest   tests/test_image_routing.py   tests/test_image_reference.py   tests/test_image_generation.py   tests/test_provider_ingestion_safety.py   tests/test_governance.py -q
```

Expected: PASS.

- [ ] **Step 2: Run formatting/lint/static analysis**

Run:

```bash
python -m ruff check .
python -m ruff format --check .
python tools/static_analysis.py
```

Expected: all commands exit 0.

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 4: Run coverage gate used by repository CI**

Run:

```bash
python -m pytest --cov=bridge --cov-report=term-missing --cov-report=json:coverage.json
```

Expected: PASS and repository coverage floor remains satisfied.

- [ ] **Step 5: Confirm git diff is scoped to the approved feature**

Run:

```bash
git status --short
git diff main...HEAD --stat
git diff main...HEAD --check
```

Expected: only the files named by this plan/spec (plus mechanically required lock/docs changes if verification proves necessary), with no whitespace errors or private configuration.

- [ ] **Step 6: Do not merge yet**

Stop after verification and invoke `superpowers:requesting-code-review` followed by `superpowers:verification-before-completion`. Merge/release only after review and the user's integration approval or an already-preserved execution instruction authorizing it.
