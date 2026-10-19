# Helper JSON recovery implementation plan

> **For agentic workers:** Use Superpowers subagent-driven development for the independent NPC and curator tasks, then review the integrated branch.

**Goal:** Correct the reproducible helper diagnostics and curator recovery gaps identified in the 18 October live audits, then validate, merge, deploy, and release.

**Architecture:** Keep existing durable source claims, private drafts, publication guards, and bounded provider recovery. Add fixed NPC audience subcodes to the existing parser/repair path. Give curator output its own parser classification while reusing bounded canonical recovery where its private-panel contract permits.

**Tech stack:** Existing Python 3.11 runtime, SQLite, pytest, Ruff, and protected GitHub Actions.

**Spec and evidence:** The two supplied `sttb-live-audit-20261018T024542Z.report.md` and `sttb-helper-json-audit-20261018.md` reports are the investigation inputs. Source inspection at `c256c46237f9df4381028b980df2adcef80e3b7e` confirms the relevant runtime is unchanged from audited live commit `e5877e3b60336fd8bcb42ed5f70154f71928336e`. At those commits the curator parser was named `parse_curated_memories`; the second audit's `parse_curator_response` name is inaccurate for the audited source.

## Global constraints

- Preserve audience rejection, accepted-state publication, canonical source validation, NPC coverage, and independent simulation publication.
- Use only fixed content-free diagnostic codes; rejected provider output must never become repair input or log content.
- Permit at most one format repair per extraction. Keep provider rate-limit handling and autonomous failure limits intact.
- Curator is a private panel accumulator with no character-audience contract; it must not be treated as classified shared memory.
- Preserve helper routes, budgets, story settings, dependencies, and Python version unless a separately measured comparison establishes a necessary operational correction.

## Review focus

- Malformed or wrongly shaped curator output cannot clear accepted memory or advance coverage.
- A source edit/deletion during inference suppresses stale repair/publication.
- A capped response remains incomplete even if its JSON parses.
- NPC diagnostics remain bounded and private across mixed invalid operations and failed repair.
- An invalid NPC repair cannot discard independently valid original simulation output.

## Task 1: NPC audience diagnostics

**Files:** `bridge/npc_extraction_format.py`, `bridge/npc_extraction.py`, `bridge/extraction_contracts.py`, `bridge/diagnostic_events.py`, and focused tracker contract tests.

**Interfaces:** Preserve the parser's `(groups, valid)` return and generic `invalid_audience` reason. Add an allowlisted optional subtype in diagnostics and canonical repair guidance.

- [x] Write and run failing cases for visibility type/value, knower type/size/entry, shared knowers, missing restricted knowers, and shared secrets.
- [x] Add deterministic safe subtype classification without changing parser acceptance or defaults.
- [x] Propagate subtype through initial/repaired parser events and existing repair instructions, with no raw output reuse.
- [x] Verify privacy canaries, bounded retries, and independent simulation publication using the existing contract suites.

## Task 2: Curator parser and bounded recovery

**Files:** `bridge/memory_curator.py`, a focused curator response module if needed, `bridge/memory_retry.py`, `bridge/memory_response.py`, and curator recovery tests.

**Interfaces:** Use one production parser, `parse_curator_response`, retaining valid-item normalization, stable-key generation, and deduplication without a legacy parsing path. Extraction classifies malformed JSON separately from wrong envelope/invalid populated output. If generic recovery gains a contract option, its default must retain classified-audience checks for all current callers.

- [x] Write and run failing worker cases for malformed JSON, wrong envelope, and populated output containing no valid candidates.
- [x] Add fixed parser-owned errors and one canonical-source repair, preserving valid curator normalization.
- [x] Suppress repair if the canonical source is no longer valid; keep provider failures distinct from parser failures.
- [x] Verify capped private curator output can receive the bounded repair without disabling classified-memory audience guards.
- [x] Verify repeated failure retains accepted state and pending coverage, and produces no private diagnostics.

## Task 3: Integration and release

- [x] Review both task diffs independently, then review the combined branch.
- [ ] Run focused contracts, full CI, architecture/static analysis, privacy/security checks, and required protected checks for the exact PR head.
- [ ] Merge only with passing required checks and current base/head verification.
- [ ] Use the established trusted signing and backup/deploy procedure; publish the authorized new release.
- [ ] Resume only the affected current-session layers through bounded supported claims after correction. Verify live service, source identity, and actual memory progress; report any remaining provider limitation precisely.
