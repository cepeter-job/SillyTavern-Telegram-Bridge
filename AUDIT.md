# Repository audit — RAM, resource ownership, and preproduction cleanup

Date: 2026-10-03. Baseline: `fc0990e` (main, including PR #344).

Later focused review: [2026-10-06 audit consolidation](docs/audits/2026-10-06-consolidation.md).
The measurements below remain the dated October 3 snapshot.

Separate focused review: [2026-10-07 CI workflow audit](docs/audits/2026-10-07-ci-workflow-audit.md)
covers the GitHub Actions entry files, gate inputs and the reference-project
comparison. It is a workflow review, not part of the resource-ownership
measurements above.

## Scope and evidence

Repository-wide tracked-file, Python AST/reference, architecture, dependency-lock,
and static resource scans; targeted manual review and executable regressions for
cache ownership, SQLite handles, Telegram errors, background admission, runtime
startup/shutdown, and retired interfaces. The baseline contained 557 tracked files
and 266 runtime Python modules. There were no exact duplicate nonempty files,
import cycles, or reciprocal import pairs. Static leakscan reported zero findings,
but the new behavioral regressions still exposed real cleanup/retention defects.

This is an engineering audit, not a proof of zero possible leaks or a production
load certification. The earlier audit remains available in Git history rather
than presenting its obsolete metrics and recommendations as current findings.

## Findings addressed

| Finding | Correction and regression evidence |
|---|---|
| Decoded native data could greatly exceed the 4 MiB source-byte cache budget | Account for the decoded object graph and key, with an additional 4 MiB estimated-heap budget. Evict least-recently-used entries. Count shared objects once, handle cycles, and bypass caching for unknown object types. |
| Large or stale cache misses were unnecessarily deep-copied | Return uncached loader results directly. Continue copy isolation for retained entries and cache hits. |
| SQLite setup failures leaked ownership of opened handles and could retain the writer gate | Close initialized and lightweight connections on every escaping `BaseException`, including interrupted schema/pragmas. |
| Telegram HTTP-error streams survived retries or chained exceptions | Close request-error bodies in `finally` and raw polling-error bodies before retry waits. |
| Shutdown retained ordered-queue closures and payloads; a submission race could restore them | Atomically stop admission and clear waiting queues. Do not requeue after shutdown. Let running work finish; keep durable recovery in SQLite. |
| Startup/recovery exceptions bypassed runtime cleanup | Unwind admission, health, diagnostics, sync, executors, and the database with `finally` and `ExitStack`. Continue cleanup when another cleanup callback fails. Run maintenance only after a clean exit. |
| Hindsight synchronous SDK calls left their thread event loop and socket pair open | Give each synchronous SDK client an `asyncio.Runner` scope spanning construction, request, and transport cleanup. Real SDK cleanup is exercised without network calls; repeated recall/retain success/failure tests require every allocated loop to be closed. |
| Resource warnings could become non-failing unraisable warnings in pytest | Treat both `ResourceWarning` and `PytestUnraisableExceptionWarning` as errors, alongside existing thread-error checks. |

The first regression run produced 16 expected failures. Another six startup
failure/interruption regressions failed before lifecycle cleanup was implemented.
A raw polling HTTP-error regression also reproduced open response ownership before retry.
The full CI suite exposed an additional SDK event-loop leak. Allocation tracing
identified Hindsight synchronous cleanup; four repeated-operation regressions
failed before adding explicit loop ownership. The warning policy was not weakened.
The CI-warning guard and ten retirement guards also failed before their fixes.
Tests include genuine SQLite handles, weak references to queued payloads, a real
executor with controlled events, and decoded-data cache eviction/copy checks.

## Measured cache retention

Synthetic `tracemalloc` comparison of the original and revised cache in the same
Python environment: 20 JSON files, each containing 10,000 small objects; load each,
discard returned copies, collect garbage, then measure retained traced allocations.
The files together fit the original source-byte budget.

| Measurement | Baseline | Revised |
|---|---:|---:|
| Retained traced allocations | 43,968,158 bytes | 2,199,594 bytes |
| Peak traced allocations | 46,596,846 bytes | 7,650,659 bytes |
| Entries retained | 20 | 1 |

That is approximately 95.0% less retained traced memory and 83.6% lower traced
peak for this workload. It is not a measurement of live bridge RSS. Cache sizing
estimates Python object graphs; it is not an allocator, native-buffer, or process
RSS limit. Entry-count limits also bound cache bookkeeping overhead.

## Dead code and compatibility retirement

Removed these ten functions from production after checking all tracked references:

- `fail_choice_generation`, `persist_assistant_delivery_ids`;
- `parse_story_response`, `parse_npc_extraction`, `grounded_user_label`;
- `handle_imagine_prompt`, `acknowledge_pending_update`;
- `find_npc_exact`, `list_episodic_memories`, `store_character_rank`.

The first choice helper was fixture setup and is now explicit test SQL. NPC,
episodic, and character-rank setup/observation live in a tests-only support module;
the test-only `EpisodicMemory` record moved there too. Parser tests now exercise
the live diagnostic parsers. Update-ack tests assert canonical status values.
All four image-only delivery/progress-cleanup tests now exercise the live route
owner rather than a retired text-command helper. One test that exclusively tested
the removed delivery-metadata wrapper was retired; current delivery/recovery tests
remain. No compatibility aliases were introduced.

Four completed callback/image-routing plans and specifications were removed from
current docs; they remain in Git history. Governance guards prevent their return.
Public examples, active user/operations docs, and the future humanizer design remain.
Dynamic urllib and HTMLParser hooks are real callbacks, not unused functions.
Active provider protocols and data-safety migration/purge behavior were preserved;
those are not compatibility facades to delete indiscriminately.

## Verification and limitations

Run the changed-area regressions, Ruff lint/format, architecture scan, dependency
lock validation, leakscan, the repository mypy target set, and `git diff --check`.
Local verification passed: **373 selected tests** before the additional SDK finding;
the Hindsight ownership follow-up passed **99 tests plus 65 subtests**. Ruff
lint/format (505 Python files), architecture/dependency-lock/leakscan checks,
mypy (99 target files), and `git diff --check` also passed. A deliberately leaked file was independently rejected by the
new unraisable-warning policy. The final runtime reference sweep found no remaining
unreferenced top-level function candidates under the audit heuristic.

GitHub CI runs the full Python/coverage, UI smoke, dependency/security, and CodeQL
gates on this PR. Exact final run results are recorded in the PR checks and body.
No coverage threshold was lowered and no leak-scanner suppression was added.

The production host's full-suite guard was honored: broad testing stays in CI,
with focused single-worker tests locally. No production service, database,
configuration, credentials, or release was changed. No paid provider calls were
used. A long-duration production soak and native-library/GPU allocation profiling
were not performed; the benchmark does not establish that every runtime path is
leak-free. Existing SQLite page-cache/mmap budgets were not blindly reduced, as
latency and I/O trade-offs need representative workload measurements.
