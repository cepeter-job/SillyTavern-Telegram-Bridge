# Changelog

All notable changes to **SillyTavern Telegram Bridge** are documented here.

Versions 0.3.007–0.3.019 below are backfilled from their published GitHub release notes, retrieved on 2026-10-09. Dates use the UTC publication date. Historical verification statements describe those releases, not new acceptance or benchmark results.

The complete previous changelog is preserved byte-for-byte in [CHANGELOG-legacy.md](CHANGELOG-legacy.md), including 0.3.006 and all older entries. Its former Unreleased section is a historical snapshot, not a list of pending work. No original changelog content or signed tag was rewritten.

## [Unreleased]

## [0.3.021] - 2026-10-10

### Conversation setup and helper reliability

- Close expired conversation-setup panels when they are used. Preserve the current panel and draft when an older callback arrives for the same message, and fail safely when callback-token storage is unavailable (#507).
- Align episodic, scene and NPC helper prompts with their validated JSON-object contracts; keep NPC and curator generation on the single JSON-response path (#508).
- Repair missing NPC roots with the existing bounded repair, preserving independently valid tracker state if the NPC repair fails. Do not mark a failed NPC extraction complete (#508).
- Let synchronous Hindsight retention wait up to 90 seconds while recall and metadata retain their 30-second default. Gradually back off repeated retention failures and ambiguous-delete watches to one-hour intervals without clearing late-write evidence (#508).
- Include the previously reviewed HTTP-error response-stream cleanup (#495), Anime art-direction refinement (#489), and test/CI compatibility fixes. Experimental Python 3.15 checks remain manual-only (#506).

### Evidence and deployment boundaries

- Preserve the frozen helper study and independent writing-profile review evidence (#482, #504, #508). The helper study did not meet the 20% story-input goal, and its measurements do not cover the later NPC repair correction.
- No runtime dependency or database schema change since v0.3.020. Production remains on Python 3.11; Python 3.14 migration is not part of this release.
- Story assembly, production history selection/pruning, private provider routes and configuration remain unchanged. No new provider comparison, Telegram acceptance test, or production input-savings claim is implied by this release.
- Install only from the independently trusted SSH-signed tag after exact-source CI/CodeQL checks and SHA-256 asset verification. Back up private state and verify the running revision after the supervised restart.

## [0.3.020] - 2026-10-09

### Context research and opt-in diagnostics

- Preserve native source/reader verification and the inactive reversible history codec research (#467). The original frozen experiment and failed automated quality results remain unchanged; the codec cannot be dispatched as a production prompt.
- Add exact source-backed statement-level shadow selection (#475) and retain frozen matched NanoGPT/post-release observations and blinded-review packets (#476).
- Add metadata-only prefix stability and instruction-duplication profiling (#478), without deleting any character instructions, dialogue, or source evidence.
- Add the final physical-request observability layer (#480) with request size, prompt-role sizing, provider-reported input/cache/output usage, and retry/continuation accounting across adapters. Opt-in is explicitly required; nothing is recorded automatically by this release until enabled.
- Lower the **new-trial** efficiency goal under issue #421 from 30% to **20%** (#481), retaining separately versioned evaluations, complete accepted-work accounting, required source and reader/branch causal proof, independent blinded human narrative review and staged production approval.

### Compatibility and security

- No changes to locked runtime dependencies or the persistent database schema since v0.3.019. No automatic story migration, lossless source-authority waiver, or history-pruning activation.
- Production context selection/pruning remain disabled; request observation defaults to off.
- Install only as an independently trusted SSH-signed release tag after verifying the official SHA-256 assets. This changelog entry alone is not a signature or deployment confirmation.

### Quality-gated context objective (issue #421, 2026-10-09)

- Lower the **current new-trial** provider-reported story input-reduction objective from 30% to **20%**, retaining complete accepted-work accounting, source/reader/causal proof, independent blinded narrative quality and production approval gates.
- Introduce a separately versioned v2 synthetic replay fixture so historical v1 trial plans, signed releases and original 30% evidence remain untouched. Evaluation preflight follows a newly predeclared target (never below 20%); the frozen native-v1 trial runner and its original 30% protocol hash stay byte-for-byte unchanged.
- Keep context pruning and final-request observation **off** until signed deployment and explicit opt-in. A merged commit or this changelog is not a published release.

## [0.3.019] - 2026-10-09

**Release commit:** `4e3b31af8c6f8c9f81d97cf09051a2ba31477b03`

### Native writing and preset safety

- Consolidate #469–#471 in #472: reject modular SillyTavern Chat Completion exports in the native TXT/simple-JSON System Prompt catalogue, rather than treating auxiliary export strings as selectable prompts. Handle malformed and invalid-UTF-8 files without hiding valid entries.
- Place character-card post-history guidance after conversation history and before the current user message. Keep native agency, state, Telegram format, language and Light Novel response-envelope contracts authoritative.
- Add optional Scene Continuity, Grounded Dialogue, Ensemble Focus and Magical Realism profiles through the existing selector. No automatic profile selection or active-session migration.
- Add the source-verified hybrid-history **shadow** selector and offline evidence tooling (#473). This is not approval to prune live history or a claim of measured production savings.

### Verification and compatibility

- The exact release commit passed protected CI and code-quality gates; the annotated tag used the existing trusted SSH release key.
- Full modular preset interpretation remains unsupported. Writing-quality comparisons and the 30% input-reduction objective still require matched provider measurements and independent blinded review.
- No private configuration or story data is included in the release archive.

## [0.3.018] - 2026-10-09

**Release commit:** `17cd7a89fb9f72dd42cb467ab22cef6620d66602`

- Supply source-authenticated preceding dialogue to classified Summary extraction for short ambiguous user replies and one-word actions (#466).
- Include at most two exact preceding canonical turns, including the immediate assistant turn, with 3,600 total context characters and 3,100 per turn. Omit oversized turns rather than truncating them.
- Recheck source fingerprints before repair and after inference. Only the current canonical message establishes newly accepted facts; preceding dialogue is reference-only, not a new coverage or private-knowledge grant.
- Preserve audience validation, block/text limits, source/lease/revision fences, subscription-only routing and the single repair limit. History pruning remains off.

## [0.3.017] - 2026-10-09

**Release commit:** `50c69b912bf3e3aeaca691a3b54d7d63645a1c38`

- Apply the 32-classified-block limit to every Summary response, including the existing JSON-format repair (#462).
- Allocate output tokens from the actual fresh-window prompt after rollover, rather than reserving output for the old archived summary.
- Preserve source-proven archive handoff, strict reader/privacy validation, 12,000 combined characters, 5,000 per block and source/lease/checkpoint guards.
- No runtime dependency or schema change from the preceding release. The historical one-part format probe was not proof of complete story continuity or 30% savings.

## [0.3.016] - 2026-10-09

**Release commit:** `7e4e25884b8760f99401a2113fe32f3d81e47278`

- Start a new bounded Summary window at 30 of the permitted 32 classified blocks using the existing atomic, source-proven archive handoff (#459).
- Retain the separate 80%-of-12,000-character rollover rule and preserve private-reader, source-revision and lease protections.
- Regression cases distinguish the 30-block rollover from a 29-block window. Runtime dependencies and SQLite schema are unchanged from 0.3.015.

## [0.3.015] - 2026-10-09

**Release commit:** `5cfb6aed92fbb4a79751b940b249270d781ca721`

- Provide mutually exclusive shared and restricted JSON examples in initial Summary extraction and its existing single repair (#458).
- Shared blocks require an empty known-by list; restricted blocks require real, source-supported knowers. Ambiguous private facts must not become shared.
- Keep the strict parser and audience validator unchanged. Conflicting outputs still fail closed without a second repair, coverage advancement or manufactured archives.
- Historical read-only subscription probes validated small-sample JSON shape, not general narrative fidelity. No runtime dependency or schema change from 0.3.014.

## [0.3.014] - 2026-10-09

**Release commit:** `5f3756a013fc2b2bb5d2e64762be0196953115f5`

### Memory reliability

- Migration 32 adds durable classified Summary archive windows with accepted text, reader boundaries, source checkpoints and digests (#457).
- Archive an accepted Summary transactionally at 80% capacity, then extract a fresh bounded window from the next canonical source part. Split source parts cannot advance row coverage until complete.
- Bound and revalidate authorized query-relevant archive recall. Source rewrites, reset/purge, manual replacement and deletion invalidate affected archive authority; the active Summary cap remains 12,000 characters.

### CI and rollout

- Add disjoint Python test shards and parallel Chromium/WebKit smoke gates (#455), retaining security, static, dependency, coverage and code-quality requirements.
- Runtime requirements.lock is unchanged. Preserve a matching pre-upgrade SQLite snapshot for rollback across migration 32.
- Synthetic source rehearsals and a read-only subscription probe are not evidence of full semantic continuity or 30% production input savings. History pruning remains disabled.

## [0.3.013] - 2026-10-08

**Release commit:** `b236bc95848efffbf87dbc3e47143b2d983f3a8d`

- Require Summary responses to stay within 32 blocks, 5,000 characters per block and 12,000 combined text characters, including newlines (#456).
- Give near-full accepted summaries explicit repetition-reduction guidance and a 10,800-character target. Apply the same limits to the existing canonical-input repair without new retries or fallbacks.
- Preserve knowledge, visibility, identities, negations, promises, causal sequence and branch facts; do not silently trim generated output.
- Align user-guide descriptions of /check, streaming and persona behavior with the implementation (#454).
- Runtime requirements.lock is unchanged; no database migration. A historical parseable one-part probe did not establish durable catch-up or the input-savings target.

## [0.3.012] - 2026-10-08

**Release commit:** `0adc2e43d59907575d76b19134405673be240211`

- Add correlated private diagnostics and bounded telemetry (#433), report incomplete Telegram cleanup after a successful local /reset (#444), and fence stale session-deletion confirmations (#446).
- Consolidate #443 and #445–#451 in #452: require canonical source/checkpoint proof before Summary ACK, preserve fork/Dependabot secret scanning, bind Mini App reads to the displayed story, repair managed-mirror drift, bound provider responses and continuation accounting, reject stale swipes, and improve portrait cleanup/dialog accessibility.
- Bootstrap checked maintainer release trust and GitHub's pinned SSH host identity without generating private keys or accepting conflicting/revoked/symlink entries (#453).
- Runtime requirements.lock is unchanged from 0.3.011; no new schema change. Installation alone does not clear an invalidated Summary backlog or authorize context pruning.

## [0.3.011] - 2026-10-08

**Release commit:** `7c3e5522dec593539a5d0f08baa93081ef31d267`

- Classify sanitized HTTP 429 responses as rate_limit instead of generic work_failed in durable Summary recovery (#442).
- Retain source/lease/revision fences and the autonomous failure ceiling; do not acknowledge failed work, increase retries or add fallback calls.
- Include the patched Mini App development lockfile (#440). Runtime requirements.lock is unchanged from 0.3.010; no database migration or pruning activation.

## [0.3.010] - 2026-10-08

**Release commit:** `18a5c6de5a55b64a163204c7164225ebf29000bc`

- Recover safely mergeable 33–64-block classified Summary responses by coalescing only adjacent blocks with identical normalized visibility and knowers (#437).
- Validate every original block, preserve exact text/order and audience boundaries, then apply the unchanged 32-block and size limits. Mixed, oversized or unmergeable output fails closed; no extra model call is required for safe coalescing.
- Report inactive Director guidance and honor /clear (#434), restore mandatory full-history secret scanning (#436/#439), and cache pinned browser downloads without dropping smoke coverage (#441).
- Runtime requirements.lock is unchanged from 0.3.009. Source fidelity or format recovery does not establish causal-semantic completeness or measured savings.

## [0.3.009] - 2026-10-08

**Release commit:** `a3b79cf7a8057247d34c2fd5555d40d6cf19df72`

- Avoid story-style automatic continuation for Summary/Scene/Episodes JSON; use one canonical-input format repair rather than replaying malformed generated text (#432).
- Request strict JSON-object syntax only on the specifically tested NanoGPT GLM 5.2 HTTPS route. Allocate Summary output adaptively between 1,200 and 4,096 tokens without weakening classified validation.
- Add synthetic continuity, reader/branch/source and no-network budget preflight checks (#431). These are not native semantic proof or narrative-quality approval.
- Runtime requirements.lock is unchanged from 0.3.008. Existing invalidation may remain after upgrade; only accepted, fenced worker jobs may advance coverage.

## [0.3.008] - 2026-10-08

**Release commit:** `3ad5fdf2864733e54ed800cf56ea47163489431d`

- Add bounded JSON/shape repair and safe failure classifications for durable memory extraction (#428); prevent uncovered-source ACK and premature invalidation clearing, with read-only backlog diagnostics (#430).
- Add guarded context evidence selection and attribution with SILLYTAVERN_CONTEXT_SELECTION_MODE defaulting to off (#424), plus source-bound shadow previews and synthetic evaluation (#427/#429). No live prompt pruning.
- Guard character/tracker identity and revisions (#423), repair /imagine fallback prompt routing (#422), and add Manhwa and revised Anime guidance (#425/#426).
- Runtime requirements.lock is unchanged. Keep prior mirror and database backups; installation does not automatically clear the production Summary backlog or prove 30% savings.

## [0.3.007] - 2026-10-07

**Release commit:** `410aa5d9cb44aa02bd80e364cef7485b7a282b25`

### Story experience and canonical state

- Add canonical Story Trackers, native task progression, natural action adjudication using Utility/Director and bridge-owned d20 receipts, and the /check panel. No automatic rolls for greetings, narrator steering or groups.
- Add /usage and visibility-filtered /trackers, persisted Realism/Anime image styles, and the Story Deck Mini App redesign.
- Bound estimated input independently of model context via a default 49,152-token input cap while preserving output reservation and mandatory context.
- Track supporting-NPC relationships/agendas, inventory, skills, conditions, factions, quests and foreshadowing through reversible source-scoped native state; no parallel private model ledger.
- Retire the abandoned Internal States protocol. Fence extraction/check delivery against stale callbacks, rewrites, resets and branches; preserve historical projection ownership and authorized private-reader boundaries.
- Preserve independently valid tracker updates when NPC extraction fails, with a tracker-only repair and complete-row/source guards.

### Migration and verification

- Migration 25 adds tracker records, source receipts, revisions/history and check results without replaying already processed NPC sources.
- Migration 26 retires pending temporary bootstrap work while preserving completed native state, resuming incomplete rows safely and rolling back rewritten suffixes. Saved prompts and transcripts are not rewritten.
- Consolidate test owners and retain the strict aggregate CI gate over Python, Mini App and security/static checks. Rollback across schema changes requires the matching pre-upgrade database snapshot.

## Historical sources and retention

The backfilled entries derive from the published release collection retrieved on 2026-10-09: `https://api.github.com/repos/cepeter-job/SillyTavern-Telegram-Bridge/releases?per_page=100`. Each entry retains the historical release commit independently of a retained tag or release URL. The full pre-backfill changelog is [preserved here](CHANGELOG-legacy.md).

Latest-only release housekeeping is a separate operation: authenticate the newest signed tag and published assets, verify the preserved history bundle, ref inventory, release metadata and downloaded assets, then retire only older releases/tags. This documentation change does not delete anything or re-sign a historical tag. See [Operations](docs/operations.md#latest-only-release-housekeeping).
