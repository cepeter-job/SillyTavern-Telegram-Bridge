# Provider discovery, runtime health, and resilience

## Approved scope and intent
Implement the four waves approved in the conversation: runtime observations;
resilience; `/providers` diagnostics and per-provider maintenance; bounded
persistence/history and concurrent probes. Each wave is a separate branch/PR.
Preserve deterministic `ModelRouter` resolution and existing session ownership.
Do not update or restart the running VPS service as part of publishing.

## Boundaries
- YAML owns endpoints, credential sources and explicitly configured models.
- Discovery cache supplies model IDs only; failed discovery retains last good IDs.
- Runtime health is learned from actual calls through `ProviderPort`.
- A `ProviderExecutionPolicy` above the transport resolves exact routes, observes
  attempts and enforces cooldowns. `ModelRouter` remains unchanged and pure.
- Live probes are diagnostics only; their success must never reset a runtime
  circuit or authenticate an inference route by implication.
- Runtime status is explicitly injected into provider callbacks, not an ambient
  singleton and not stored on immutable application settings/request values.

## Wave 1: foundation
Introduce frozen health/attempt values, thread-safe `ProviderRuntimeHealth`, and
`ProviderExecutionPolicy`. Compose one instance per application. Record canonical
provider/model identity, last success/failure, sanitized category/status and
failure streak. Ignore cancellation and request-local errors for provider health.
A model 404 changes only that model. Endpoint/credential-level errors are
provider scoped. Older concurrent successes must not erase newer failures.
Keep observation optional for standalone port consumers and tests.

## Wave 2: resilience
After three consecutive timeout/network/5xx failures, block calls for 60 seconds.
Repeated failed recovery probes double delay, capped at 900 seconds.
429 uses valid Retry-After seconds or HTTP-date (RFC 9110 section 10.2.3); missing
values use 60 seconds. Clamp waits to 86400 seconds and reject malformed values.
401/403 and 402 require operator attention, suppress immediate retry, and permit
a recovery request after 300 seconds or an explicit local reset. Model 404 uses
the same short recheck window at model scope. One half-open request is admitted
atomically; cancellation releases its reservation. No sleeping retry loop.

Fallback lists live on the selected provider spec: `utility_fallbacks` and
`story_fallbacks`. They contain explicit `provider-id::model-id` entries only.
At most two distinct fallback routes are attempted after the primary. Story
fallback requires `allow_story_fallback: true`; otherwise users choose through
`/providers`. Utility fallbacks apply only to known utility purposes (summary,
memory, scene, rank, optimizer, choices, npc). No guess by model name or price.
Never fallback after visible streaming output, cancellation, deadline exhaustion,
or a request-local error. Never mutate the session's selected model. Record usage
against the model actually attempted. Fallback credentials are independently
resolved from their provider, never forwarded from the failed primary.

## Wave 3: provider interface and maintenance
Keep `/providers` as the only Telegram entry point. Retain global Health/Refresh
buttons and add per-provider Test/Refresh/Reset-runtime controls using actor- and
session-bound callback tokens. Reset changes local circuit state only.
Show runtime state, remaining cooldown, last success/error, model counts, catalog
age and configured/discovered source. A stale list is not an unavailable model.
Use explicit probe descriptions: catalog reachable, inference probe, local OAuth
state. A first-byte streaming response is not proof of successful completion.
Health results must fit Telegram limits and paging must not repeat paid probes.

Isolate configuration/network failures per provider without relaxing DNS-pinned
endpoint policy. Discovery is still opt-in and lazy with its current TTL. Force
refresh can target one provider. Refresh rendering must not immediately retry
failed discovery. Validate cache shapes, atomically serialize writes, and preserve
static plus discovered IDs consistently between menus and routing. Do not truncate
the catalog to 50 models before pagination.

## Wave 4: diagnostics
Persist only bounded sanitized state/history under the private bridge home.
Use a separate versioned JSON diagnostic store (no operational DB migration),
atomic replace with mode 0600, bounded reads, no secrets/prompts/error bodies.
Cap state at 1024 entries and history at 10 transitions per route. Expire
observations after 24 hours; never restore half-open reservations. Corrupt files
or persistence errors must not prevent requests or mask original failures.
Run manual probes with at most three workers and a serialized sweep per service;
results retain catalog order. No scheduled background network probes.

## Verification and release
Write failing regression tests before production changes. Test time with injected
clocks, concurrency with deterministic synchronization, networks with local
fakes (never bill a real provider). Run full pytest, lint, format, dependency/
architecture checks and mypy for every wave; protected GitHub checks gate merges.
Publish the next unused version with an SSH-signed annotated tag trusted by the
existing external allowed-signers file, plus source ZIP and SHA-256 checksum.
Preserve runtime/development dependency locks and existing updater compatibility.
