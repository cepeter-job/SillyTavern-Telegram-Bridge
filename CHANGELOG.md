# Changelog

All notable changes to **SillyTavern Telegram Bridge** are documented here.

## [Unreleased]

### Maintenance

- Audit regression-test size and source-inspection patterns; share identical boundary-test helpers while retaining their assertions, and use short parameter IDs for oversized provider-health fixtures.
- Run Python regression/coverage and Mini App smoke as independent CI jobs. Preserve the protected `test` check as a strict final gate over both jobs and the existing security/static checks; publish JUnit, timings and diagnostic artifacts.

### Added

- Add plain-text `/usage` for the active session’s last 7 days of provider-reported token usage, coverage, failures/cancellations and daily/model/task breakdowns. Reuse the existing ledger without a model call or Mini App panel; include Telegram command-menu and Help entries.
- Add `/trackers` and Mini App **Manage → Story trackers** for saved, visibility-filtered canonical state and recent checks, including extraction freshness. Both views reuse SQLite and do not request model work.
- Add persisted **Realism / Anime** selection under `/imagine` → **Options** for scene and custom prompts, including character references, prompt-length accounting, reset and alternate-ending preferences.
- Bound estimated input independently of the model context window with `SILLYTAVERN_CONTEXT_INPUT_CAP_TOKENS` (default 49,152). The existing compactor uses the smaller of this cap and the model's available input budget; `/prompt` shows both values. `max_tokens` remains an output limit.
- Assimilate story trackers into typed, source-scoped canonical state: supporting-NPC BOND/Sparks/Grudge and agendas, user inventory/skills/conditions, factions, quest metadata and foreshadowing links.
- Reuse the existing bounded NPC Utility extraction job, including atomic multipart publication, without a second per-source tracker request. Project eligible relationship/agenda fields into NPC Bank and reference existing Narrative arcs/threads; native Scene, Narrative, Director and Ending owners retain authority.
- Add `/check <domain> <DC> <action>` with one bridge-owned d20 result per durable action. Established user modifiers apply at the action's source boundary; delivery recovery preserves the original roll and does not create an assistant turn or advance agenda timers.
- Supply bounded optional canonical context to ordinary, image, edited, regenerated and continued replies, Light Novel choices, Director and epilogue generation. Character scope excludes private offscreen tracker facts.

### Fixed

- Retire the abandoned Internal States prompt protocol, including numeric baseline import, automatic historical bootstrap, special prompt rewriting and output suppression. Native tracker extraction, ordinary Telegram formatting and Light Novel renderer-envelope validation remain in place.
- Fence tracker publication and check delivery against replay, stale workers, rewritten/deleted sources and session changes. Reset, swipe, continuation and alternate-ending checkpoints now preserve the same state boundary as canonical story history.
- Reconstruct historical NPC checkpoint fields from bounded field history so a later tracker projection cannot erase the older snapshot value.
- Carry bounded native NPC field history through branch restoration so later rewinds retain tracker projection ownership, manual overrides and deliberate field clears.
- Preserve reversible tracker revisions and accepted-source receipts together in alternate-ending checkpoints. Adopt delayed NPC aliases with source-attributed history; reject conflicting prior scores and malformed supplied JSON types atomically.
- Require every character-scope reader to be authorized for private agendas, and compact Director/choice tracker context without truncating fixed planning JSON.

### Database migration

- **Migration 25** adds canonical tracker records, source receipts, revisions, history and check results without replaying previously processed NPC history.
- **Migration 26** stops pending tracker bootstrap work from an earlier installation and removes its temporary marker. It fences stale workers, preserves completed native state, restarts any discarded partial new row and rolls back genuinely rewritten suffixes before resuming extraction. Saved prompts and transcripts are not rewritten.
- No new runtime dependencies or endpoint settings. Rollback across this schema change requires the matching pre-upgrade database snapshot.

## [0.3.006] - 2026-10-06

### Fixed

- Close SQLite backup, validation and restore connections deterministically on success and failure. Finalize only the destination in DELETE journal mode so a verified backup is a standalone file; leave the live database in WAL mode.
- Create database backups with private permissions from the start and clean failed temporary snapshots only after closing their handles. Preserve historical backups that still have WAL/SHM sidecars rather than orphaning those sidecars during retention.

### Maintenance

- Share Telegram UTF-16 length and escape calculations, identical episodic/NPC JSON fence parsing, actor visibility normalization, and HTML newline handling without changing the stricter Light Novel parser or raw repository decoding.
- Add a Python file-size ratchet against the reviewed base commit. Existing oversized files cannot grow, shrinking limits must be updated, and cohesive small modules remain valid.
- Add reference-audit evidence for aliased/local imports, installer module commands and path-invoked workers. Findings are review candidates, not automatic deletion instructions.
- Consolidate the repository audit, preserving the live memory snapshot layers, Tailscale installer entrypoint and per-domain callback routing.
- Backfill six published release entries and document the intentional latest-only release/tag policy without rewriting source history.

### Compatibility and validation

- No runtime dependency-lock or database-schema changes.
- Regression coverage includes backup/restore failure cleanup, standalone WAL snapshots, Unicode entities, visibility decoding distinctions and audit guardrails. Full release verification remains tied to the exact PR and merge commits in CI.

## [0.3.005] - 2026-10-06

_Backfilled from the published release notes. Verification describes that historical release._

**Release commit:** `28e61d5d323e6ea7f2ea3ef2031372a613feede6`
**Signed annotated tag:** `v0.3.005`

### Telegram formatting

- Render allowlisted Telegram-safe HTML as native Telegram message entities instead of flattening every style to plain text.
- Support bold, italic, underline, strikethrough, spoiler, inline code, preformatted code, blockquote, expandable blockquote, and safe HTTP/HTTPS text links.
- Preserve existing single-star roleplay narration italics and keep general Markdown syntax literal rather than enabling MarkdownV2 parsing.
- Preserve formatting through story generation, greetings, image replies, edited replies, recovery, streaming finalization, TTS-visible text, and Telegram chunking.
- Allow compatible nested entities, including bold or spoiler formatting inside blockquotes and narration italics inside spoiler/bold spans.
- Use UTF-16 entity offsets, split formatting safely across Telegram's message limit, and trim trailing whitespace from entity ranges so multiline spoilers remain valid.

### Safety

- Unknown presentation HTML remains sanitized.
- Script/style content is discarded.
- Text links accept only HTTP/HTTPS targets.
- Persisted delivery checkpoint payloads remain plain-text normalized.
- Custom emoji, text mentions, and formatted dates remain intentionally out of scope.

### Dependencies

- Update Hindsight client to the reviewed 0.10.2 line and refresh locked runtime dependencies.
- Refresh the development tooling lock and CI uv bootstrap pin.

### Verification

- PR #374 passed its full CI suite with 3,499 tests plus 806 subtests and 82.09% application coverage.
- Post-merge CI and CodeQL both passed on the exact release commit.
- Static analysis, dependency audit, secret scan, Mini App smoke tests, and security-critical coverage gates passed.

## [0.3.004] - 2026-10-06

_Backfilled from the published release notes. Verification describes that historical release._

**Release commit:** `55f786418c4cd6a41e20f6d4f9630e119ca74b94`
**Signed annotated tag:** `v0.3.004`

### Story memory

- Make accepted story-memory work durable and independently recoverable instead of letting one failed layer discard other completed work.
- Preserve complete deterministic source coverage rather than relying on a rolling recent-message snapshot.
- Apply one request-era eligibility scope for branch, incarnation, rewrite watermark, reader visibility, audience/knowledge boundary, and as-of time.
- Keep SQLite as canonical authority while using Hindsight only for semantic candidate ranking; remote text is never trusted as prompt authority.
- Preserve valid earlier memory across rewrites, clears, retries, alternate branches, stale workers, and external-memory outages.

### Retrieval and extraction

- Add rebuildable SQLite FTS5 full-corpus episodic/fact search with bounded query expansion and local-authority evidence fusion.
- Make long-source extraction resumable and complete across summary, scene, curator, and NPC layers before publication.
- Add durable raw-archive attempt tracking so delayed, timed-out, crashed, or overlapping remote writes remain cleanup obligations without starving ordinary ingestion.
- Keep recurring uncertain cleanup watches out of the normal ingestion gate while finite due cleanup failures still block and retry safely.

### Request budgeting

- Budget after final Light Novel/story instructions using the actual model/output allowance.
- Protect current-user, scene, image, and continuation content while trimming only optional sections.
- Revalidate fallback, recovery, normalized Codex, Muse, and auto-continuation attempts before provider dispatch.

### Evaluation and verification

- Add a reproducible synthetic memory evaluator and documented fixture/results.
- Final tested branch head passed 3,487 tests plus 806 subtests with 82.07% application coverage.
- Post-merge `main` CI and CodeQL both completed successfully on the exact release tree.
- PR #371 completed independent staged and whole-branch review with no remaining Critical or Important finding.

### Database migration

- **Migration 24** adds durable raw-archival attempt tracking used for crash/timeout cleanup recovery.
- Forward migration preserves canonical story data. Rollback across this schema change requires the matching pre-upgrade database snapshot.

## [0.3.003] - 2026-10-05

_Backfilled from the published release notes. Verification describes that historical release._

**Release commit:** `0049d9ce52486990e3a7ba7eeb8ea300cba12235`
**Signed annotated tag:** `v0.3.003`

### Added

- Add `/provider` as an alias for the provider panel and pair Story/Utility model selection with their reasoning controls in the same inline flow.
- Add opt-in direct Codex OAuth model discovery against the bridge-owned native Codex endpoint. Discovery is disabled by default, filters to API-supported picker-visible models, and preserves configured/cached models on failure.
- Add current GPT-6 Codex catalog entries plus the `gpt-6-luna-900k` context alias.
- Add a high-churn memory-retention regression covering repeated lifecycle activity so future leaks are caught by CI.

### Documentation

- Rewrite setup, configuration, operations, Mini App and user-facing guidance for clearer installation and day-to-day operation.

### Verification

- The release commit passed both protected push-time workflows on `main`.
- PR #370 passed its focused Codex provider/auth/context regression suite before merge.
- The release tag is SSH-signed with the same independently trusted maintainer key used for v0.3.002.
- No database-schema migration is introduced by this release.

## [0.3.002] - 2026-10-05

### Fixed

- Guard Light Novel **Retry Choices** against repeated taps. The first accepted retry now atomically moves the saved choice set back to pending and immediately replaces the Telegram retry panel with a buttonless preparing state before worker dispatch.
- Repeated or stale retry callbacks converge the same panel to the disabled pending state while preserving the existing durable-job idempotency, so only one choice-generation job is queued and committed story text is never regenerated.

### Compatibility and validation

- No runtime dependency-lock or database-schema changes.
- Release verification covers duplicate-callback convergence, the full Python suite, static analysis, dependency audit, secret scan, Mini App smoke checks, and application/security coverage gates.

## [0.3.001] - 2026-10-04

### Fixed

- Preserve character quality ranks across safe character-directory moves and
  filesystem recreation by keying new rank state to the card SHA-256 instead of
  absolute path, mtime and inode. Existing filesystem-bound rank state upgrades
  lazily only when the original recorded file still proves byte-for-byte identity.

## [0.3.000] - 2026-10-04

The 0.3 series adds the complete Narrative Engine, AI Director, closed stories
and independent alternate endings.

### Added

- Narrative Style in character setup and a `/narrative` panel for current stories:
  Player-centric, Ensemble, World-driven, Observer and advanced Custom settings.
- Personal setup defaults that never overwrite another session's preferences.
- Committed narrative scene/thread tracking with revision-safe Utility
  reconciliation, bounded batches and bounded rewind snapshots.
- Shared narrative policies for text, images, edits, regeneration, continuation,
  Light Novel choices and Group speaker selection. Off-screen choices use
  narrative steering instead of invented user participation.

- A canonical AI Director with a dedicated optional model route, independent
  reasoning budget, strict proposal validation and adaptive/fixed cadence.
- Actor- and revision-bound Director Room controls in Telegram and the Mini App,
  with temporary scene directions, persistent objectives and decision history.
- Groups consume canonical plans without a second planning-model request.

- Revision-aware story arcs with committed-text evidence for resolved outcomes,
  plus separate persistent user guidance in Director Room.
- Ending Goal revisions, finale-readiness checks and atomic immutable pre-finale
  snapshots as the storage foundation for Closed Story.
- Full Closed Story execution with multi-turn finales, a separate Story-model
  epilogue, bounded restart recovery and immutable completed originals.
- Ending controls and explicit finale confirmation in Telegram and the Mini App.
- Alternate Ending creates an independent session from an immutable pre-finale
  checkpoint, with local continuity restored and Hindsight recall isolated to
  the new session. Completed originals are never reopened.
- Greeting photos from the opening message image URL, or the selected character
  PNG, with delivery receipts so a failed photo does not resend the opening text.
- Light Novel choices vary motive and approach without assuming altruism or
  forcing cruelty. Off-screen scenes retain narrative-steering choices.

### Release signing

- Rotate the fresh-install public signer pin for `v0.3.000`. Existing operators
  must independently verify and add the new public key before updating. Existing
  trust files and historical tags are preserved.

### Fixed

- Provider-escaped roleplay paragraph breaks render as real Telegram newlines
  without decoding code blocks or literal paths.
- Deleted opening messages no longer send orphan greeting photos; their image
  URLs and delivery receipts are removed transactionally with the message.
- Reset clears derived narrative state while preserving Narrative Style and
  personal defaults. Its local cleanup is atomic after successful memory purge.
- Optional NumPy acceleration now falls back to existing Python vector math when
  the installed wheel cannot initialize on a VPS's exposed CPU instruction set.

## [0.2.068] - 2026-10-03

_Backfilled from the published release notes. Verification describes that historical release._

## What's Changed
* docs: make setup and user guides easier to follow by @cepeter in https://github.com/cepeter/SillyTavern-Telegram-Bridge/pull/355

## [0.2.067] - 2026-10-03

_Backfilled from the published release notes. Verification describes that historical release._

## What's Changed
* fix: make roleplay italics deterministic including first messages by @cepeter in https://github.com/cepeter/SillyTavern-Telegram-Bridge/pull/353
* fix: recover invalid optimizer previews safely by @cepeter in https://github.com/cepeter/SillyTavern-Telegram-Bridge/pull/354

## [0.2.066] - 2026-10-03

_Backfilled from the published release notes. Verification describes that historical release._

## What's Changed
* feat: discover provider model context metadata safely by @cepeter in https://github.com/cepeter/SillyTavern-Telegram-Bridge/pull/351

## [0.2.058] - 2026-10-02

### Added

- Add capability-aware `/imagine` Auto routing. With valid catalog-level text
  and reference targets, the bridge uses the active native SillyTavern character
  PNG as a single in-memory identity reference for reference-capable image models
  and falls back to the configured text target when no usable reference exists.
- Add bounded OpenAI-compatible `/images/edits` transport with explicit
  `image_edit_endpoint`, per-model `image_model_capabilities`, and manual model
  selection that always overrides Auto.

### Changed

- Treat image output size as a preference for reference/edit models that cannot
  honor an exact bridge preset, while preserving existing text-to-image sizing.
- Remove the temporary image-generation progress message on both success and
  failure without masking a successful image delivery if progress cleanup fails.
- Keep final Telegram `/imagine` delivery image-only and avoid a second paid
  generation request after a provider failure.

- Deliver `/imagine` results as image-only Telegram photos, without echoing the
  source prompt, selected model, or provider-revised prompt in the photo caption.
- Keep the temporary `🎨 Generating image…` message while rendering, then remove
  it after successful image delivery so the completed Telegram output is only the image.

### Compatibility and validation

- No runtime dependency-lock or database-schema changes. Existing sessions, image
  provider selection, prompt limits, scene generation, and updater trust boundaries
  remain compatible.
- Publishing this release does not deploy or restart a running bridge.

## [0.2.057] - 2026-10-02

### Added

- Render SillyTavern-style single-star narration, thoughts, and actions as Telegram
  italic entities while keeping direct spoken dialogue and surrounding text normal.
  Preserve stored transcripts, generation prompts, Light Novel envelopes, TTS quote
  extraction, delivery checkpoints, UTF-16 offsets, long-message splitting, and
  streaming-preview finalization.

### Fixed

- Prevent rejected or unauthorized Telegram senders from stalling durable update
  polling when the best-effort private-bot notification itself fails, including the
  `403: bot was blocked by the user` case.

### Changed

- Complete canonical feature-panel ownership and bounded transcript-read ownership,
  removing duplicate domain senders/readers while preserving domain-specific policy.
- Complete the audited callback decomposition across character, feature/common, enum,
  provider, persona, session, NPC, World Info, and greeting workflows. Large flat
  dispatchers now route through explicit named actions with narrow arguments while
  preserving callback vocabulary, authorization, ordering, durable operation identity,
  and existing behavior.

### Compatibility and validation

- No runtime dependency-lock or database-schema changes. Existing sessions, queued
  operations, updater trust boundaries, public configuration examples, and private
  deployment state remain compatible.
- Release-prep verification runs the protected CI, dependency audit, secret scan,
  static analysis, Mini App smoke/session-safety checks, full Python test suite, and
  application/security coverage gates before the signed tag is published.
- Publishing this release does not deploy or restart a running bridge.

## [0.2.056] - 2026-10-02

### Fixed

- Recover complete Light Novel story envelopes wrapped in a singleton JSON array
  or a nonstandard full-response code fence, without logging private story text.
- Offer explicit, session- and owner-scoped recovery routes after repeated Light
  Novel protocol failures. Persist per-turn model and strategy overrides across
  durable job recovery without changing session defaults.
- Remove stale invalidated Light Novel choice panels when editing a manual reply.
- Apply the selected image model's prompt ceiling to Current Scene and Custom
  Prompt generation; use a 1,200-character ceiling for `z-image-turbo` and preserve
  the generic 4,000-character fallback. Report provider prompt-limit errors safely.

### Maintenance

- Bound native/text caches with entry and byte budgets, release idle chat/session
  lock identities, and retain only the latest idle speech model while preserving
  active callers. Byte budgets do not promise an exact process-memory ceiling.
- Use the pinned Hindsight SDK's public cleanup lifecycle and remove verified
  unused helpers and retired private-client compatibility paths.
- Consolidate read-only source-inspection test helpers, document maintainability
  findings, and restore configuration-example and documentation-table consistency.
- Exercise production Mini App job polling in the smoke test instead of a
  test-only request burst; preserve the production rate limit.
- Add the requested NanoGPT invitation panel to the README.

### Compatibility

- No dependency-lock or database-schema changes. Existing sessions, queued work,
  public examples, callback isolation, and recovery boundaries are preserved.
- Publishing this release does not deploy or restart a running bridge.

## [0.2.055] - 2026-10-01

### Fixed

- Serialize busy interactive panel callbacks so repeated clicks cannot launch overlapping work or duplicate task execution.
- Report the installed bridge version from the managed deployment marker when available, avoiding stale version reporting when release files and changelog metadata diverge.

### Security and maintenance

- Update `pypdf` for current security advisories and synchronize dependency locks.
- Remove completed internal planning artifacts that are no longer required at runtime.

## [0.2.054] - 2026-10-01

### Added

- Replace direct `/imagine <prompt>` generation with a session-scoped image panel
  offering **Current Scene**, **Custom Prompt**, and image-generation options.
- Current Scene builds a bounded visual prompt from structured scene state, the
  latest committed assistant turn, and established character appearance context
  through the session Utility-model route without changing the roleplay transcript.
- Add per-session image model selection plus Square, Landscape, and Portrait output
  size presets with a reset-to-defaults action.

### Changed

- Inline `/imagine <prompt>` now opens the panel instead of bypassing the scoped
  image workflow.
- Character Auto Optimizer now preserves the original card's established maturity,
  sexual explicitness, taboo level, intimacy style, violence level, and intentional
  adult themes instead of treating them as quality defects. Manual Suggestion may
  change those attributes only when the user's guidance explicitly requests it.
- Optimizer guidance also prevents automatic mature-content escalation beyond what
  the original character card supports.

### Validation and compatibility

- Image generation remains read-only with respect to roleplay transcript/history,
  and image model/size overrides remain scoped to the active session.
- Combined feature tree passed 2,385 tests and 778 subtests before release
  finalization.
- No runtime dependency lockfiles or database migrations changed in this release.

## [0.2.053] - 2026-10-01

### Changed

- Rename the user-facing **Grounded User** setting to **I am not MC** mode while
  preserving the existing `grounded_user` storage, callbacks, and grounding
  semantics.
- Light Novel choices now preserve the established scene's maturity, intensity,
  genre, intimacy, danger, and subject matter instead of implicitly softening
  mature scenes into safer menu options. Grounding continues to constrain
  causality, agency, consent, knowledge, and probability of success rather than
  imposing a safer content rating.
- Choice generation still avoids introducing or escalating mature content beyond
  what the current scene supports. The policy is shared by Strategy A inline
  choices and Strategy B/C choice-only generation.

### Validation and compatibility

- Add regression coverage for maturity-preserving Light Novel prompts and the
  renamed I am not MC settings surfaces.
- No runtime dependency lockfiles or database migrations changed in this release.

## [0.2.052] - 2026-10-01

### Fixed

- Close Hindsight clients through the SDK public loop-aware lifecycle so repeated
  timeout cleanup cannot strand aiohttp connector tasks and accumulate bridge
  memory.
- Add bridge systemd memory guardrails (`MemoryHigh=512M`, `MemoryMax=768M`,
  `MemorySwapMax=256M`) with memory accounting so a future runaway is contained
  to the bridge service instead of exhausting VPS RAM and swap.

### Validation and compatibility

- Add regression coverage for Hindsight client cleanup and generated installer
  memory limits.
- No runtime dependency lockfiles or database migrations changed in this release.

## [0.2.051] - 2026-10-01

### Added

- Added provider/default and per-model context-window metadata, conservative
  tokenizer estimates, and persisted prompt-compaction diagnostics in /prompt
  and Mini App system health.

### Changed

- Context planning now reserves an additional bounded safety margin and rejects
  fixed prompts that still cannot fit after compaction before contacting the
  provider, with a user-visible oversized-context diagnostic.

## [0.2.050] - 2026-10-01

### Added

- Runtime provider/model health learned from real requests, categorized failures,
  bounded circuit cooldowns, Retry-After handling and single-request half-open recovery.
- Explicit Utility fallback chains and opt-in Story fallback, with cancellation,
  visible-output, deadline and credential-isolation safeguards.
- `/providers` diagnostics with targeted model refresh, provider tests, local
  runtime reset, cached probe pagination and catalog freshness metadata.
- Private, bounded runtime history and restart recovery; manual provider probes
  use at most three workers and serialize sweeps per bridge instance.

### Fixed

- Broken provider configuration no longer aborts an entire maintenance sweep.
  Discovery keeps last-known models, preserves configured IDs, handles malformed
  caches, and does not truncate the model picker to fifty entries.
- Refresh completion redraws avoid a second discovery request. Manual probes
  distinguish catalog reachability, local OAuth and unvalidated inference streams
  from successful real generation; probes cannot clear runtime cooldowns.
- Private model-cache writes are atomic and preserve newer concurrent results.

### Security and upgrade notes

- Includes the previously merged locked `urllib3` 2.8.0 update. Upgrading from
  v0.2.049 changes runtime dependencies: install the verified release and its
  locked dependencies manually. The Telegram `/update` dependency safeguard
  intentionally refuses this automatic upgrade.
- Provider health changes add no dependency, operational database migration,
  background probing or default silent Story-model switch.

## [0.2.049] - 2026-09-30

### Added

- Add the session-scoped **Grounded User** mode. It preserves explicit Persona/story advantages while discouraging unearned competence, authority, admiration, attraction, protection, plot centrality, and automatic NPC deference. Light Novel choices and Group Director speaker selection follow the same grounded policy without adding another provider call.
- Add the guided multi-choice installer with Linux package-manager detection, missing-dependency repair, pinned signer bootstrap, newest signed-release discovery, existing SillyTavern/dataRoot/native-user detection, and minimal first-install Telegram/provider prompts.
- Add durable delivery-recovery records and checkpoints for committed replies, including source identity, rendered payload, acknowledged Telegram chunks, and bounded retry ownership.

### Changed

- Preserve queued-session ownership across edit, regeneration, continuation, media, greeting, Light Novel, and restart-recovery paths instead of redirecting work through the currently active session.
- Keep existing SillyTavern Node/npm dependencies observational only during bridge installation; the installer reports their state but does not mutate an existing SillyTavern installation.
- Harden application/service dependency boundaries and align coverage/security documentation with the enforced CI surface.

### Fixed

- Bind Telegram NPC undo confirmations to the exact field-history revision the user reviewed, and refuse stale confirmations when background extraction changes that field before confirmation.
- Show Mini App NPC undo only for the latest visible revision of each field and enforce the same change-revision guard in the NPC service/API.
- Prevent character restore from performing a no-op rewrite when the installed card already matches the selected verified backup.
- Sanitize completed Light Novel replies after language rendering so presentation HTML cannot leak into Telegram output.
- Reject missing explicit image credentials before request construction, preventing fallback to unrelated text-provider credentials.
- Prevent transcript message identity reuse and remove incompatible response variants when edited/rewritten branches invalidate their source turn.
- Reject stale curated-memory completions and incompatible NPC audience mutations; keep summary coverage aligned only with successfully processed whole message rows.
- Enforce manual group-turn ownership before conversational photo, image-document, voice, and ambiguous PNG external work.
- Checkpoint Telegram delivery acknowledgements and keep locally committed edits/replies recoverable until delivery is complete.
- Route transient SQLite claim failures through durable worker retry handling rather than misclassifying them as business failures.
- Bind pending World uploads to the initiating actor and session and atomically consume the one-use authorization.

### Maintenance

- Isolate explicit test settings homes from ambient runtime environment state so CI and release verification cannot accidentally read machine-level bridge paths.
- Keep the full protected verification surface green across pytest/coverage, static analysis, dependency audits, secret scanning, mypy, architecture checks, shell syntax, and Mini App DOM smoke tests.

### Compatibility

- Runtime and development dependency lock files are unchanged from v0.2.048.
- Database migrations advance from 6 to **9**:
  - migration 7 adds non-reusable explicit message identity while preserving existing row IDs and indexes;
  - migration 8 adds assistant delivery-progress state;
  - migration 9 adds immutable job delivery intents for stale-safe recovery.
- Back up the operational database before upgrading. Restoring an older bridge binary requires the matching pre-upgrade database backup.

## [0.2.048] - 2026-09-30

### Added

- Add the session-scoped persistent NPC Bank with structured durable fields, reversible field history, Utility-model background extraction, knowledge boundaries, and branch-safe edit/regeneration rollback.
- Add Telegram `/npc` list, dossier, visible history, manual refresh, and confirmed field undo.
- Add verified character-card backup restore for Telegram and the Mini App, including deleted-card recovery and stale-revision protection.
- Add the Mini App NPC Bank with searchable dossiers, visibility-safe history, background refresh, and stale-safe field undo.

### Fixed

- Rerank character cards after Mini App optimizer proposals are applied, matching the Telegram flow, and hide empty rank overlays when no valid tier is available.

### Compatibility

- Runtime and development dependency files are unchanged from v0.2.047.
- Schema migration 6 adds NPC Bank tables. Back up the operational database before upgrading; restoring an older bridge binary requires the matching pre-upgrade database backup.

## [0.2.047] - 2026-09-30

### Added

- Add a durable episodic-memory layer alongside the existing rolling session continuity summary.
- Extract durable events from stable conversation segments using the session Utility/Memory model, with importance filtering, normalized deduplication, and source-row tracking.
- Retrieve query-relevant episodic memories separately from Hindsight and the continuity summary, and inject them as explicitly untrusted historical context.
- Add shared and restricted episodic-memory visibility with explicit `known_by` character boundaries.
- Fail closed for unscoped secret memories so private facts are not exposed across characters.

### Changed

- Keep episodic memories isolated to the active session and bounded by the prompt context budget.
- Suppress stale episodic recall during edited-message regeneration and invalidate memories derived from rewritten history.
- Purge episodic memories during session reset and session deletion.
- Add schema migration 5 using a companion visibility table without destructive `ALTER TABLE` upgrades.

### Fixed

- Delete the consumed Light Novel selection panel and the replaced story's choice panel only after `/regen` commits successfully, while retaining the newly generated panel.
- Keep edits bound to the owning session's model and character fields, and remove replaced Light Novel choice panels after the edited branch commits.

### Compatibility

- Runtime dependency files are unchanged from v0.2.046.
- Existing episodic records without visibility metadata remain shared for backward compatibility.

## [0.2.045] - 2026-09-28

### Fixed

- Keep long Home Persona, World, and Memory status values contained within their own Mini App status columns on narrow Telegram viewports instead of overlapping adjacent columns.

## [0.2.044] - 2026-09-28

### Added

- Add local Bridge, Telegram, and Database icons to Mini App health cards on both Home and System.

### Changed

- Implement the story-first Mini App design with a live Home story card, recent sessions, compact mobile character cards, five primary destinations, and a grouped Manage hub while keeping Usage under Advanced settings and System directly accessible.
- Add the optimized Mini App design gallery to README using compact WebP assets instead of multi-megabyte PNG screenshots.

## [0.2.043] - 2026-09-28

### Changed

- Make the user-facing installation, configuration, Mini App, operations, token-usage, and command documentation match the current signed-release workflow, provider controls, Hindsight loopback policy, and installer-managed files without clobbering private configuration.

### Fixed

- Prevent the Mini App **Review latest release** flow from creating or applying an update confirmation when the installed version is already current; stale confirmations are invalidated and already-latest races are refused instead of being reported as successful installs.

## [0.2.042] - 2026-09-28

### Changed

- Simplify first-install bootstrap into a one-time external signing-key trust setup followed by one copy/paste command block that discovers the newest `v*` tag, verifies it before checkout, and creates local branch `main` at the exact verified release commit for signed `/update` compatibility.
- Generate new private `.env` files with `SILLYTAVERN_UPDATE_ALLOWED_SIGNERS` pointing at the standard external `~/.config/sillytavern-telegram/trusted-maintainers` path while preserving explicit template values, and move `--release`, `--unsafe-main`, and manual/offline ZIP procedures into Advanced / development documentation.

## [0.2.041] - 2026-09-28

### Fixed

- Reconcile a stale managed live mirror when the signed release commit is already checked out in the source tree, so manual fast-forwarding of `main` cannot leave `/update` reporting the previous installed version.

## [0.2.040] - 2026-09-28

### Added

- Add opt-in `-900k` context variants for OpenAI Codex Sol, Terra and Luna, with model-aware prompt budgeting and wire-safe base model IDs.

- Require an explicit signed `--release` or development-only `--unsafe-main` when the installer must clone its source; document verifying the signed release before executing first-install repository code.

- Snapshot the operational SQLite database with the online backup API before verified update activation; add explicit manual backup and offline restore commands with integrity checks and pre-restore preservation.

### Changed

- Document and regression-test two-key release-signing overlap, rotation, revocation, and compromise recovery without publishing a synthetic production backup key.

- Confine the third-party Hindsight SDK to numeric loopback with explicit proxy bypass, retire misleading external Hindsight allowlist settings, and document transport-specific egress guarantees.

- Move session-scoped Story reasoning controls from `/settings` into `/providers` beside Utility reasoning, preserving the existing budget and preset values.

### Fixed

- Normalize recognized provider HTTP, timeout, and network failures at the provider boundary into sanitized typed errors, then surface actionable messages consistently in story jobs, Telegram character optimization, and Mini App optimization without exposing upstream bodies, URLs, credentials, or filesystem details.


## [0.2.039] - 2026-09-28

### Added

- Add independent session-scoped Utility reasoning controls to `/providers`, reusing the canonical None/Low/Medium/High/Max/Custom budgets for Utility-model summaries, curated memory, scene state, Light Novel strategy B choices, and character rank/optimizer work.

### Changed

- Ratchet security CI with a non-shrinking typed-surface baseline, dedicated coverage floors for authentication/network/update/environment modules, and SHA-pinned full-history secret scanning.

- Show the active Story and Utility models plus both reasoning budgets in a Telegram block quote when `/providers` opens.
- Keep one active management panel per actor and chat/topic: opening a new command panel or confirmation closes the previous one while leaving Light Novel choices and conversation artifacts alone.
- Keep `/new` non-destructive to the previous conversation while closing stale management UI before session-name input.

### Fixed

- Make `/reset` best-effort delete tracked user inputs, assistant replies, open choices, and consumed Light Novel choice quotes for the active session, including choice panels that were already consumed.
- Scrub bridge credentials from every helper subprocess environment while preserving only the process plumbing needed by TTS/ffmpeg, document parsing, runtime-health Git, Tailscale and self-update supervisor commands.
- Reject duplicate keys in the private environment file so appended allowlist or credential changes cannot be silently ignored; existing process environment variables retain precedence.

## [0.2.038] - 2026-09-28

### Fixed

- Optimize character PNGs with consistent duplicate `chara` or paired `chara`/`ccv3` metadata by updating every embedded copy while preserving schema-specific data and all unrelated PNG chunks; conflicting or malformed copies remain safely rejected.

### Changed

- Show registered character-rank icons and the current-session checkmark in the Character Info and Optimizer pickers, and include the cached rank label in their selected-character detail panels.

## [0.2.037] - 2026-09-28

### Added

- Add a private Mini App Usage view and current-session token overview with provider-reported input/output totals, cached/reasoning subsets, UTC daily figures, model/task breakdowns, 24-hour/7-day/30-day filters and explicit missing-data coverage. No historical estimates, billing prices or subscription quotas are inferred.
- Add a content-free, session-scoped SQLite usage ledger through forward migration 3, with 90-day pruning on tracked writes and deletion alongside its session. Back up before upgrade; restoring an older binary requires a matching pre-upgrade database.

### Changed

- Redesign the Mini App as an active-session-first native workspace: local SVG icons, theme-aware light/dark surfaces, responsive desktop sidebar/mobile tabs, searchable More tools, on-demand page modules, safe-area handling, keyboard focus and reduced-motion support. Keep existing protected management workflows and full character artwork.
- Capture usage through explicit provider-port scopes for story, rewrite, choice, image, continuity and character-utility work. Normalize OpenAI-compatible, Anthropic, OpenCode and Codex counters without double-counting cumulative stream snapshots or cached/reasoning subsets.

### Fixed

- Prevent stale page loads from clearing a newer navigation's loading state. Keep large token counts readable on narrow cards while retaining exact accessible figures.
- Treat interrupted streams and failed continuation requests as incomplete usage coverage while preserving known counts and the existing response behavior.

## [0.2.036] - 2026-09-27

### Fixed

- Make Mode A Light Novel recover missing or invalid inline choices automatically through the existing durable choice-only Story-model worker, keep the committed story pending during recovery, distinguish pending from final failure in Telegram, accept one unambiguous trailing choices-only JSON envelope, and log content-free inline rejection reasons/counts.
- Show the active character portrait in the Mini App Home session card and preserve complete character artwork in the Characters tab instead of center-cropping it.

## [0.2.035] - 2026-09-27

### Added

- Add an independent native OpenAI Codex OAuth provider with device login, private rotating credentials, CLI status/logout and static model routing. Interactive SSH login does not require a browser on the server.

### Changed

- Render `/character` with standard Telegram inline keyboards and registered custom-emoji rank icons directly before each character name, removing the separate rank column and retired RichMessage compatibility paths.
- Add a Mini App reasoning selector while retaining canonical numeric reasoning-budget validation.
- Add sanitized Humanizer rejection diagnostics with reason and source/candidate character counts. Rewrite safety thresholds and original-response fallback are unchanged.

### Fixed

- Recover Mode A Light Novel narratives from one trailing fenced JSON envelope without accepting ambiguous multi-fence output.
- Increase separate Light Novel choice requests to a 60-second per-request timeout, with one controlled transient-error retry and content-free failure diagnostics. Preserve committed stories, strict choice validation and strategy routing.
- Harden Codex OAuth file handling against links and oversized state, serialize refresh rotation with bounded lock acquisition, and restrict native bearers to the official Codex endpoint.
- Bound Codex stream reads, require completion, prefer canonical completed output, throttle previews and honor cancellation before credential/network work. Provider error messages are not exposed to chats or logs.

### Notes

- Native Codex login requires an eligible OpenAI account and device-code authorization. Automated tests use local fixtures; interactive OAuth authorization and account-specific live inference are not part of release verification.
- No runtime dependencies changed from v0.2.034. Publishing this release does not update a running deployment or enable Humanizer/Codex for existing sessions.

## [0.2.034] - 2026-09-27

### Fixed

- Refresh the registered S/A/B/C/D Telegram rank custom-emoji IDs after the set was republished, while retaining the same canonical 100×100 WEBM media and Mini App assets.

### Added

- Reuse the canonical animated S/A/B/C/D rank WEBMs in the Mini App character grid, with reduced-motion/static fallback and resilient unavailable-portrait handling.

### Changed

- Replace the Mini App's raw reasoning-budget field with None/Low/Medium/High/Max presets and a Custom input, while preserving the canonical numeric session setting and existing saved values.
- Redesign the Mini App as a Telegram-native control center: compact live-status header, responsive desktop sidebar/mobile bottom navigation, More sheet, session-focused dashboard, skeleton loading states, native mobile dialogs, and portrait-first character cards without changing API or page contracts.
- Refocus `README.md` as a compact user entry point and move detailed installation, configuration, usage, operations, update, and troubleshooting material into linked guides under `docs/`.
- Retire completed internal Superpowers implementation plan/spec files from the distributed repository; active maintenance, security, release, example, asset, CI, and systemd artifacts remain tracked.

## [0.2.033] - 2026-09-27

### Added

- Add the opt-in Telegram Mini App: server-validated Telegram identity, private-chat scoping, localhost-only listener, same-origin security headers and native theme integration.
- Add Character Manager portraits, search, protected deletion, upload previews, new-session selection and digest-bound optimizer previews with manual suggestions and Apply/Discard.
- Add Story/Utility model selection, validated generation settings and private presets; native Session/Persona/World management with revision checks and backups.
- Add continuity-summary and curated-memory editing, explicit Hindsight sync/search/purge, and private Data Bank uploads, search, version activation/removal and reindex operations.
- Add the System dashboard with observed polling health, immutable boot identity, durable actor-owned operations and expiring verified-update review.
- Add re-runnable user-scope `install.sh` with hash-locked Python setup, env-only provider configuration, starter resources, private-state preservation and user systemd.
- Add `--with-tailscale-funnel`: discover the authenticated node, fill a blank Mini App URL, reuse an exact public proxy or choose an unused HTTPS port, check backend authentication readiness and verify persistent direct Funnel routing.

### Changed

- Standardize Mini App HTTPS deployment on Tailscale Funnel directly to the loopback bridge. Remove the former proxy installer/configuration generator; preserve existing private Serve routes and unrelated system services.
- Clarify how Light Novel strategy A uses the final compacted narrative prompt, while B/C use a bounded snapshot of session context and the committed story.

### Fixed

- Bind Mini App forms and pending confirmations to their rendered session instead of mutable global selection; reject stale-session mutations. Add locked development-only UI interaction tests to CI.
- Allow completed optimizer previews to be reopened without a second model call; preserve actor/session/revision checks when applying.
- Persist update acknowledgement before scheduling restart. Confirm completion only after the replacement process successfully polls Telegram with the expected loaded commit/version; ignore malformed or expired state.
- Keep installer diagnostics free of provider-controlled credential names and preserve forum-topic scope in update notifications.

### Installation note

- This release changes `requirements.lock` from v0.2.032. Existing installations must stop the bridge, update the clean checkout and run `install.sh`; the signed `/update` dependency guard remains enforced. Tailscale installation/login and tailnet HTTPS/Funnel authorization are external prerequisites, not silently provisioned by the bridge.

## [0.2.032] - 2026-09-27

### Changed

- Show each Light Novel action in full inside the choice-panel message and replace long inline-button labels with compact numbered `1`–`4` selectors, keeping the existing durable callbacks and **⏭ Next Scene** action.

## [0.2.031] - 2026-09-26

### Added

- Add a permanent **⏭ Next Scene** action below Light Novel’s random 2–4 choices. It consumes the current choice panel once and submits a fixed narrative instruction that advances without speaking, deciding, or acting for the user character until that character can meaningfully participate again.

### Changed

- Render a consumed Light Novel choice or **⏭ Next Scene** panel as a Telegram block quote of the visible selection, without a `Selected:` prefix. Escape generated labels for Telegram HTML while leaving the durable submitted user-turn text unchanged.

## [0.2.030] - 2026-09-26

### Added

- Add a staged standard-session conversation wizard: Character → Normal/Light Novel → optional A/B/C strategy → Persona → World → System Prompt → Session → Apply, with no partial session mutation.
- Add dedicated `/lightnovel` controls and durable random 2–4-choice panels after the opening greeting and subsequent story turns. A embeds choices with the story; B uses the Utility model; C makes a second Story-model call.
- Consume choices and enqueue the corresponding user turn atomically. Persist ready choices/counts across restart, reject stale/replayed/foreign callbacks, and retry choice generation without regenerating committed narrative.
- Prefer a Bot API 10.3 RichMessage Character Menu so the hardcoded rank custom emoji can animate inside disabled rank buttons; automatically fall back to the classic inline-keyboard panel when RichMessage delivery is unavailable.
- Check in public GIF/WEBM references for the hardcoded character-rank custom emoji, with SHA-256 provenance and rank-to-`custom_emoji_id` mapping.
- Add a three-column Character Menu layout (`rank | character | action`) with silent S/A/B/C/D rank buttons using the bot-owned animated rank custom-emoji set registered from the project GIFs; IDs are hardcoded and require no `.env` configuration.
- Add Auto Optimize / Manual Suggestion character-optimizer choices. Manual guidance is bounded and bound to the initiating actor, session, character revision and expiry; previews can request another guided draft before Apply.
- Add an opt-in, per-session Humanizer pass after language rendering, with bounded provider requests, conservative text-preservation checks and default-off metadata persistence. Include upstream attribution and a pinned-reference refresh specification; no scheduler or automatic prompt promotion is installed.
- Rank uploaded character cards S–D with the utility model and show the tier only in the dedicated rank column of the main Character Menu. A new ⚡ Optimizer menu rewrites a card's text fields with the utility model and shows a preview before applying (with a verified backup) or discarding.

### Changed

- Rotate the SSH release-signing key used by signed update tags and refresh the documented trusted-maintainer public key/fingerprint.
- Standard sessions now require `/start` after `/new` or `/reset`; `/start` only selects the opening greeting once, and plain `start` is no longer an alias. Management commands/input remain usable before starting. Existing non-empty sessions are backfilled as started once.
- Reset keeps conversation configuration while invalidating choices and queued narrative work from the prior reset epoch. Character/group management, Normal generation, RAG, memory and Humanizer keep their canonical execution paths.

### Fixed

- Close the Session panel reliably when Cancel is pressed, and remove temporary `⏳ Command queued.` notices after command execution, including jobs recovered after restart.
- Strip presentation HTML from streaming previews as well as completed bot replies, and finalize replies in the existing preview message so raw tags and temporary duplicate responses do not appear during generation.
- Rename optimizer preview follow-up from Manual Suggestion to **Revise**, add an explicit revision-prompt instruction after the displayed base values, and use clear tier-specific badge-and-letter labels as the RichMessage/classic rank fallback without duplicating animated and static icons.
- Remove legacy rank prefixes from Character Info and Optimizer pickers, and make Manual Suggestion revisions chain from the current temporary preview while preserving exact-byte Apply validation against the installed original.
- Replace the two Humanizer On/Off buttons with one session-scoped ON/OFF toggle, and keep optimizer Apply reranking the new card revision while invalidating stale cached ranks on ranking failure.
- Normalize model-generated presentation HTML into readable Telegram-safe plain text before persistence and delivery while preserving literal HTML inside inline/fenced code, HTTP(S) link destinations, and Markdown URL/email autolinks.
- Keep Manual Suggestion pending state actor-scoped in shared chats so one user's optimizer guidance cannot replace another user's pending prompt.
- Preserve original character-card backups and use atomic, checksum-checked replacement for confirmed uploads and optimizer results. Bind approval to a unique user/session/content proposal; refuse stale/replayed previews and keep staged files outside the card catalog.
- Validate utility-model rank syntax, bind cached ranks to file revisions, whitelist optimized fields and provide complete paginated previews. Preserve the original queued session and actor for document import and recovery.
- Re-uploading a character card whose name already exists no longer silently skips it or auto-creates a versioned copy. It now stages the upload and asks for confirmation to overwrite, keep the existing card, or save as a new version.

### Architecture

- Add an explicitly composed RAG service and embedding port; separate bounded document extraction, embedding transport, indexing/query use cases and SQL-only Data Bank repositories. Retire the old RAG aggregation/re-export modules and extend dependency/type guards across the new boundaries.
- Preserve caller-owned rollback for Data Bank version activation, removal and query-embedding cache updates; those helpers no longer commit an enclosing transaction.
- Retire aggregate database/repository facades in favor of domain SQL owners and explicit transactional use cases. Metadata, jobs, operation phases, settings/presets, panel bindings and sync identities now preserve enclosing rollback; replace the lock-only write callback with real short transaction scopes.
- Separate character, session, World Info, provider/model, settings, conversation and sync callback domains. Keep ordered routing and all callback identifiers, actor/session binding, photo/text fallbacks and Back/Close behavior unchanged.
- Separate session lifecycle/repository, session deletion views and native imports from Telegram transport. Compose a required SessionService for ingress, workers and Persona selection; session creation and updates participate in caller-owned transactions.
- Resolve models only through configured providers and known model IDs, including opted-in discovery results; reject ambiguous/unknown routes before startup rather than guessing `provider-one`. Check-only startup no longer updates Telegram’s command list.

### Maintenance

- Separate settings, voice, Data Bank, group, persona, provider, media and generation workflows into canonical owners; retain panel routing and delivery behavior without compatibility re-export modules. Response-variant writes now preserve caller-owned transactions.

## [0.2.029] - 2026-09-25

### Fixed

- Schedule self-update restarts through a transient user-systemd timer outside the bridge service cgroup, preventing successful restarts from being misreported as failures when `KillMode=control-group` terminates child processes.

## [0.2.028] - 2026-09-25

### Added

- Show the selected native character-card PNG in Telegram Character Info, with Back/Close controls and a text-only fallback when Telegram cannot render the image.

### Configuration cleanup

- Remove empty built-in provider URL/model fallback stubs, give empty catalogs actionable panel guidance, and report missing Chat Completions endpoints before network access. Clarify GitHub-managed CodeQL Default Setup and the privacy of stored diagnostics.

### Persistence

- Require caller-owned transactions for repository writes; conservatively serialize commented/CTE/PRAGMA statements and raw cursor calls, retain writer ownership after statement failures and through RETURNING results, and release it correctly on transaction/context completion.

### Fixed

- Accept wrapped OpenAI-compatible streaming SSE events with `data.choices`, matching the already-supported non-streaming response envelope.
- Enforce manual group-turn ownership on native Telegram edits at ingress and execution, including recovered jobs and edits to a message in another session.

### Security

- Pin declared CI actions to verified full commit SHAs and lock development/audit tooling with hashes; audit both complete locks without re-resolving dependencies.

### Changed

- Make provider health and model refresh panel-only actions; remove misleading text aliases and their queue classification.

### Documentation

- Refocus README on installation, complete environment/provider configuration, signed updates, release downloads, and user troubleshooting; keep developer internals in CONTRIBUTING/SECURITY.
- Expand `.env.example` to cover every supported user setting and remove the unused `image_default_size` provider example.

## [0.2.027] - 2026-09-25

### Maintenance

- Publish the first SSH-signed release under the hardened updater trust policy. Runtime code is unchanged from v0.2.026.

## [0.2.026] - 2026-09-25

### Maintenance

- Consolidate overlapping architecture/quality regression files into their canonical policy suites, remove exact duplicate historical tests, and explicitly ignore local pytest/mypy/Ruff caches. Runtime ownership modules and intentional example files remain unchanged.

## [0.2.025] - 2026-09-24

### Maintenance

- Add security disclosure and contribution guidance aligned with the actual preproduction workflow, with no invented contacts or response-time promises.
- Configure bounded weekly pip and GitHub Actions dependency proposals; document explicit regeneration of the custom hash-pinned runtime lock and add an offline manifest/lock compatibility gate.
- Guard the reviewed public-example hashes and reject unexpected external Python socket connections during tests.

### Configuration

- Replace environment-derived module globals with explicit immutable application settings. Both entry points perform the same runtime bootstrap, provider callbacks capture their own settings, native API clients are operation-owned, and Persona reads no longer share a process-global configuration cache.
- Migrate tests to explicit per-test settings snapshots and fail the suite on unhandled worker-thread exceptions.

### Architecture

- Retire the shared common module in favor of canonical topic, logging, scheduling and fixed-limit owners; extract SQLite mechanics and grouped panel commands with enforced dependency direction.
- Keep the relocated function bodies and resource budgets unchanged; correct runtime permission setup to use the configured environment file and validated permission boolean.

- Define exact core port call contracts and canonical request values, remove the redundant composition factory, and bind conversation and leaf-command dependencies explicitly.
- Route Live Sync snapshot retention through the configured memory service rather than an unbound memory backend; add regression coverage for the retention/provider binding.

### Quality

- Measure full-package statement and branch coverage including subprocesses, enforce a measured 68% combined baseline, publish JSON/XML reports, and retain explicit security-regression contracts.

- Enforce whole-tree Ruff linting, security rules and formatting; separate incremental type coverage from graph isolation and include both security policy modules.

### Security

- Require independently trusted SSH-signed release tags, bounded isolated preparation, version-bound confirmation and safe managed update targets. Use structured outcomes, retain the previous mirror for recovery, and report partial activation or restart failure explicitly.

- Verify opened environment-file ownership, private permissions, file type and size; apply assignments atomically and validate bounded numeric settings with redacted, actionable startup errors.

- Replace deterministic callback IDs with random chat-scoped handles, remove the process cache, and preserve caller transaction ownership during token creation and lookup.

- Require explicit external provider hosts, a separate LAN/tailnet grant, DNS-pinned connections with verified hostname TLS, same-origin redirects, and no implicit proxy routing. Existing installations must configure their external-host allowlists.

## [0.2.024] - 2026-09-24

### Fixed

- Fixed `/update` release-state detection so an empty Unreleased section is not reported as local unreleased changes (#121).

## [0.2.023] - 2026-09-24

### Added

- Added a user-scoped Linux installation guide covering the runtime virtual environment, startup validation, user-level systemd persistence, service logs, and optional login lingering (#115).
- Expanded interactive `/help` coverage for direct command forms, subcommands, `/cancel`, parameterized lookups, and accepted command aliases (#112, #113, #117).

### Changed

- Refreshed current-product documentation, removed retired migration artifacts, and simplified README command tables so `/help` is the canonical detailed command reference (#111, #112, #114, #117).
- Aligned the systemd example with the documented user-scoped `.venv`, environment file, bridge data, and source paths (#115).
- Renamed internal Live Sync migration-era identifiers and logs to current terminology, removed the obsolete raw `settings_input` compatibility path, and consolidated architecture regression guards around current boundaries (#116).

### Fixed

- Fixed Help lookup for parameterized commands and aliases, corrected System Prompt help to match native JSON/TXT support, and clarified panel-only behavior for streaming and voice toggles (#117).
- Moved updater live staging under the user-scoped bridge home by default so the hardened user systemd service can self-update without writing to the retired Hermes scripts path (#119).

## [0.2.022] - 2026-09-24

### Added

- Added Cline-free provider pass-through and Cline-PASS response-envelope handling for native API relay flows (#109).
- Added streaming continuation support across provider boundaries (#108).
- Added Director policy injection with explicit boundaries for group customization (#107).
- Added static architecture enforcement to prevent back-edge regressions through the application layer (#106).

### Changed

- Refactored update routing into a decomposed service boundary with explicit provider-first model selection (#105).
- Refactored final cycle retirement to remove the last runtime back-edge into the application boundary (#104).
- Refactored final input flow into an explicit service boundary (#103).
- Refactored native API scene delivery into its own boundary port (#102).
- Refactored help/memory delivery into a dedicated v2 boundary (#101).
- Refactored world and curated memory into an explicit delivery boundary (#100).
- Refactored UI runtime cycle into a self-contained boundary wave (#99).
- Refactored Director goal delivery into a dedicated boundary (#98).
- Refactored reset command to enforce session/task ownership (#97).
- Refactored input flow into an explicit service boundary (#96).
- Refactored language runtime into a dedicated boundary (#95).
- Refactored generation delivery into its own port (#94).
- Refactored model router into an explicit provider port (#93).
- Refactored group service into a v2 boundary with explicit ownership (#92).
- Retired the application backedge for native API flow, completing the boundary refactor wave (#91).

### Fixed

- Fixed Cline-free provider response envelope handling to preserve stream continuity.
- Fixed model router provider port to resolve transient routing fallbacks.

## [0.2.018] - 2026-09-20

### Added

- Added injected PersonaService, SyncService, and JobService application boundaries while preserving compatibility with existing callers.
- Added regression coverage for Persona lifecycle safety, Live API Sync routing, and durable job intake and recovery.

### Fixed

- Made Persona creation retry-safe and cleaned up newly created Personas when session selection fails.
- Serialized Persona selection, update, disable, and deletion operations to close lifecycle race windows.
- Preserved durable job ordering, idempotency, recovery, and worker boot behavior through JobService.

### Changed

- Reduced SQLite contention by keeping RAG embedding work outside write transactions, reusing the realtime sync worker connection, and using a smaller page-cache budget for lightweight worker connections.

## [0.2.017] - 2026-09-19

### Added

- Added an explicit extension registry and Director policy boundary for Group Director customization, with validation and runtime integration.
- Added regression coverage for Director policy behavior, extension registration, group execution invariants, and SQLite contention.

### Fixed

- Serialized SQLite write transactions to reduce contention between concurrent workers.
- Prevented successful native message edits from being reported as rollback failures.
- Preserved Director instruction non-disclosure and behavior-priority semantics across customization and execution.

### Changed

- Made model target selection provider-first and separated story-model selection from utility-model selection.
- Removed the obsolete standalone release-notes file.

## [0.2.016] - 2026-09-19

### Added

- Added explicit duplicate and new-version notifications for uploaded character cards; changed cards are retained with content-hash filenames.

### Changed

- Made `/status` text-only with a formatted Telegram report and removed the obsolete status drilldown panel.
- Externalized privacy-sensitive character, model, user-name, provider, and TTS defaults from source code into environment configuration.
- Removed character and model identity details from startup logs.

## [0.2.015] - 2026-09-19

### Added

- Added World-style per-item panels for Persona, Preset, Data Bank document, and Group character management.
- Added confirmation before deleting Presets and removing Data Bank documents.
- Added confirmation before removing a character from a Group without deleting the native character card.
- Added regression coverage for the managed item-panel layouts.

### Fixed

- Fixed Telegram World panel delivery by preserving the bot token while generating per-item callback tokens.
- Retried one transient Telegram `sendMessage` 404 before reporting a command failure.

### Changed

- Session and Character panels now show select and delete actions on each item row, with in-place refresh after deletion.

## [0.2.014] - 2026-09-19

### Added

- Added covering indexes for cleanup deletes: `callback_tokens_expires_idx` on `callback_tokens(expires_at)`, `panel_sessions_expires_idx` on `panel_sessions(expires_at)`, `operations_state_idx` on `operations(state, updated_at)` to prevent full table scans on every schema init.
- Added `/world` panel actions for uploading validated native World Info JSON and deleting inactive lorebooks with confirmation and reference protection.

### Fixed

- Translated leftover Indonesian-language user-facing error messages (voice/image/character-card/Data Bank size and format limits) to English for consistency with the rest of the bot's UI copy.
- Fixed `/update` version display so an `Unreleased` changelog is compared against its latest released base without overwriting local development changes.

## [0.2.013] - 2026-09-18

### Added

- Added per-session utility-task model routing, invisible Group Director mode, Data Bank document versioning, and budget-aware context compaction.
- Added read-only status/prompt/scene panels, Director-goal and curated-memory panels, summary confirmation, and a synchronous Help fast path.
- Added bounded RAG retrieval improvements, optional SQLite vector support, and safer runtime override loading.

### Changed

- Improved SQLite maintenance, worker connection reuse, shutdown cleanup, and query-planner maintenance.
- Consolidated panel, recovery, memory, card, catalog, and session-column helpers without changing their public behavior.
- Improved default character-name fallback and user-facing Help/README guidance.

### Fixed

- Preserved Data Bank embedding metadata during reindexing and kept legacy embedding rows readable.
- Added lifecycle cleanup coverage for background work and Hindsight session data.

## [0.2.012] - 2026-09-18

### Changed

- Added SQLite connection performance pragmas (`synchronous = NORMAL`, `temp_store = MEMORY`, `cache_size = -64000`, `foreign_keys = ON`); worker connections stay connection-local while WAL and optional extension setup happen once on the schema-init connection.
- Added query planner maintenance (`optimize_database`, `PRAGMA optimize`) after bulk deletions, and dedicated end-of-shutdown disk reclamation (`run_database_maintenance`) with incremental auto-vacuum initialization and a bounded-timeout `VACUUM` on a separate connection.
- Added optional `sqlite-vec` vector extension detection and initialization support, with extension loading always disabled after the attempt.
- Removed the unused native-cache reset hook.
- Removed the redundant Live API Sync JSONL serialization/parsing round trip; API chat records are now validated and normalized directly.
- Kept bounded message/transcript validation, metadata handling, and swipe variants while removing retired JSONL-transfer limits from shared configuration.
- Updated README, Help, environment examples, and provider configuration guidance to describe Live API Sync as the only conversation synchronization path.

## [0.2.011] - 2026-09-17

### Fixed

- Removed retired provider entries and stale model-cache data from the live bridge configuration.
- Added a Hive streaming chat health check for providers that do not expose a `/models` endpoint; JSON requests now include the required content type.
- Recovered streaming replies that end with `finish_reason=length` before producing visible content by retrying with bounded larger output budgets.
- Documented provider health behavior for endpoints without model discovery.

## [0.2.010] - 2026-09-17

### Fixed

- Improved incomplete response recovery for streaming chat providers and accepted both standard SSE `data:` framing variants plus text content blocks.
- Made `/update` a true no-op when the installed release already matches the latest release; it no longer syncs or restarts in that case.
- Renamed the Persona panel action to `Delete inactive` to match its protected-target picker behavior.

## [0.2.009] - 2026-09-17

### Fixed

- Persona deletion now mirrors Character deletion safety: the active Persona is excluded from the delete picker, and Personas referenced by another session are protected.
- Updated README and Help with the inactive/unreferenced Persona deletion rules.

## [0.2.008] - 2026-09-17

### Changed

- Added concise comments to provider and environment configuration examples so each field's purpose is clear.
- Expanded README and Help descriptions for Expressions, image generation, semantic splitting, User dialogue/User action formatting, and quote-driven voice replies.

## [0.2.007] - 2026-09-17

### Added

- Added native SillyTavern expression sprite discovery with session-scoped manual or automatic selection, change-only Telegram delivery, and neutral/fixed-avatar fallbacks.
- Added guarded `/imagine` prompt input and OpenAI-compatible Images API adapter; image generation remains opt-in through provider catalog fields.
- Added semantic Telegram message splitting that prefers paragraphs, newlines, sentence boundaries, and whitespace while preserving the UTF-16 limit.
- Added User dialogue/User action prompt formatting for single-star action spans without changing stored transcript text or user role semantics.

### Changed

- Automatic voice replies now synthesize only model dialogue enclosed in straight double quotes; narration and unquoted text are not spoken.
- Disabled the manual `/tts` command; `/voice` controls automatic quote-driven voice replies.
- Expanded README, Help, provider examples, and environment examples with concise configuration guidance.
- Removed live deployment endpoint and port details from the changelog.

## [0.2.006] - 2026-09-17

### Added

- Added a dedicated keyless OpenCode Muse Free transport using the `/responses` endpoint, canonical OpenCode session/request headers, low reasoning effort, and a bounded output-token floor.
- Added panel-first input flows for `/edit`, `/remember`, `/macro`, and `/tts`, with session binding, expiry cleanup, validation, and `/cancel` support.
- Added Memory search, Data Bank search, and a safe `/stscript` Reset action panel.
- Added native Persona deletion with confirmation, reference cleanup, and avatar preservation.

### Changed

- `/note` is now the sole Author's Note interface; the duplicate STscript Note action was removed.
- `/status` now shows the custom session title followed by the technical session ID.

### Fixed

- OpenCode Muse Contributor Free generation now succeeds through the bridge adapter instead of returning HTTP 403.

## [0.2.005] - 2026-09-17

### Changed

- Made Persona storage fully native to SillyTavern: settings and avatars are the source of truth, SQLite stores only native avatar references, and the bridge catalog is archived.
- Removed Persona import/export controls and obsolete bridge catalog scope; native Persona create/edit now writes directly to SillyTavern storage with verified backups and readback.

## [0.2.004] - 2026-09-17

### Added

- Added validated naming before creating normal, Character-driven, or topic-local group sessions; cancellation leaves no empty session behind.
- Added deterministic session-prefixed Hindsight document IDs, local document mapping, and fail-closed targeted memory cleanup when deleting an inactive session.
- Added native persona interoperability through SillyTavern's authenticated settings API and native User Avatars directory, with verified backups, atomic writes, and readback checks.
- Added safe recovery of session character references after native card renames using a unique PNG image fingerprint or unique embedded card name.
- Added a topic-only New group session wizard that chains Character and World Info selection without colliding with ordinary session flows.

### Changed

- Character cards and World Info now use SillyTavern's native local paths directly; bridge JSON remains cache/fallback state where needed.
- Increased Character, Persona, System Prompt, and World Info catalog limits to 40 entries.
- Removed automatic chat-file synchronization, manual JSONL Export/Import, document-import routing, recovery state machines, fallback-only database helpers, and their panel controls. Live API Sync is now the only conversation synchronization path.
- Simplified `/sync`, README, and Help around Live API Sync only.

### Fixed

- Rebound the Character panel to a uniquely renamed native card and refreshed its embedded display name.
- Treated an unchanged Character panel refresh as a successful idempotent action instead of reporting `Callback processing failed`.
- Normalized isolated invalid Persona entries without blocking the catalog, surfaced malformed-catalog warnings in the panel, and preserved newer concurrent edits when native export rollback was needed.
- Enforced fixed response-language selections with a final bounded render pass across normal, image, edit, regenerate, and continue paths; Auto mode remains single-pass and fixed-language streaming previews are suppressed.
- Explicitly disabled hidden reasoning on OpenRouter when the configured reasoning budget is zero, and automatically requested one bounded continuation when a non-stream response stopped at its output-token limit.
- Changed `/reset` to clear only the active session's SQLite conversation and session-scoped Hindsight documents; other sessions and the shared per-chat Hindsight bank are preserved.
- Enforced Hindsight recall as active-session-only, ignored legacy broader scope metadata, and removed user/character scope choices from the memory panel.
- Removed the obsolete Hindsight scope panel; `/memory search <query>` and `/remember <fact>` remain text-input commands, while bridge recall is verified against the live Hindsight API.
- Moved Persona metadata to native SillyTavern settings and native User Avatars; bridge SQLite now stores only the native avatar reference, and the bridge JSON catalog is archived.
- Kept session and group setup callback state scoped to the correct chat, topic, and session.

## [0.2.003] - 2026-09-16

### Added

- Added Telegram persona editor for creating and editing persona name/description with scoped input, backup verification, and atomic JSON writes.
- Added opt-in Phase 3 near-real-time bidirectional sync through SillyTavern's supported loopback HTTP chat API with cookie/CSRF authentication, conflict detection, and Phase 2 fallback.

### Changed

- Added a persona information review step before editing, with separate name, description, or combined edit actions.
- Added opt-in Phase 2 file synchronization with checkpoints, conflict detection, edit/delete transfer, and compatible swipe transfer.

### Fixed

- Show visible chat feedback when a queued callback reaches an expired panel, while preserving callback-toast behavior for non-queued callbacks.
- Removed expired panel bindings before rejecting stale actions and classified swipe callbacks explicitly as session-scoped.
- Serialized persona catalog read-modify-write operations globally and used unique temporary files for atomic replacements.
- Treated an unchanged model/provider/world panel as an idempotent refresh instead of reporting `Callback processing failed` after a successful model refresh.

## [0.2.002] - 2026-09-16

### Added

- Added Phase 1 manual bidirectional sync panel at `/sync`.
- Added stable per-session sync IDs and transfer checkpoints in SQLite.
- Added `bridge_sync` metadata to SillyTavern-compatible JSONL exports.
- Added safe JSONL imports as separate sessions, preserving the active session.

### Changed

- Removed standalone `/export` and `/import` commands; use the `/sync` panel for SillyTavern transfers.
- Reworded Help navigation and detail pages with task-based category names and explicit action guidance.

### Documentation

- Documented the Phase 1 sync scope: transcript, title, character reference, persona, World Info references, Author's Note, and compatible generation settings.

## [0.2.001] - 2026-09-16

### Added

- Added manual group user-turn gating with Claim turn and Pass user turn actions.
- Added topic-only New group session wizard chaining Character and World Info selection.
- Added Help category → command → detailed-information drill-down panels.

### Fixed

- Made repeated group Disable actions idempotent when Telegram reports that the panel is unchanged.

## [0.2.000] - 2026-09-16

### Fixed

- Displayed current generation field values in the Settings panel after valid input.
- Tracked and removed invalid-input feedback together with its pending prompt on `/cancel` or Close.
- Added Character → Session chaining so a selected character is applied only to the chosen target session.
- Extracted pending-input, command-routing, and reply-persistence flows from the main message handler.
- Added pinned pytest development tooling and CI execution.

## [0.1.107] - 2026-09-16

### Fixed

- Routed the shared inline-keyboard removal helper through panel close/delete.
- Made every panel Cancel/Close path remove its message and binding instead of leaving stale text.
- Preserved valid `Panel closed.` fallback behavior when Telegram rejects deletion.

## [0.1.106] - 2026-09-16

### Fixed

- Fixed panel close/delete callbacks to read nested Telegram callback message IDs correctly.
- Prevented `message_id=None` HTTP 400 errors and generic callback-processing failures.
- Added exact message-ID regression coverage for Author's Note and shared panel close paths.

## [0.1.105] - 2026-09-16

### Fixed

- Changed character-card Upload guidance to reuse the existing panel message with Back and Close buttons.
- Changed Character panel Cancel/Close to delete the panel message and clear its binding.
- Added regression tests for the upload guidance and close lifecycle.

## [0.1.104] - 2026-09-16

### Fixed

- Changed the shared panel Close action to delete the panel message and clear its session binding.
- Removed the `/settings <name> <value>` text mutation path and fallback instructions from the Settings panel.
- Added lifecycle coverage for Settings, Preset Save, and STT User input panels.

## [0.1.103] - 2026-09-16

### Fixed

- Fixed panel close/delete to read callback message IDs correctly.
- Made delete the primary panel-close operation, with a valid `Panel closed.` fallback when Telegram rejects deletion.
- Applied the same old-panel cleanup to Settings, Preset Save, and STT User input callbacks.
- Removed closed panel bindings to prevent stale callback processing failures.

## [0.1.102] - 2026-09-16

### Fixed

- Fixed Author's Note User input to close the original panel before waiting for text.
- Fixed Author's Note Cancel to remove the old panel binding and prevent stuck duplicate panels.
- Added regression coverage for panel close/delete ordering and removed alias behavior.

## [0.1.101] - 2026-09-16

### Fixed

- Removed pre-production compatibility guards and legacy message migration branches for `/model`, `/authornote`, and `/system`.
- Added one generic unknown-slash-command guard before model generation.
- Fixed Author's Note Cancel to close and delete the previous panel message.

## [0.1.99] - 2026-09-16

### Removed

- Removed the user-facing `/authornote` legacy alias.
- Kept `/note` as the only Author's Note command and added a safe migration response for old `/authornote` messages.

## [0.1.98] - 2026-09-16

### Changed

- Changed `/note` and `/authornote` to open an Author's Note panel instead of accepting direct text or `off` mutations.
- Added Off and session-scoped User input actions with expiry and `/cancel` support.
- Added regression coverage for panel routing and pending note input.

## [0.1.97] - 2026-09-16

### Added

- Added safe inactive-session deletion from the `/session` panel with a separate confirmation step.
- Protected the active session and sessions with queued or running jobs.
- Removed all local SQLite data associated with a deleted session while retaining shared chat Hindsight memory.

## [0.1.96] - 2026-09-16

### Changed

- Clarified `/reset` confirmation wording: SQLite reset is active-session-only, while Hindsight purge covers the entire Telegram chat bank.

## [0.1.95] - 2026-09-16

### Refactored

- Split the oversized `common.py` into `common.py` and `cards.py` while preserving the shared runtime loader namespace.
- Split the PDF regression test into its own test module.
- Enforced a maximum of 500 lines for every Python file; the largest current file is 485 lines.

## [0.1.94] - 2026-09-16

### Documentation

- Synchronized README and `/help` with the canonical `/providers` panel and removed `/model` command.
- Documented provider-action validation and the voice input Auto/User input language panel.

## [0.1.93] - 2026-09-16

### Fixed

- Routed `/stscript reset` through the reset confirmation and Hindsight purge flow.
- Rejected unknown `/providers` actions before normal generation.
- Added an STT language panel with Auto, fixed language choices, pagination, and session-scoped User input.

## [0.1.92] - 2026-09-16

### Removed

- Removed the user-facing `/model` command and its Telegram command-menu entry.
- Made `/providers` the single provider/model panel entry point.
- Kept a safe migration response for old `/model` messages so they cannot fall through to model generation.

## [0.1.91] - 2026-09-16

### Changed

- Removed direct text fallback handling for model, persona, preset, and branch selection commands.
- Preset use/delete now resolve only through panel callbacks; preset save uses the panel's two-step name input.
- Removed stale direct-selection documentation and added a regression assertion for the deleted preset text handler.

## [0.1.90] - 2026-09-16

### Changed

- Routed `/model <id>`, `/persona <id>`, `/preset use|delete <name>`, and `/branch <number>` to their panels instead of direct text mutations.
- Added a panel Save preset action with session-scoped, expiring two-step name input.
- Added panel regression coverage for selection redirects and preset creation.

## [0.1.89] - 2026-09-16

### Changed

- Changed `/reset` to require an explicit Telegram confirmation panel.
- Reset confirmation now purges and recreates the entire per-chat Hindsight bank before deleting session data.
- Failed Hindsight purge preserves the session and reports the failure instead of partially resetting.

## [0.1.88] - 2026-09-16

### Fixed

- Changed `/reset` to clear the active conversation and restart from the character card's opening greeting.
- Persisted the reset greeting as the first assistant message without invoking model generation.
- Added durable reset replay handling and regression coverage.

## [0.1.87] - 2026-09-16

### Security

- Isolated PDF Data Bank extraction in a resource-limited subprocess.
- Added bounded PDF parser input, page count, text output, CPU time, memory, and parent timeout controls.
- Added regression coverage for valid and invalid PDF parsing.

## [0.1.86] - 2026-09-16

### Added

- Added a session-scoped model response language panel and `/language` command.
- Added validated language persistence in session storage, including JSONL export/import.
- Added response-language instructions to model prompt assembly and status output.

## [0.1.85] - 2026-09-16

### Security and reliability

- Added crash-safe durable operation recovery for generation, continuation, edit, export, start, TTS, and JSONL import flows.
- Added durable scheduler handoff protection, bounded recovery batches, and stale swipe/session callback protection.
- Restored public Python 3.11 CI and audit regression tests.

## [0.1.84] - 2026-09-16

### Changed

- Removed the legacy Hindsight endpoint compatibility alias from the bridge script.
- Set the only supported Hindsight endpoint variable to `HINDSIGHT_API_URL`.
- Corrected the default local Hindsight endpoint configuration.
- Audited live bridge environment/configuration without exposing credential values.

## [0.1.83] - 2026-09-16

### Fixed

- Configured the bridge to use the supported Hindsight API endpoint.
- Verified Hindsight retain, recall, and delete behavior without exposing deployment details.
- Updated README and `.env.example` to use the actual Hindsight API URL setting.

## [0.1.82] - 2026-09-15

### Security and reliability

- Completed phase-aware replay handling for durable generation, edit, continuation, export, start, TTS, import, and group operations.
- Added atomic group response/variant/turn commits for text and image generation.
- Added durable callback token recovery and global scheduler/backlog bounds.
- Hardened character deletion references, prompt field limits, backup naming, and custom prompt permissions.
- Added embedding revision namespaces and untrusted Hindsight/RAG content boundaries.
- Expanded private crash/recovery regression coverage and documented canonical Python 3.11 lock policy.

## [0.1.81] - 2026-09-15

### Security and reliability

- Fixed PNG character-card Document routing before generic image analysis.
- Fixed Forum Topic panel binding scope and rejected expired/unbound session callbacks.
- Added durable callback tokens with SQLite persistence and expiry cleanup.
- Added operation identity to regeneration, continuation, edit, export, start, TTS, import, and group-turn paths.
- Hardened character deletion against default, session, and group references with verified pre-delete backups.
- Added group-turn recovery after committed assistant responses.
- Made `/continue` persistence and Telegram output consistent.
- Added global scheduler caps, bounded backlog batches, embedding revisions, character prompt limits, panel label limits, and phased JSONL import recovery.
- Moved recalled Hindsight content into an untrusted user-content boundary and documented PDF/process and Python lock policies.

## [0.1.80] - 2026-09-15

### Changed

- Combined model refresh into the provider panel.
- Added Provider health and Refresh models panel actions.
- `/providers health`, `/providers refresh`, and `/model refresh` now open the provider panel instead of running text-only actions.
- Updated README and `/help` provider workflow documentation.

## [0.1.79] - 2026-09-15

### Changed

- Removed legacy character text fallbacks for info, versions, restore, and delete.
- Character management commands are now panel-only; invalid legacy forms return a panel instruction instead of entering generation.

## [0.1.78] - 2026-09-15

### Added

- Expanded `/character` into a panel with character selection, metadata info, upload guidance, and safe two-step deletion of non-active cards.
- Kept legacy text commands for compatibility; panel actions use short callback tokens and preserve verified backups.
- Updated README and `/help` for the character panel workflow.

## [0.1.77] - 2026-09-15

### Security and reliability

- Added operation markers and deterministic replay handling for durable group/session/import/TTS side effects.
- Bound panel callbacks to the originating session and added expiring dynamic callback tokens.
- Made Forum Topic voice/document multipart delivery include the real chat ID and message thread ID.
- Moved PNG character-card detection into the durable document worker.
- Bounded per-chat scheduler memory and added SQLite backlog wake-up dispatch.
- Added JSONL message/transcript limits, TXT prompt permission enforcement, backup retention, export filename limits, and settings-input expiry.
- Added RAG embedding coverage status, untrusted-reference prompt boundaries, `defusedxml`, and a hash-pinned dependency lock.
- Updated documentation for TXT System Prompts, custom systemd paths, RAG allowlists, legacy JSONL prompt import, and best-effort auxiliary jobs.

## [0.1.76] - 2026-09-15

### Changed

- Removed the private runtime `systemprompt/` directory from the public repository.
- Added an ignore rule so private TXT System Prompt files cannot be committed accidentally.

## [0.1.75] - 2026-09-15

### Changed

- Converted public and private System Prompt files from JSON arrays to multiline TXT files.
- System Prompt panel choices now use `balanced.txt`, `concise.txt`, and `natural.txt`.
- Updated README and `/help` to document TXT-only examples.

## [0.1.74] - 2026-09-15

### Added

- Added multiline `.txt` System Prompt files.
- Each `.txt` file is one panel choice, labeled from its filename, and loaded without JSON newline escaping.
- Updated README and `/help` to document JSON and TXT System Prompt files.

## [0.1.73] - 2026-09-15

### Changed

- Removed development-only Bandit baseline, GitHub workflow, Ruff config, development requirements, and test suite from the public repository.
- Kept `requirements.txt` and `requirements.lock` for user installation and reproducibility.

## [0.1.72] - 2026-09-15

### Changed

- Changed each System Prompt JSON file to a JSON array of prompt lines.
- Loader joins array items with real newlines before panel selection/generation.
- Updated README examples and regression coverage for array loading.

## [0.1.71] - 2026-09-15

### Changed

- Changed System Prompt JSON files to contain only a raw JSON string.
- Panel labels now derive from each filename (`natural.json` → `Natural`).
- Added regression coverage for raw-string prompt loading.

## [0.1.70] - 2026-09-15

### Changed

- Removed the legacy `config/system_prompts.example.json` file.
- Kept only `config/system_prompts.example/` with one JSON file per System Prompt choice.

## [0.1.69] - 2026-09-15

### Changed

- Changed System Prompt catalogs to one JSON file per panel choice.
- Added support for single-prompt JSON objects with `name` and `prompt` fields.
- Split the private catalog into `balanced.json`, `concise.json`, and `natural.json` with a backup of the original multi-entry file.
- Added public directory examples under `config/system_prompts.example/`.

## [0.1.68] - 2026-09-15

### Changed

- Published the existing `natural.json` System Prompt catalog unchanged as the public `config/system_prompts.example.json`, per user confirmation that it contains no secrets and is publicly available.

## [0.1.67] - 2026-09-15

### Changed

- Added a sanitized generic `natural` System Prompt entry to `config/system_prompts.example.json`.
- Kept the live 27 KB `natural.json` prompt catalog private and outside Git.

## [0.1.66] - 2026-09-15

### Added

- Added two-step custom input for `/settings` fields, including reasoning budget.
- Added panel prompts, `/cancel`, and Python range/type validation for custom values.
- Added regression coverage proving custom settings input is consumed by settings instead of generation.

## [0.1.65] - 2026-09-15

### Fixed

- Normalized all common Telegram group command mention forms: `/command@bot`, `@bot /command`, and `/command @bot`.
- Prevented mentioned panel commands from falling through to normal generation.
- Added regression coverage for all three formats.

## [0.1.64] - 2026-09-15

### Changed

- Clarified `/settings` panel text with the active reasoning label and exact budget.
- Synchronized reasoning buttons with the canonical Python `REASONING_LEVELS` validator.
- Updated `/help` and README wording for session generation settings.

## [0.1.63] - 2026-09-15

### Fixed

- Normalized Telegram group command suffixes such as `/settings@botname` before routing.
- Ensured suffixed commands open the same panels as their private-chat forms instead of triggering normal generation.
- Added regression coverage for bot-addressed commands.

## [0.1.62] - 2026-09-15

### Fixed

- Added job/session replay hardening for deterministic `/new` operations and already-delivered replies.
- Added strict provider/catalog/RAG redirect validation.
- Added RAG reindex/backfill and source-aware citation truncation.
- Added SQLite busy timeout, job/failed-turn retention, STT model initialization locking, log rotation, response-variant turn IDs, stale-summary edit protection, and character filename caps.
- Fixed the default systemd SillyTavern writable path and clarified enum panel boundaries.

### Added

- Added a generation settings reasoning panel and Data Bank reindex action.

## [0.1.61] - 2026-09-15

### Changed

- Updated `/help`, Telegram command registration, and README for enum panels.
- Documented free-form text boundaries and Python validation for non-enum values.
- Documented pagination for preset and Data Bank option panels.

## [0.1.60] - 2026-09-15

### Added

- Added enum panels for streaming, TTS, STT mode/model, Hindsight memory mode/scope, presets, and Data Bank actions.
- Kept free-form values such as queries, facts, notes, preset names, language codes, and numeric generation settings under Python validation.
- Updated README and `/help` to distinguish panel enums from free-form text commands.

## [0.1.59] - 2026-09-15

### Added

- Added the `/group` control panel with Add/Remove character, Choose speaker, Mode, Enable/Disable, and Next speaker actions.
- Added panel-based character selection for group membership with 8 items per page.
- Updated group help and README documentation.

## [0.1.58] - 2026-09-15

### Changed

- Made System Prompt selection fully panel-only.
- Removed `/systemprompt list`, `/systemprompt use`, and arbitrary text fallback routing.
- Updated README, `/help`, and Telegram command registration accordingly.

## [0.1.57] - 2026-09-15

### Added

- Added Telegram Forum Topic scoping using `message_thread_id`.
- Isolated sessions, history, queues, group state, media, callbacks, and replies per topic.
- Added topic-aware Telegram message delivery and regression coverage.


## [0.1.56] - 2026-09-15

### Fixed

- Enforced exactly one submitted/in-flight job per chat for strict FIFO execution.
- Woke durable chat queues whenever any background capacity is released.
- Removed delayed duplicate callback acknowledgments from queued callback workers.
- Prevented recovery from re-sending assistant replies with committed Telegram delivery IDs.
- Added regression coverage for three-job FIFO interleavings and replay delivery.

## [0.1.55] - 2026-09-15

### Changed

- Clarified provider adapter behavior in README and `/help`.
- Documented that adapter-enabled providers can generate, while catalog-only entries are view-only.
- Documented 8-item provider/model panel pagination.

## [0.1.54] - 2026-09-15

### Added

- Added multi-world World Info panel selection with toggleable active lorebooks.
- Added multi-lorebook prompt merging, session status display, and list-compatible chat export/import.
- Added `Done` and `Clear all World Info` panel actions.

## [0.1.53] - 2026-09-15

### Added

- Added eight-item pagination to dynamic inline panels for characters, personas, sessions, World Info, System Prompts, providers, and models.
- Added Previous/Next navigation callbacks while preserving active-item markers.

## [0.1.52] - 2026-09-15

### Changed

- Made World Info/lorebook selection panel-only through the inline keyboard.
- Removed text-based lorebook selection and text entry-editor fallbacks.
- Updated `/help` and README command references to match the panel-only flow.

## [0.1.51] - 2026-09-15

### Added

- Routed state-mutating commands, callbacks, and native edits through durable per-chat FIFO jobs.
- Added continuous queued-job draining when worker capacity becomes available.
- Added failure-safe native edit regeneration.
- Added embedding namespaces, BM25 rank ordering, and single-retrieval context/citation bundles.
- Made CI install and audit the exact dependency lock; added gating Bandit delta checks.
- Aligned the hardened service template with character, World Info, and STT cache write paths.

### Fixed

- Removed the unconditional startup requirement for the generic `LLM_API_KEY`.

## [0.1.49] - 2026-09-15

### Added

- Added callback-token handling for long System Prompt choice keys.
- Added UTF-16-safe Telegram message splitting.
- Added typed World Info editing for integer and boolean fields.
- Added retention for processed Telegram update records.
- Split generation and utility/media worker pools.

## [0.1.48] - 2026-09-15

### Added

- Added fail-closed provider credential lookup when `api_key_env` is explicit.
- Added runtime permission enforcement for private bridge state.
- Added Hindsight HTTPS/loopback endpoint validation and hostname allowlisting.
- Added compatible systemd hardening with `UMask=0077`, private temp, filesystem protection, and restricted address families.
- Added behavioral tests, a locked dependency set, and CI test execution.

### Fixed

- Updated `pypdf` and locked dependencies after vulnerability scanning.


### Fixed

- Added durable SQLite jobs for normal generation and long-running commands.
- Added durable SQLite handoff and session-safe recovery for voice, image, and document jobs.
- Captured the active session ID before enqueue so queued work cannot move to a later session.
- Added strict per-chat FIFO dispatch while retaining parallel work across different chats.
- Requeued unfinished jobs after restart and made `/retry` redelivery-aware.


### Fixed

- Fixed `/systemprompt` failing with Telegram HTTP 400 after selecting a long JSON prompt.
- System Prompt menus now display the selected JSON entry name instead of embedding the full prompt text.


### Added

- Moved normal text and image generation into bounded background workers.
- Added durable failed-turn records and `/retry` for failed character responses.
- Added duplicate Telegram update protection with the `processed_updates` table.
- Finalized streamed responses through the full Telegram splitter after removing the preview.

## [0.1.44] - 2026-09-15

### Fixed

- Added durable Telegram update deduplication to prevent redelivered updates from generating duplicate replies.
- Removed a failed streaming placeholder before sending a fallback reply.
- Preserved existing response and session history behavior.

## [0.1.43] - 2026-09-15

### Fixed

- Made Help panel Close hide the message before attempting deletion.
- Kept the close action independent from callback acknowledgment failures.
- Restored category-button acknowledgment for the Help panel.

## [0.1.42] - 2026-09-14

### Fixed

- Fixed Help panel Close handling with a hide-message fallback when Telegram rejects `deleteMessage`.
- Updated README to document the guaranteed panel cleanup behavior.

## [0.1.41] - 2026-09-14

### Changed

- Made Help panel Close delete the Help message instead of only removing buttons.
- Removed `/systemprompt list` and `/systemprompt use` from Help documentation.
- Documented System Prompt as a JSON-choice panel with an Off button.

## [0.1.40] - 2026-09-14

### Changed

- Changed `/start` to send only the character card `first_mes`.
- Kept `/help` as the separate command for the help panel.
- Clarified that `balanced` and `concise` are JSON entries loaded from the configured prompt folder.

## [0.1.39] - 2026-09-14

### Changed

- Configured `SILLYTAVERN_SYSTEM_PROMPTS_DIR` to support a dedicated local `systemprompt/` folder.
- Kept JSON prompt files ignored from Git while loading them from the configured directory.

## [0.1.38] - 2026-09-14

### Changed

- Disabled arbitrary text System Prompt fallback.
- Limited the System Prompt panel to JSON choices from `SILLYTAVERN_SYSTEM_PROMPTS_DIR` plus `Off`.

## [0.1.37] - 2026-09-14

### Changed

- Added `SILLYTAVERN_SYSTEM_PROMPTS_DIR` for loading multiple JSON prompt files from a SillyTavern folder.
- Moved the live `natural.json` catalog into the configured SillyTavern system-prompts directory.
- Kept System Prompt panel and text commands compatible.

## [0.1.36] - 2026-09-14

### Changed

- Added an inline Telegram choice panel for JSON System Prompt catalogs.
- Kept `/systemprompt list` and `/systemprompt use <name>` for text automation.

## [0.1.35] - 2026-09-14

### Added

- Added private JSON System Prompt choices with `/systemprompt list` and `/systemprompt use <name>`.
- Documented `SILLYTAVERN_SYSTEM_PROMPTS_FILE` and the generic example catalog.
- Kept private prompt content outside the public repository.

## [0.1.34] - 2026-09-14

### Added

- Added a per-session System Prompt with `/systemprompt <text>` and `/systemprompt off`.
- Included the session System Prompt in prompt assembly with macro expansion.
- Added migration support for existing SQLite sessions.

## [0.1.33] - 2026-09-14

### Changed

- Split the message command handler into `commands.py` and `message_commands.py`.
- Kept every implementation module within the recommended 150–500 line range, except intentionally small launcher/runtime files.
- Preserved the modular runtime loader and live behavior.

## [0.1.32] - 2026-09-14

### Changed

- Clarified the new streaming, preset, macro/STscript, World Info editor, and card-version commands in README and Telegram autocomplete.
- Added the new feature commands to the Telegram command menu.

## [0.1.31] - 2026-09-14

### Added

- Added live SSE response updates controlled by `/stream on|off`.
- Added per-chat generation presets with `/preset save|use|delete`.
- Added World Info entry editor commands for list, add, set, and confirmed removal.
- Added safe `/macro` preview and `/stscript note|reset` commands.
- Added character-card backup version listing and confirmed restore.

## [0.1.30] - 2026-09-14

### Security

- Removed the main LLM key fallback for external embedding endpoints.
- Required HTTPS for external provider endpoints, with optional host allowlisting.
- Added DOCX decompression-ratio and uncompressed-size limits.
- Added PDF page-count and extracted-text limits.

## [0.1.29] - 2026-09-14

### Added

- Added reasoning level settings: `none`, `low`, `medium`, `high`, and `max`.
- Persisted reasoning levels per chat session and documented `/settings reasoning medium`.

## [0.1.28] - 2026-09-14

### Changed

- Documented the `streaming: true` provider setting required by SSE-based Chat Completions endpoints.
- Kept the public provider example generic while matching the live streaming configuration model.

## [0.1.27] - 2026-09-14

### Fixed

- Resolved legacy catalog model IDs such as `provider/model` through the configured provider catalog before generating text.
- Prevented valid existing sessions from falling back to the example endpoint and producing DNS failures.

## [0.1.26] - 2026-09-14

### Fixed

- Kept shared `Path` available in the modular runtime namespace so World Info and prompt assembly work during live message processing.

## [0.1.25] - 2026-09-14

### Added

- Added automated distribution ZIP boundary validation to GitHub Actions.
- The CI ZIP check confirms `CHANGELOG.md` is included and runtime/tooling files are excluded.

## [0.1.24] - 2026-09-14

### Changed

- Removed vendor-specific provider IDs, endpoint fallbacks, and runtime labels from the public bridge source.
- Made provider selection, streaming, and bridge data paths configuration-driven.
- Added `SILLYTAVERN_BRIDGE_HOME` for portable private runtime data.
- Kept the live deployment on its existing private data directory through systemd environment settings.

## [0.1.23] - 2026-09-14

### Fixed

- Corrected the GitHub Actions privacy scan to inspect tracked file names instead of matching `.gitignore` and workflow text.

## [0.1.22] - 2026-09-14

### Added

- Added GitHub Actions quality checks for compilation, YAML examples, and public privacy boundaries.
- Added `/providers health` endpoint probes without running inference.
- Added checksum-verified private backups for imported character cards.
- Added RAG `Sources:` footers when indexed documents contribute to a reply.
- Improved `/regen`, `/continue`, `/edit`, and branch variant outgoing-message tracking and cleanup.

## [0.1.21] - 2026-09-14

### Changed

- Clarified in README that release tooling is private maintainer tooling and excluded from public ZIPs.
- Updated `/providers refresh` help text to describe the standalone bridge catalog.
- Removed stale Hermes labels from user-facing provider catalog messages and request metadata.

## [0.1.20] - 2026-09-14

### Changed

- Moved the release publisher to the private maintainer path outside the public repository.
- Removed `scripts/publish_release.sh` and its README instructions from the public source.
- Removed the public release-tooling directory from the next distribution archive.

## [0.1.19] - 2026-09-14

### Changed

- Corrected the public product name typo to `SillyTavern Telegram Bridge`.
- Corrected the service example, release helper default title, README release command, and release branding.

## [0.1.18] - 2026-09-14

### Changed

- Explained why `scripts/publish_release.sh` is included in the public repository.
- Documented that the helper is maintainer-only tooling and is not required by the runtime service.
- Clarified that runtime deployments may omit the `scripts/` directory.

## [0.1.17] - 2026-09-14

### Changed

- Split the monolithic bridge implementation into domain modules under `bridge/`.
- Kept `sillytavern_telegram_bridge.py` as a nine-line compatibility launcher.
- Added README architecture documentation and kept every domain module below 500 lines.
- Preserved the existing command, provider, memory, RAG, media, group, and branch behavior.

## [0.1.16] - 2026-09-14

### Changed

- Updated README examples to use generic provider, model, character, and credential names.
- Documented the standalone provider catalog and optional Anthropic Messages adapter accurately.
- Updated `/help` and Telegram command-menu descriptions to identify the standalone bridge catalog.
- Removed the old provider-runtime label from the user-facing command menu.

## [0.1.15] - 2026-09-14

### Changed

- Added a commented optional Anthropic Messages provider block to `config/providers.example.yaml`.
- Kept the basic example limited to two active generic providers.
- Documented `ANTHROPIC_API_KEY`, `api_endpoint`, `transport`, and `anthropic_version` in the example.

## [0.1.14] - 2026-09-14

### Added

- Synced the Anthropic Messages adapter into the public source.
- Added generic Anthropic content-block and SSE handling to the distributable bridge.

### Verification

- Real Anthropic Messages inference returned `ANTHROPIC_OK`.
- Public bridge compiled successfully.

## [0.1.12] - 2026-09-14

### Changed

- Restored the missing `v0.1.11` entry in the cumulative changelog.
- Corrected release ordering and attribution.
- Kept release notes scoped to their own version changes.

## [0.1.11] - 2026-09-14

### Changed

- Removed local live-environment and credential-copy details from the public cumulative changelog.
- Kept release bodies scoped to their own release.
- Refreshed public documentation without exposing local runtime details.

## [0.1.10] - 2026-09-14

### Changed

- Replaced vendor-specific names in the public provider example with `provider-one` and `provider-two`.
- Added generic `adapter: chat_completions` support for arbitrary OpenAI-compatible provider IDs.
- Updated public environment examples to use generic provider credential names.
- Kept vendor-specific provider catalogs confined to local runtime configuration.

## [0.1.8] - 2026-09-14

### Changed

- Removed the remaining live credential-name reference from public source.
- Added the generic `SILLYTAVERN_RAG_EMBEDDING_API_KEY` setting.
- Refreshed the public ZIP after the privacy cleanup.

## [0.1.7] - 2026-09-14

### Changed

- Removed local live-environment and credential-copy details from the public cumulative changelog.
- Kept release bodies scoped to their own version changes.
- Refreshed the public ZIP with cleaned documentation.

## [0.1.6] - 2026-09-14

### Added

- Anthropic Messages adapter with `/messages` request translation.
- `x-api-key` and `anthropic-version` headers.
- System prompt, alternating message, image-block, and SSE text parsing.
- Optional generic Anthropic provider configuration.

## [0.1.5] - 2026-09-14

### Changed

- Added `SILLYTAVERN_ENV_FILE` for a dedicated bridge environment path.
- Updated the systemd example for an external environment file.
- Documented keeping credentials and runtime state outside the public repository.

## [0.1.4] - 2026-09-14

### Changed

- Renamed provider catalog endpoint field from `api` to `api_endpoint`.
- Added a compatibility fallback for older catalogs using `api`.
- Updated the standalone provider catalog and public example.

## [0.1.3] - 2026-09-14

### Changed

- Removed unsupported Anthropic Messages transport from the basic provider example.
- Added a runtime guard for supported Chat Completions transports.
- Simplified the public provider example to two Chat Completions providers.
- Updated README documentation and provider catalog setup.

## [0.1.2] - 2026-09-14

### Added

- Automatic model discovery through OpenAI-compatible `/models` endpoints.
- TTL-based model catalog cache.
- `/providers refresh` force-refresh command.
- Static YAML fallback when discovery fails.
- `SILLYTAVERN_MODEL_REFRESH_SECONDS` and `SILLYTAVERN_MODEL_CACHE` settings.

## [0.1.1] - 2026-09-14

### Changed

- Provider catalog became independent from the host application's provider configuration.
- Added `SILLYTAVERN_PROVIDER_CONFIG` and `/providers`.
- Updated the example catalog to generic credential names.
- Documented SillyTavern prerequisites and portable setup.

## [0.1.0] - 2026-09-14

### Added

- Telegram character chat backed by SillyTavern character-card metadata.
- Per-chat and per-session SQLite history.
- Character, persona, World Info, and Author's Note selection.
- Two-level provider/model catalog navigation.
- Session-scoped generation settings.
- Response regeneration, swipe variants, branch selection, and continuation.
- Native Telegram edited-message handling with branch replacement.
- Image input, text-to-speech, and voice transcription.
- Hindsight memory, session summaries, and bounded context compression.
- Data Bank RAG with FTS5 and optional embeddings.
- Multi-character group chat with bounded autonomous mode.
- Character-card upload and metadata validation.
- Inline help menu with command categories.
- Bounded background workers for media, documents, and memory retention.

### Security and portability

- Runtime credentials remain in `.env` and are excluded from Git.
- Live state, logs, cards, and user personas are excluded from the public repository.
- Paths and Telegram allowlists are configurable through environment variables.
- Unsupported provider entries are shown as catalog-only rather than exposed as inference options.

### Known limitations

- The bridge reimplements selected SillyTavern concepts and does not execute every native extension or STscript hook.
- Autonomous group mode produces a bounded labeled exchange in one generation request.
- Historical assistant bubbles created before message-ID tracking cannot be deleted during branch replacement.
- Embedding search requires an OpenAI-compatible embedding endpoint; lexical FTS5 remains the fallback.
