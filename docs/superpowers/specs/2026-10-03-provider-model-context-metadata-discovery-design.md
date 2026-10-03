# Provider Model Context Metadata Discovery Design

**Status:** Approved design, pending implementation plan  
**Date:** 2026-10-03  
**Scope:** Public installer, provider discovery, context planning, provider diagnostics

## 1. Problem

The bridge has safe long-session prompt compaction and supports explicit provider/model context-window metadata, but a newly installed custom provider does not receive any context metadata automatically.

The guided installer currently creates a provider similar to:

```yaml
providers:
  example:
    api_endpoint: https://provider.example/v1
    models:
      - model-a
    discover_models: false
```

When neither `context_window_tokens` nor `model_context_window_tokens` is present, context planning falls back to `SILLYTAVERN_CONTEXT_WINDOW_TOKENS`, currently 32,768 tokens. This is safe but can unnecessarily compact sessions for models that actually support 128K, 256K, 512K, or larger windows.

A public installer cannot reasonably expect users to know a model's context limit. Asking during installation would encourage guesses and can cause provider request failures if a value is overestimated.

## 2. Goals

1. Fresh public installs must work without asking the user for a context-window size.
2. A provider may keep its configured model list pinned while still allowing context metadata discovery.
3. Model-list discovery and model-metadata discovery must be independently controllable.
4. Explicit YAML configuration must always override discovered metadata.
5. Unknown or unavailable metadata must retain the conservative 32K fallback.
6. Metadata discovery must not block normal story generation.
7. Metadata discovery must never silently add, remove, reorder, or switch models.
8. No new cron, timer, or periodic scheduler is introduced.
9. The feature must reuse the existing provider security boundary and bounded cache infrastructure.

## 3. Non-goals

- Maintaining a repository-wide database of third-party model context windows.
- Guessing context size from model names.
- Querying unrelated external model registries.
- Changing output-token reservation behavior.
- Changing provider fallback/routing behavior.
- Automatically enabling model-list synchronization.
- Supporting arbitrary recursive JSON metadata extraction.
- Raising the existing 1,000,000-token validation ceiling in this change.

## 4. User-facing behavior

### Fresh guided install

The installer continues to ask only for the information users can reasonably know:

- default model in `provider::model` form
- OpenAI-compatible endpoint
- provider API key

It does **not** ask for context length.

The generated provider config enables metadata discovery but keeps model-list discovery disabled:

```yaml
providers:
  example:
    transport: chat_completions
    api_endpoint: https://provider.example/v1
    api_key_env: LLM_API_KEY
    models:
      - model-a
    discover_models: false
    discover_model_metadata: true
```

For manually authored provider configs, `discover_model_metadata` is opt-in and defaults to false when omitted. This avoids introducing unexpected network behavior for existing custom configs while making the guided installer zero-knowledge by default.

### Context resolution

For a configured model, the bridge resolves context in this order:

1. explicit `model_context_window_tokens[model]`
2. explicit provider-wide `context_window_tokens`
3. discovered cached context metadata for that configured model
4. global `SILLYTAVERN_CONTEXT_WINDOW_TOKENS` fallback

The existing 32K default remains the last-resort safety ceiling.

### Diagnostics

Provider/context diagnostics should expose the source, for example:

```text
Model: example::model-a
Context: 131,072 tokens
Source: discovered provider metadata
```

or:

```text
Model: example::model-a
Context: 32,768 tokens
Source: conservative global fallback
Provider did not publish usable context metadata.
```

The provider panel may summarize metadata coverage, such as `Context metadata: 3/4 configured models known`.

## 5. Provider schema

Add two optional fields:

```yaml
discover_model_metadata: true

# Optional override for providers whose richer model catalog is not at
# <api_endpoint>/models.
models_endpoint: https://provider.example/v1/models?detailed=true
```

`models_endpoint` is used by both model-ID discovery and metadata discovery when present. Otherwise discovery uses `<api_endpoint>/models`.

The endpoint must pass the same URL validation, host allowlist, credential, response-size, and DNS-pinning rules as existing provider discovery. Credentials must never be embedded in the URL.

## 6. Discovery semantics

The existing provider catalog refresh flow is extended rather than creating a second scheduler.

A provider is queried when either:

- `discover_models: true`, or
- `discover_model_metadata: true`

A single bounded catalog request may serve both purposes.

### Model-list behavior

When `discover_models: true`, discovered model IDs continue to be cached and may participate in the existing routing catalog exactly as today.

When `discover_models: false`, discovery must not add any returned model ID to routing. The configured `models:` list remains authoritative.

### Metadata behavior

When `discover_model_metadata: true`, the bridge examines metadata only for model IDs that are explicitly present in the provider's configured `models:` list.

Returned entries for other models are ignored for metadata purposes.

This boundary guarantees that a provider catalog cannot expand the user's routing surface merely because metadata discovery is enabled.

## 7. Supported context metadata fields

Initial extraction is deliberately bounded. For each matching model object, the bridge checks a fixed ordered set of top-level fields:

1. `context_length`
2. `context_window_tokens`
3. `context_window`
4. `max_context_length`
5. `max_position_embeddings`

The first valid value wins.

Values pass the bridge's existing context integer validation. Invalid, boolean, non-numeric, below-minimum, or above-maximum values are ignored.

The implementation must not recursively scan arbitrary JSON for numbers called “context”. Provider-specific nested formats can be added later through explicit adapters or a documented schema extension.

## 8. Cache design

Extend each provider's existing model catalog cache entry with metadata-specific fields:

```json
{
  "model_context_window_tokens": {
    "model-a": 131072,
    "model-b": 262144
  },
  "metadata_refreshed_at": 1791000000.0,
  "metadata_last_attempt_at": 1791000000.0,
  "metadata_last_error": null
}
```

Existing model-list cache fields remain separate.

A transport or provider refresh failure does not delete the last successfully cached context values. This prevents a temporary provider outage from suddenly compacting established sessions back to 32K. Diagnostics may mark the metadata stale or report the last refresh error.

A successful catalog response is authoritative for metadata presence. For each explicitly configured model, if the returned model entry is absent or contains no valid bounded context field, the bridge clears that model's previously discovered context value so resolution safely falls back to explicit configuration or the global fallback. This prevents stale discovered metadata from indefinitely overestimating a model whose provider-side limits changed.

Cache values are advisory only. They never override explicit YAML.

## 9. Refresh lifecycle

### Startup

After normal settings validation and service startup, the bridge schedules one non-blocking metadata refresh for providers that:

- have `discover_model_metadata: true`, and
- do not have fresh metadata according to the existing refresh interval.

The startup path must not wait for this request. Until metadata is available, context planning safely uses explicit configuration or the global fallback.

This is a one-shot startup maintenance action, not a recurring timer.

### Manual refresh

The existing provider refresh action refreshes whichever dimensions are enabled:

- model IDs when `discover_models: true`
- context metadata when `discover_model_metadata: true`

The UI label should describe the broader behavior, e.g. **Refresh provider catalog**, rather than imply that only model IDs are refreshed.

### Generation path

Story generation never performs discovery network I/O. Context resolution is cache/config only.

## 10. Context planner integration

`context_metadata_for_model()` continues to require a valid provider/model selection.

For explicit configured models, it merges three sources without mutating the routing catalog:

- provider YAML
- provider model override map
- metadata cache

The source returned in the context profile should distinguish:

- `provider-model`
- `provider`
- `discovered-provider-model`
- `global-fallback`

Discovered metadata must only be considered when:

- the provider has `discover_model_metadata: true`, and
- the model ID is explicitly configured in `models:`.

This keeps metadata cache contents from becoming routing authority.

## 11. Installer changes

`_provider_document()` adds:

```yaml
discover_model_metadata: true
```

It continues to write:

```yaml
discover_models: false
```

No new installer prompt is added.

The example provider configuration and documentation explain that:

- metadata discovery is safe to enable without model synchronization,
- context metadata is best-effort,
- explicit values win,
- 32K remains the fallback when metadata is unavailable.

## 12. Error handling

Metadata discovery is best-effort.

| Condition | Behavior |
|---|---|
| provider lacks `/models` | record metadata error; retain existing cache; use explicit/fallback context |
| HTTP 401/403 | record auth failure; do not modify cached values |
| HTTP 429/503 | record provider availability error; keep cached values |
| timeout/network error | record error; keep cached values |
| oversized/invalid JSON | reject response; keep cached values |
| successful response omits configured model entry | clear that model's discovered context; use explicit/fallback context |
| successful response contains invalid/out-of-bounds context | clear that model's discovered context; use explicit/fallback context |
| model not explicitly configured | ignore metadata |
| explicit YAML value exists | use YAML regardless of cache |

Metadata failure must never make the provider unavailable for inference by itself.

## 13. Security and privacy

The feature stays within the already configured provider origin and existing provider credential boundary.

Requirements:

- use `strict_urlopen`
- validate `models_endpoint` with existing provider endpoint validation
- preserve response-size limits
- no credentials in logs
- no arbitrary redirects to unapproved hosts
- no cross-provider metadata lookups
- no external centralized model registry
- cached metadata contains only model IDs, bounded numeric context values, timestamps, and error categories

## 14. Expected code boundaries

Likely implementation areas:

- `bridge/provider_discovery.py`
  - separate model-ID and metadata extraction from one catalog response
  - support `models_endpoint`
  - track metadata refresh state

- `bridge/provider_catalog_cache.py`
  - validate and persist bounded context metadata maps

- `bridge/provider_catalog.py`
  - merge explicit context configuration with cached metadata for configured models only

- `bridge/context_compaction.py`
  - consume the new metadata source label; budgeting algorithm otherwise unchanged

- `bridge/install_support.py`
  - enable metadata discovery in generated custom-provider config

- provider panel/diagnostics modules
  - show metadata coverage/source and broaden refresh wording

- `config/providers.example.yaml`
  - document the two independent discovery flags

- `docs/configuration.md` and user installation documentation
  - explain zero-knowledge context discovery and fallback behavior

No new service, daemon, timer, or database table is required.

## 15. Testing strategy

### Provider discovery tests

- metadata-only provider performs one catalog request
- metadata-only provider does not add returned model IDs to routing
- combined model + metadata discovery reuses one response
- custom `models_endpoint` is honored and validated
- only explicitly configured models receive metadata
- known context field aliases parse correctly
- invalid/out-of-range values are ignored
- transport/provider refresh failures preserve prior successful metadata
- successful responses clear stale metadata when a configured model no longer publishes a valid context value

### Context resolution tests

- explicit per-model value beats all other sources
- provider-wide explicit value beats discovered cache
- discovered configured-model metadata beats global fallback
- absent metadata resolves to 32K fallback
- cached metadata cannot authorize an unconfigured model
- source labels are correct

### Installer tests

- generated provider has `discover_models: false`
- generated provider has `discover_model_metadata: true`
- installer asks no context-window question

### Regression tests

- existing provider model discovery behavior remains unchanged
- discovery-disabled providers make no catalog request
- generation performs no metadata network request
- context compaction still preserves fixed instructions/current turn
- provider metadata failure does not fail inference routing

## 16. Acceptance criteria

The feature is complete when all of the following are true:

1. A fresh custom-provider install never asks for context-window size.
2. The generated config pins the user's model list while enabling metadata discovery.
3. A provider that publishes context length can automatically raise the bridge planner above the 32K fallback.
4. Enabling metadata discovery alone cannot add any model to routing.
5. Explicit YAML context values always win.
6. Provider/catalog failures retain safe behavior and do not block generation.
7. Unknown context continues to resolve to the conservative 32K fallback.
8. Startup metadata refresh is non-blocking and no recurring cron/timer is added.
9. Tests cover routing isolation, cache precedence, installer behavior, malformed metadata, and failure handling.
