# Context Efficiency Implementation Plan

> **For agentic workers:** Use Superpowers execution and independent review.
> Each task follows a failing public-behavior test, minimal implementation and
> focused regression verification before integration.

**Goal:** Implement the reversible context preparation and evaluation capability
described in issue #421 without changing default provider prompts.

**Architecture:** Reuse scoped canonical blocks and durable checkpoints. Capture
content-free accounting during construction, retain an ephemeral baseline and
candidate, and revalidate selection before the existing final budget check.

**Tech stack:** Existing Python 3.11, SQLite, pytest and repository dependencies.

**Spec:** `docs/superpowers/specs/2026-10-08-context-efficiency.md`

## Global constraints

- Default mode is `off`; `enabled` also needs explicitly approved slices.
- No new dependencies, schemas, memory database, provider routing or live calls.
- Preserve exact current input, roles, mandatory policies and source audiences.
- Missing accounting is unknown; estimates cannot prove provider savings.
- No history removal without source coverage and complete continuity proof.
- No existing module-size exception may grow; new Python modules stay below 500 lines.

## Review focus

- Same text from distinct canonical sources or audiences remains distinct.
- Async recall, rewrites, deletion and alternate branches invalidate candidates.
- Continuation targets, negation, repeated dialogue and images survive exactly.
- Helper failure, repair and fallback cannot hide input or advance pending parts.
- Structural checks and synthetic estimates never auto-approve quality or rollout.

## Task 1: Section attribution and redacted persistence

Files: `bridge/context_section_metrics.py`, `bridge/context_diagnostics.py`,
`tests/test_context_section_metrics.py`; generation integration in Task 3.

Interface: `estimate_context_sections(sections, *, chars_per_token=4.0)` returns
only `mandatory`, `history`, `world_info`, `derived`, `task` integer estimates.
Persist as `section_estimated_tokens`; strict `selection_metrics` allowlist.

- [x] Prove missing attribution and unsafe metric values fail regression tests.
- [x] Implement deterministic estimates and validation at both save and read.
- [x] Preserve builder metrics when transport attempts report final budgets.
- [x] Verify accounting/window tests and independent integration review.

## Task 2: Canonical selector and continuity preflight

Files: `bridge/context_selection.py`, `bridge/context_selection_store.py`,
memory contracts/service/composition/readers and dedicated selection tests.

Interfaces: `context_selection_mode(app_settings)`,
`context_slice_enabled(app_settings, slice_name)`, and
`select_memory_blocks(scope, blocks)` returning selected channel blocks and
bounded decision counters. Extend `MemoryPromptContext` with ephemeral baseline,
candidate and revalidation data, keeping existing default fields unchanged.

- [x] Prove exact duplicates collapse and distinct source/audience facts survive.
- [x] Preserve leaf-to-evidence association during canonical construction.
- [x] Apply pure selection only after local eligibility validation.
- [x] Test missing coverage, historical scope, invalid pointers and source races.
- [x] Retain baseline whenever required fact/causal closure cannot be proved.
- [x] Independently review source and reader safety before activation logic.

## Task 3: Prompt construction, single dispatch and fallback

Files: generation and its story/edit/regen/continue/image callers, small prompt
runtime helpers and `tests/test_context_selection_dispatch.py`.

- [x] Prove off and shadow preserve the complete provider payload for valid captures.
- [x] Attribute sections from construction-owned spans without prompt parsing.
- [x] Keep original policies/roles and replace only selected derived payloads.
- [x] Revalidate captured selection immediately before final budgeting.
- [x] Preserve a valid baseline on selection uncertainty; stop before dispatch
  on known source/reader revocation and preserve protected-budget errors.
- [x] Verify current text/images/continuation, task additions and single dispatch.

## Task 4: Summary helper projection

Files: `bridge/helper_input_projection.py`, `bridge/memory.py`,
`tests/test_helper_input_projection.py`.

Interface: `project_summary_messages(messages, previous, *, mode)` returns
baseline for off/shadow and a lossless serialized candidate when enabled.

- [x] Demonstrate enabled input is smaller with identical parsed prior/source.
- [x] Preserve settings, model, schema, source role/offsets and one dispatch.
- [x] Exercise real durable checkpoint reuse, malformed responses and rewrites.
- [x] Count failed unknown primary usage and reported fallback usage separately.

## Task 5: Paired full-story replay and review gate

Files: `tools/evaluate_context_efficiency.py`, small evaluation support modules,
synthetic fixtures, evaluator tests and user documentation.

- [x] Run the real story builder and output-aware final budget on frozen captures.
- [x] Keep baseline/candidate inputs matched without answer hints in prompts.
- [x] Validate paired observations and include all attempted accepted-work input.
- [x] Block savings/quality approval on unknown usage or unreviewed/regressed output.
- [x] Produce offline CLI reports with zero requests and explicit quality limits.

## Integration and delivery

- [x] Review all changes independently and resolve material findings.
- [x] Run the full suite/coverage/security/static/size gates on the final revision.
- [ ] Create a focused PR linked to #421 and wait for repository CI.
- [ ] Merge the verified PR and record remaining separately authorized evaluation.
- [x] Keep live activation off; do not claim measured savings or narrative equivalence.

## Local verification before PR

Independent runtime and evaluator reviews approved the final changes. The full
resource-warning/coverage run completed with 4,296 passed, 817 subtests passed
and seven installer failures caused by the root-only execution environment.
All seven reproduce on unmodified base `7c4893dd40fab33eccd1f5f0e05f02464191414e`;
non-root repository CI remains a required merge gate. Coverage is 83.36%, and
all four security coverage floors pass. Lock, size, reference, architecture,
Ruff and the 174-file CI mypy surface pass. Native offline replay estimates
10,458 input tokens for each variant (0% reduction), with zero model calls.
