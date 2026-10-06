# CI, Tracker Views and Image Styles Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for scoped
> implementation and superpowers:verification-before-completion before reporting
> success. Root owns integration, commits, PR creation and merge.

**Goal:** Complete the four requested improvements and merge a verified PR.

**Architecture:** Reuse existing canonical SQLite and session metadata. Add one
shared tracker read projection, style-aware image prompting, and independent CI
jobs feeding the existing protected test context.

**Tech stack:** Python 3.11, SQLite, Telegram Bot API, native JavaScript Mini App,
pytest/xdist/coverage, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-06-ci-trackers-image-styles-design.md`

## Global constraints

- Work on `feat/ci-trackers-image-styles` in the dedicated puntoap worktree.
- Preserve unrelated branches, files and worktrees.
- Preserve unique regression coverage and existing security/coverage floors.
- Do not add another canonical tracker store or change `/status`.
- Keep image-only output and existing callback/session concurrency protection.
- Existing production dependencies and database schema remain sufficient.
- Review and merge are authorized; release publication and live rollout are outside scope.

## Review focus

- A missing or skipped CI job must not make the protected test context green.
- New tracker readers must respect both historical source cuts and current privacy.
- A fresh Mini App tracker GET must not create a session or consume tokens.
- Very small image prompt limits must include style and reference overhead.
- Session reset/branch restore must carry the intended style without cross-session leakage.

## Task 1: Audit tests and restructure CI

**Files:** `.github/workflows/ci.yml`, `.github/workflows/README.md`,
`tools/ci_gate.py`, `tests/test_ci_gate.py`, existing test support and boundary
tests, `tests/test_provider_health_persistence.py`.

- [x] Record baseline counts, exact helper duplication, current timing and ZeroClaw source.
- [x] Exercise the gate with literal success/failure/missing/skipped/cancelled outcomes.
- [x] Make `test` the strict aggregate of five independent jobs.
- [x] Consolidate only identical helpers and shorten giant parameter IDs.
- [x] Publish useful JUnit, coverage, smoke, reference and leak-scan evidence.
- [x] Verify affected tests and independent CI gate behavior.

## Task 2: Expose saved canonical trackers

**Files:** `bridge/simulation_view.py`, optional output helper,
`bridge/simulation_commands.py`, `bridge/simulation_repository.py`,
`bridge/npc_service.py` only for reusable visibility,
`bridge/closed_session_guard.py`, Mini App tracker API/assets/router/navigation,
tracker tests and the existing Mini App smoke fixture.

**Interface:** A shared bounded tracker view supplies a whitelisted JSON projection;
Telegram renders that projection without using an LLM.

- [x] Test visible fields, hidden-field exclusion and source/session boundaries first.
- [x] Add saved progress/freshness reads and the bounded presentation.
- [x] Register `/trackers` and allow it for inspection of closed stories.
- [x] Add an authenticated read-only API and Manage page with empty/stale states.
- [x] Exercise Telegram output bounds and Mini App rendering with real saved data.

## Task 3: Add image style selection

**Files:** `bridge/image_styles.py`, `image_panels.py`,
`image_generation.py`, `image_routing.py`, image callback sections in
`feature_callbacks.py`, image config checkpoint registries and focused tests.

**Interface:** Validated session preference `image_style:<chat>:<session>`
accepts `realism` or `anime`; absent/invalid saved values fall back to Realism.

- [x] Test panel state, persistence, invalid input and reset.
- [x] Test provider payloads for scene/custom, both styles and text/reference inputs.
- [x] Reserve style overhead before advertising/validating custom-input limits.
- [x] Include style in alternate-ending configuration copies.
- [x] Run affected image and lifecycle regressions.

## Task 4: Integrate, review and merge

**Files:** Bot command/help catalogs, user/Mini App docs, changelog, exact shrinking
module-size exceptions and required dependency-policy entries.

- [x] Update command/help navigation and document SQLite/style behavior.
- [ ] Run relevant local checks and complete GitHub Linux CI on the final commit.
- [x] Independently review feature boundaries, regression maintenance and CI semantics.
- [ ] Fix concrete findings and rerun covering checks.
- [ ] Merge only after all required checks succeed on the reviewed head.
- [ ] Verify merge and update this worktree without disturbing the other active task.

## Execution notes

- Base: `7d3a5e40a8e78d055888ec22d2a4eb9274464628`.
- Baseline Linux run 37496980927: 3,727 tests, 811 subtests, 82.47% coverage.
- A concurrent task switched the original checkout. Own test files were copied,
  hash-checked and moved to the dedicated linked worktree before implementation.
- Windows-only test tooling includes colorama; no runtime lock change is needed.

- Integrated main `4462d1c3` (PRs #382/#383), preserving migration 26 and legacy
  tracker prompt/bootstrap retirement. The NPC service size limit is now 572.
- Review and merge evidence is tracked in
  [PR #384](https://github.com/cepeter/SillyTavern-Telegram-Bridge/pull/384).
- First Linux run on `8e76e9bd`: 3,782 tests and 811 subtests passed with 82.58%
  coverage and security floors passing. The fifteen-page UI and tracker assertions
  passed before a later smoke request burst hit the production rate limit.
- The aggregate correctly rejected that failed smoke job. The corrected harness
  reserves capacity before the twelve-request block and its following phase.
  Focused boundary replay passed; final CI and merge status are recorded in the PR.
