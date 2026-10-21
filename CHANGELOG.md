# Changelog

All notable changes to **SillyTavern Telegram Bridge** are documented here.

Versions 0.3.007–0.3.019 below are backfilled from their published GitHub release notes, retrieved on 2026-10-09. Dates use the UTC publication date. Historical verification statements describe those releases, not new acceptance or benchmark results.

The complete previous changelog is preserved byte-for-byte in [CHANGELOG-legacy.md](CHANGELOG-legacy.md), including 0.3.006 and all older entries. Its former Unreleased section is a historical snapshot, not a list of pending work. No original changelog content or signed tag was rewritten.

## [Unreleased]

### Greeting media resilience and choice diagnostics

- Keep a successfully delivered opening and its started session valid when the optional greeting photo transport fails. Persist the photo attempt before network I/O so interruption recovery cannot automatically duplicate an uncertain upload; required text and database failures remain recoverable errors. A process exit after the attempt checkpoint may omit the optional photo.
- Distinguish empty, overlength and command-prefixed Light Novel choices in content-free diagnostics and bounded repair guidance. Encourage shorter choices while retaining the 160-character hard limit, exact 3–4 count and two-call maximum.
- Update Python 3.11/3.14 runtime/development locks and migration constraints to certifi 2026.10.19 and uv 0.13.4, and update workflow uv bootstrap pins, following the compromised-CA trust advisories. Other resolved package versions remain unchanged.

## [0.3.031] - 2026-10-18

### Field-specific narrative validation diagnostics

- Add fixed `validation_field` and `validation_code` metadata to narrative parser retry and final-rejection events, including nested scene, thread, arc, evidence and resolution fields. Record only the first failing check for each attempt, never submitted values or private entity identifiers.
- Enforce explicit field/rule allowlists at the diagnostic boundary. Keep arbitrary exception text and provider failures out of parser-field diagnostics; malformed JSON records only the output-level category.
- Preserve the existing parser acceptance rules, broad rejection categories, canonical-input repair, retry limits, source/publication guards, helper routes and budgets. No dependency, database schema, prompt or service-unit change.

## [0.3.030] - 2026-10-18

### Director Room reassessment recovery

- Clarify narrative reconciliation's changed-arc contract: omit unchanged accepted arcs, leave nonterminal outcome evidence empty, and copy required outcome quotations directly from the cited committed row. Continued scenes preserve the established thread, viewpoint and POV mode; observed changes require a new scene identity and explicit transition.
- Preserve fixed reconciliation rejection codes through the Director prerequisite and report actionable failures instead of a generic unavailable result. Source changes cannot carry forward an obsolete rejection cause; provider output and story text remain excluded from diagnostics and user-facing errors.
- Treat a completed Mini App reassessment with `accepted: false` as a failure. Keep unsaved Director form values, display the returned failure message, and retain the retry path instead of showing success and replacing the editor.
- Retain strict evidence validation, atomic narrative publication, existing model inheritance, bounded format repair, source guards and Director leases. No dependency, database schema or service-unit change.

## [0.3.029] - 2026-10-18

### Helper extraction recovery

- Distinguish the exact NPC audience contract violation with fixed, content-free subcodes. Feed the applicable guidance into the existing canonical-input repair and retain diagnostics for the repaired result, including provider failure. Keep the original audience validation, single repair allowance, NPC coverage guards, and independent publication of valid simulation updates.
- Classify malformed curator JSON separately from invalid output shape. Allow one format repair from the same canonical source and accepted accumulator, with the same helper model and output budget. Suppress repair when the source changes or memory is disabled.
- Reject populated curator results containing no valid memory entries. Repeated malformed or invalid output preserves accepted curated memory and pending coverage instead of publishing an empty accumulator as completed work.
- Keep the curator's private-panel contract separate from classified character memory. Existing audience checks remain the default for Summary, Scene, and episodic repair; the private curator grants no character knowledge.

### Compatibility and verification boundaries

- Production remains on Python 3.11 with unchanged dependency locks, database schema, autonomous retry limits, helper budgets, and default routing. Provider output caps and HTTP rate limits remain explicit failures; these changes do not guarantee model quality or clear parked jobs automatically.
- Includes the shared fixture and regression-suite maintenance from #539 and #540. Release and deployment verification apply to the final signed source revision; focused tests do not establish Telegram acceptance or complete memory recall.

## [0.3.028] - 2026-10-16

### Reset cleanup and logging health

- Persist operation-scoped Telegram cleanup IDs atomically with `/reset`, checkpoint each deletion, and resume interrupted cleanup from the original snapshot. Preserve new-story messages and the existing best-effort policy for attempted API failures (#538).
- Expose degraded log capture and bounded trusted caller locations. Improve Mini App diagnostics while retaining known usage and identifying capture gaps without logging raw story content (#537).
- Production remained on Python 3.11 with the existing locked dependencies and no new database migration. This entry summarizes the previously published [v0.3.028 release notes](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/releases/tag/v0.3.028); it does not claim new live acceptance.

## [0.3.027] - 2026-10-15

### Installer systemd hotfix

- Generate an unquoted absolute `WorkingDirectory=` path in the managed user service. systemd rejects a quoted absolute path as non-absolute, so a manual `install.sh --no-start` from v0.3.026 could leave the regenerated unit unloadable. Keep existing path escaping and argument/environment quoting, including custom source paths with spaces and literal percent signs.
- Preserve the existing signed-source deployment, Python 3.11 runtime locks and trust configuration, database schema, private settings and saved stories. No provider, model, or narrative changes.


## [0.3.026] - 2026-10-15

### Choice recovery and diagnostics

- Make the choice JSON example contain exactly the requested 3 or 4 distinct entries instead of two duplicate examples.
- Share the existing two-attempt maximum across transient provider errors, empty output, malformed choice JSON, invalid choice contracts and explicitly capped output. Re-extract from the same canonical context with fixed feedback; never replay rejected model text or regenerate the saved story.
- Preserve content-free failure reasons, stage, attempt, count, response shape and opaque correlation references in structured runtime logs. Keep the privacy formatter's omission of arbitrary legacy arguments intact.
- Enable the existing nonstreaming Chat Completions `json_once` truncation guard for choice-only calls. Exact counts, 1200-token allocation, timeout, lease, source/epoch guards and final validation remain enforced.

### Memory and tracker reliability

- Size Summary first-request allocations from retained JSON plus the complete bounded source, within 1200–4096 tokens. Scene allocations account for both state and classified blocks, within 1000–8192 tokens. These are bounded estimates, not completeness proofs; explicitly capped output still fails validation and each helper retains its single repair attempt.
- Park autonomous memory work after the existing eight-failure limit for `output_capped` too. Explicit manual retries and Hindsight's separate retention behavior remain unchanged.
- State the existing 5000-character individual Summary block limit in initial and repair guidance, alongside the unchanged total-text and audience limits.
- Reject partial supporting-NPC extractions containing invalid operations or contradictory explicit audiences rather than silently discarding changes and advancing complete coverage.
- Reject populated unsupported simulation fields at the external extraction boundary. Emit fixed, content-free reasons and targeted repair guidance for invalid types, enums, identities, bounds, relationship changes and conflicting actor operations. Empty updates, canonical-input repair and source/publication guards remain intact.

### Maintenance and verification boundaries

- Include the configurable curator output budget from #530 and consolidated dependency updates from #534. The runtime lock changes aiohttp from 3.14.3 to 3.14.4; use the manual signed-update procedure rather than bypassing the updater's dependency guard.
- Pin NumPy 2.3.5 for the deployed pre-x86-64-v2 CPU: NumPy 2.4.6 fails to import on the target host. Regenerate both hash-locked Python dependency manifests and validate the pinned wheel on that CPU before deployment.
- Production remains on Python 3.11. No new database migration, history-pruning activation, provider-route change or service-unit change is introduced by the audit fixes.
- Regression tests establish bounded recovery and fail-closed contracts, not the cause of the historical choice failure, guaranteed model quality, elimination of every truncation, or acceptance of the live memory backlog. The structured cap guard remains scoped to the supported nonstreaming Chat Completions path; this release does not claim new streaming, Responses or Anthropic cap handling.

## [0.3.025] - 2026-10-12

### Story defaults and preset compatibility

- Default new stories to **Closed Story** across all narrative presets. Existing saved personal defaults and session choices take precedence; the foundation migration keeps pre-Narrative stories **Open-ended**. **Ask before finale** remains off by default (#521).
- Remove the three bundled legacy TXT examples, `balanced.txt`, `concise.txt` and `natural.txt`, from the distributed System Prompt examples. Retain the four writer JSON examples; System Prompt still defaults **Off**. Installed copies and user-owned presets are not deleted (#523).
- Add the contributor development handbook covering isolated setup, architecture, implementation, focused testing, review and release handoff (#520).

### Optimizer and Mini App recovery

- Preserve raw JSON segment boundaries during optimizer continuation, including split escapes and string whitespace, while retaining request, continuation and usage accounting (#517).
- Clarify optimizer validation and provider-unavailability failures. Keep progress evidence and suggestions when completion is uncertain, retain the shared Operations recovery path, and prevent stale views from applying late results (#518).
- Keep the NPC search button label on one line (#519).

### Structured extraction, ending admission and Director recovery

- Reject explicitly capped structured tracker responses on the **nonstreaming Chat Completions `json_once`** path, even when partial JSON parses. Applicable helper paths use their existing bounded canonical-input repair; direct curator calls reject capped output before publication. Preserve audience/source guards and independently valid uncapped simulation when NPC repair fails. Capped partial output is never accepted. Clarify the simulation helper's change-only output contract and extraction-specific context (#522).
- Allow readiness assessment during a reconciled resolution phase while the ending lifecycle remains open, and check finale admission after an accepted Telegram Director reassessment. Admission still requires the ordinary finale guards. Allow at most one additional Utility call for malformed, invalid-shape or explicitly capped narrative output, using bounded format re-extraction from the same committed transcript and accepted state, with reconciliation diagnostics and stale-source suppression (#524).
- Distinguish narrative publication guards and Director policy rejections using precise content-free diagnostic codes, without raw proposals, story text, extracted entity identifiers or exception messages. Revision/source metadata remain logged (#525, #526).
- Give Director POV-policy conflicts targeted feedback within the existing single repair attempt; preserve full validation and stale-state rejection (#527).
- Distinguish future rendering policy from observed scene POV; guide continued scenes to omit conflicting optional POV and repair scene-POV conflicts within the same shared retry. Preserve the selected rendering style and the established scene observation instead of inventing a transition (#528).
- Retain user-authorship, reference, audience, revision, source and ending-lifecycle guards. These changes do not rewrite saved settings or history or grant additional Director retries.

### Verification limits and compatibility

- Live verification observed a Director continue decision with empty optional POV after one shared repair, preserving the configured rendering policy and accepted scene state. The initial rejection code was not retained, so the specific repaired guard is unproven.
- Full finale-to-epilogue-to-persisted-closure acceptance has not been demonstrated. Story output caps and separate scene/curator capped-extraction failures remain unresolved by this release.
- The explicit-cap guard above does not establish streaming, Responses or Anthropic cap fixes. Release publication requires the exact metadata revision's protected hosted CI and CodeQL gates before signing.
- No new database migration, schema-version change, dependency-lock change or service-unit change occurs in the v0.3.024-to-source range. New-story narrative defaults change; the historical migration implementation in `narrative_schema.py` is pinned for **Open-ended** compatibility. Production remains on Python 3.11. Install only from the independently trusted signed tag and verified versioned ZIP/checksum assets after publication.

## [0.3.024] - 2026-10-11

### Verified code-quality audit fixes

- Use the canonical `cepeter-job` repository for release discovery and signed self-update fetches; align current installation, release and security links (#515).
- Preserve the existing persistent deployment identity so managed installations remain recognized after the repository move (#515).
- Handle bare and qualified rebuild calls safely in the leak scanner, with regression coverage for the previously crashing forms (#515).
- Keep optional NumPy import typing valid when NumPy is absent, preserving the existing pure-Python vector-math fallback (#515).
- Document logging limits, request observation and context-selection controls; keep context-selection defaults off (#515).
- Includes the settings validation and content-free memory diagnostics recorded under 0.3.023. Production remains on Python 3.11; dependency locks, database schema, private provider configuration and history-pruning policy are unchanged.

## [0.3.023] - 2026-10-11

### Settings validation and content-free memory diagnostics

- Share on/off setting validation for grounded-user and Humanizer preferences (#511).
- Record content-free extraction contract rejection codes and root-shape diagnostics; provide safe compact schema guidance for the existing bounded repair (#513).
- Record allowlisted provider finish reasons and explicit output-cap metadata, without inferring truncation from token counts (#513).
- Correlate Hindsight retention using per-attempt and per-document opaque references; report allowlisted server operation references only when returned (#513).
- Preserve repair/call limits and output allocations, the 90-second retain timeout, canonical source/audience/coverage guards, and ambiguous-timeout/late-write fences.
- Python remains 3.11.16 and history pruning remains off. No migration, dependency, database-schema, routing or configuration change; no tracker recovery, historical completion matching, idempotency, cancellation or measured token-savings claim.

## [0.3.022] - 2026-10-10

### Memory extraction reliability

- Reject supporting-NPC results with no accepted operations instead of advancing coverage. Use the existing single repair attempt; repeated invalid output remains pending (#510).
- Require a text exclusion reason for an empty episodic result. Reject populated results with no accepted candidates, and re-check durable facts during the single bounded repair. Failed repair preserves canonical source coverage (#510).
- Classify Hindsight retention failures with content-free upstream-response, HTTP, timeout or generic diagnostics. Existing retry/backoff and provider routes remain unchanged (#510).
- Align synthetic episodic providers with the explicit-empty contract and preserve the frozen post-release findings. Upstream Hindsight recovery and the issue #421 efficiency/quality goals remain unverified.
- No dependency-lock or database-schema change. Production history pruning remains off; install from the trusted signed release and verify the running revision after restart.

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
