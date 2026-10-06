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

- [ ] Record baseline counts, exact helper duplication, current timing and ZeroClaw source.
- [ ] Exercise the gate with literal success/failure/missing/skipped/cancelled outcomes.
- [ ] Make `test` the strict aggregate of five independent jobs.
- [ ] Consolidate only identical helpers and shorten giant parameter IDs.
- [ ] Publish useful JUnit, coverage, smoke, reference and leak-scan evidence.
- [ ] Verify affected tests and independent CI gate behavior.

## Task 2: Expose saved canonical trackers

**Files:** `bridge/simulation_view.py`, optional output helper,
`bridge/simulation_commands.py`, `bridge/simulation_repository.py`,
`bridge/npc_service.py` only for reusable visibility,
`bridge/closed_session_guard.py`, Mini App tracker API/assets/router/navigation,
tracker tests and the existing Mini App smoke fixture.

**Interface:** A shared bounded tracker view supplies a whitelisted JSON projection;
Telegram renders that projection without using an LLM.

- [ ] Test visible fields, hidden-field exclusion and source/session boundaries first.
- [ ] Add saved progress/freshness reads and the bounded presentation.
- [ ] Register `/trackers` and allow it for inspection of closed stories.
- [ ] Add an authenticated read-only API and Manage page with empty/stale states.
- [ ] Exercise Telegram output bounds and Mini App rendering with real saved data.

## Task 3: Add image style selection

**Files:** `bridge/image_styles.py`, `image_panels.py`,
`image_generation.py`, `image_routing.py`, image callback sections in
`feature_callbacks.py`, image config checkpoint registries and focused tests.

**Interface:** Validated session preference `image_style:<chat>:<session>`
accepts `realism` or `anime`; absent/invalid saved values fall back to Realism.

- [ ] Test panel state, persistence, invalid input and reset.
- [ ] Test provider payloads for scene/custom, both styles and text/reference inputs.
- [ ] Reserve style overhead before advertising/validating custom-input limits.
- [ ] Include style in alternate-ending configuration copies.
- [ ] Run affected image and lifecycle regressions.

## Task 4: Integrate, review and merge

**Files:** Bot command/help catalogs, user/Mini App docs, changelog, exact shrinking
module-size exceptions and required dependency-policy entries.

- [ ] Update command/help navigation and document SQLite/style behavior.
- [ ] Run relevant local checks and complete GitHub Linux CI on the final commit.
- [ ] Independently review feature boundaries, regression maintenance and CI semantics.
- [ ] Fix concrete findings and rerun covering checks.
- [ ] Merge only after all required checks succeed on the reviewed head.
- [ ] Verify merge and update this worktree without disturbing the other active task.

## Execution notes

- Base: `7d3a5e40a8e78d055888ec22d2a4eb9274464628`.
- Baseline Linux run 37496980927: 3,727 tests, 811 subtests, 82.47% coverage.
- A concurrent task switched the original checkout. Own test files were copied,
  hash-checked and moved to the dedicated linked worktree before implementation.
- Windows-only test tooling includes colorama; no runtime lock change is needed.
