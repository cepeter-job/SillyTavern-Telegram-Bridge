# CI checks and regression maintenance

## Entry files

GitHub Actions only loads entry files from `.github/workflows/*.yml`. One concern
per file, with runnable tooling in `tools/` so it is covered by the repository
tests and the size ratchet.

| File | Trigger | Jobs | Protected |
| --- | --- | --- | --- |
| `ci.yml` | `pull_request`; `push` to `main` | `python-test-shards`, `python-tests`, `miniapp-smoke`, `secret-scan`, `dependency-audit`, `static-analysis`, `test` | Yes: the `test` aggregate is the branch-protected check |
| `pr-title.yml` | `pull_request` lifecycle | `pr-title` | No, advisory |
| `pr-size-labeler.yml` | `pull_request_target` lifecycle | `size-label` | No, advisory |
| `scheduled-audit.yml` | weekly schedule; manual dispatch | `advisory-audit` | No, advisory |
| `python314-compatibility.yml` | Python 3.14 migration file changes; manual dispatch | `voice-enabled-native-preflight`, `full-regression`, `resource-owner-trace`, `native-debugger` | Candidate promotion gates (strict failing checks); optional targeted trace is manual-only and never counts as the full-suite gate |
| `python315-experimental.yml` | manual dispatch only | `full-dependency-preflight`, `core-smoke-and-benchmarks`, `stable-final-core-regression` | No, experimental; a missing 3.15 native wheel blocks migration, not Python 3.11 delivery |

## Required result and independent jobs

The existing branch-protected `test` check is the aggregate result. It waits for
`python-tests`, `miniapp-smoke`, `secret-scan`, `dependency-audit`, and
`static-analysis`. Every dependency must report `success`. A missing, cancelled,
skipped, failed, malformed, or unexpected result fails the gate. The checker is
[`tools/ci_gate.py`](../../tools/ci_gate.py); its CLI tests exercise those outcomes
and the appended GitHub job summary.

Keep the `test`, `secret-scan`, `dependency-audit`, and `static-analysis` check names
stable. The separate GitHub CodeQL requirements continue to apply. If a quality
job is added, update both `test.needs` and the checker's required job list. The
checker deliberately rejects incomplete dependency sets instead of silently
accepting a reduced gate.

The licensed Gitleaks Action runs for trusted same-repository events and requires
the repository or organization Actions secret `GITLEAKS_LICENSE`. Fork pull
requests and Dependabot events use the SHA-256-verified full-history CLI without
the licensed wrapper because GitHub withholds ordinary Actions secrets from those
events. The CLI remains required for every event, with `--log-opts=--all`,
`.gitleaks.toml`, `.gitleaksignore`, redaction and failure on findings. On events
that also select the licensed wrapper, both scans must succeed. The wrapper's
token has only `contents: read` and `pull-requests: read` to discover PR commits;
comments, SARIF uploads and Action summaries are disabled.

| Job | Scope | Retained evidence |
| --- | --- | --- |
| `python-tests` | Complete pytest discovery, at most four workers, resource warnings as errors, whole-application statement and branch coverage, security coverage floors | `application-coverage`: coverage JSON/XML, JUnit XML, pytest log with the 20 slowest tests |
| `miniapp-smoke` | Focused DOM regressions and loopback integration, plus pinned Chromium and WebKit tests of accepted-response loss, polling recovery, native dialogs, focus and narrow viewports | `miniapp-smoke-chromium` and `miniapp-smoke-webkit`: DOM, integration and browser logs, browser HTML report and failure traces/screenshots |
| `secret-scan` | Full-history CLI v8.30.1 scan with SHA-256-verified binary on every event; trusted same-repository events also run the SHA-pinned licensed Gitleaks Action v3.0.0 commit-range scan | Findings, scanner errors or integrity mismatch fail the required job; a selected licensed wrapper also requires its organization license |
| `dependency-audit` | Complete hash-locked Python runtime/development sets and both npm development toolchains | `dependency-audit`: per-scan reports and JSON/Markdown summaries |
| `static-analysis` | All workflow syntax/expressions, dependency lock consistency, module-size ratchet, reference evidence, architecture policy, leak scan, Ruff, and mypy | `memory-leak-scan`: leak scan and module reference JSON |
| `test` | Strict aggregate of the five jobs above | Table of every dependency result in the job summary |

Reports are uploaded even when their producing job fails, when the report exists,
and expire after 14 days. Bash runs with pipeline failure propagation so capturing
a test log with `tee` cannot hide the test command's failure. Checkouts do not
retain credentials. Actions remain pinned to complete commit SHAs; Python and DOM
tooling continue to use the checked-in locks. Browser tooling has a separate npm
lock under `tests/miniapp-browser/`; its Playwright version pins engine revisions.
Both engines run in independent matrix jobs, with one worker per job and a fresh
isolated loopback fixture per test. DOM checks run once in the Chromium job;
`miniapp-smoke` succeeds only when both engines and those DOM checks pass.

## Dependency and workflow validation

`tools/audit_dependencies.py` attempts four independent scans: `requirements.lock`,
the combined `requirements-dev.lock`, `tests/miniapp-ui`, and
`tests/miniapp-browser`. Python scans use `--require-hashes --disable-pip --strict`;
the npm scans explicitly include development dependencies and retain the default
all-severity audit threshold. Each scan has a 90-second timeout. Findings, skipped
Python dependencies, execution errors, timeouts, and empty results fail the required
audit, while the remaining scans still run. Reports include every scan's outcome.

The weekly advisory job uses the same coordinator. The weekly run and manual
dispatches audit `main`; dispatches for other branches are skipped, and only `main`
runs can update the shared `ci-advisory` issue. Checkout preserves the event's
audited commit. A missing dependency summary prevents a clean report. The issue
shows all scan outcomes before bounded excerpts, and the full reports are retained
in `scheduled-advisory-audit`. Dependabot checks both npm directories every Monday,
with the same seven-day version-update cooldown and grouped minor/patch policy as
the existing dependency maintenance.

The required `static-analysis` job runs Actionlint 1.7.12 over all workflow entry
files. Its binary is SHA-256-verified before execution. This check covers Actions
syntax, expressions, job dependencies and action inputs; optional ShellCheck and
Pyflakes integrations are disabled so the result does not depend on unpinned tools
installed in a runner image. The existing Python linting and behavioral workflow
tests continue to run.

## Pytest partitioning and combined coverage

`python-test-shards` runs four independent GitHub-hosted jobs. The lightweight
`tools.pytest_shard` plugin collects the full suite before selecting a deterministic
whole-file subset, balanced by collected case count. It preserves collection order
and xdist's `--dist=loadfile` fixture locality. Each job has at most four workers;
that is separate from the four-machine CI matrix. Do not run all sixteen workers
concurrently on the shared local VPS.

Each shard publishes its raw `.coverage.shard-N` data, full/selected test-ID manifest,
JUnit XML and log. The existing required `python-tests` job rejects any unsuccessful
shard, verifies that all four inventories agree and that their selected union runs
every collected test exactly once without splitting files, and requires readable,
nonempty coverage data from every shard before combining it. This includes rejecting
an empty SQLite database that Coverage.py would otherwise initialize. The unchanged
global and security-module floors apply to this combined report. Individual partial
reports use a zero floor because no subset can
cover the full application by itself; they cannot bypass the required combined gate.

A duration-hint prototype evaluated on 2026-10-09 did not demonstrate a runtime
benefit: the Python test-and-coverage path took 290 seconds in the
[trial](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/runs/37876749578),
versus 252 seconds in the preceding
[control](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/runs/37876235967).
Individual test times also varied, so this does not establish that the allocator
caused the difference. The prototype was removed; collected case counts remain
the scheduling input. Compare complete exact-head job and step measurements before
changing allocation. Startup and the slowest indivisible file can limit scaling.

`setup-uv` and `setup-node` already cache lockfile-keyed Python and npm downloads.
`miniapp-smoke` also caches Chromium and WebKit binaries in
`~/.cache/ms-playwright`, keyed by runner OS, architecture and the locked
`tests/miniapp-browser/package-lock.json` (Playwright pins the browser
revisions). The SHA-pinned cache action is best-effort: every run still invokes
`playwright install --with-deps "$BROWSER_ENGINE"` to install system libraries
and repair missing browser binaries. Caches never skip tests or security scans.
Playwright cautions that browser cache restore can cost about as much as a
download; compare cold and warm `miniapp-smoke` runs before keeping this cache.
Only the external provider response is synthetic; the summary handler, utility
executor, API adapter and SQLite records are real. Browser emulation does not
claim native Telegram Android/iOS acceptance. See
[`docs/miniapp-browser-validation.md`](../../docs/miniapp-browser-validation.md).

All jobs run on pull requests and pushes to `main`. A newer run cancels the older
run for the same ref. The module-size ratchet compares with the pull-request base
or the pre-push commit, but only when that revision carries
`tools/module_size_baseline.json`. A revision that is unavailable, is the
all-zero new-branch value, or predates that file is skipped in favour of the head
commit's parent, so the tool cannot measure an older tree as today's caps. The
absolute 500-line limit and per-file exceptions still apply when no usable
revision exists. There are no changed path exclusions from regression coverage.

## Why this shape

The 2026-10-09 comparison examined current ZeroClaw workflows at
[`cbe753fbe3226378e0788bfd127ab1350b4c8628`](https://github.com/zeroclaw-labs/zeroclaw/tree/cbe753fbe3226378e0788bfd127ab1350b4c8628/.github/workflows).
Its [npm advisory workflow](https://github.com/zeroclaw-labs/zeroclaw/blob/cbe753fbe3226378e0788bfd127ab1350b4c8628/.github/workflows/daily-npm-audit.yml)
and [npm update policy](https://github.com/zeroclaw-labs/zeroclaw/blob/cbe753fbe3226378e0788bfd127ab1350b4c8628/.github/dependabot.yml)
informed the missing JavaScript dependency maintenance here. Both of this project's
npm toolchains contain development dependencies, so excluding those packages would
miss the relevant audit scope. We retained the existing stricter aggregate gate and
full regression discovery, and added all-workflow validation to the existing static
job. ZeroClaw's Rust/platform builds, release pipelines, change-filtered skips and
additional workflow families do not match this Python service's current CI needs.

The 2026-10-06 audit examined ZeroClaw at
[`e654b4b73f286201166cf574c7fe9ff342d3341d`](https://github.com/zeroclaw-labs/zeroclaw/tree/e654b4b73f286201166cf574c7fe9ff342d3341d).
Its [quality workflow](https://github.com/zeroclaw-labs/zeroclaw/blob/e654b4b73f286201166cf574c7fe9ff342d3341d/.github/workflows/ci.yml)
provides useful patterns: independent jobs, cancellation, and an always-evaluated
aggregate. Its
[workflow registry](https://github.com/zeroclaw-labs/zeroclaw/blob/e654b4b73f286201166cf574c7fe9ff342d3341d/.github/workflows/README.md)
records each bespoke gate's invariant, origin, and retirement condition.

ZeroClaw also has change-filtered jobs and accepts their intentional skips. This
repository's five jobs run unconditionally, so a skipped dependency is an error.
The measured suite was short enough that adding a separate change classification
system was not justified. Keeping the complete regression run avoids maintaining
another map of indirect imports, runtime wiring, templates, and security scopes.

The previous `test` job ran browser checks before Python regressions. A browser
failure therefore suppressed the regression and coverage result. Running those
checks independently produces both results and removes their serial runtime from
the critical path. Reusing the protected `test` name for the aggregate ensures
that splitting the jobs does not make browser checks optional for merging.

Advisory workflows report without gating: `pr-title.yml` and
`pr-size-labeler.yml` stay outside the protected `test` aggregate, and
`scheduled-audit.yml` fails only its own scheduled run when a finding cannot be
recorded. `pr-size-labeler.yml` is the only `pull_request_target` workflow,
because labelling a fork pull request needs a write-scoped token; it never checks
out pull-request code and fetches its classifier from the trusted base revision.

| Bespoke gate | Protected invariant | Origin | Retirement condition |
| --- | --- | --- | --- |
| Required `test` aggregate | Every independent CI quality job succeeds before the protected check is green | 2026-10-06 CI/test audit of baseline `7d3a5e4`; former combined browser/Python job | Retire only when an equivalent protected check enforces the complete job set and rejects missing or unexpected skips |
| Shard evidence | Every collected test executes exactly once in whole-file shards, and every shard supplies usable coverage before combined floors are checked | 2026-10-09 workflow audit of `5cfb6aed`; missing/unreadable coverage and empty SQLite replays | Retire only when an equivalent runner proves complete execution and requires coverage evidence from every shard |
| Module-size ratchet base | The ratchet compares with a revision the change was reviewed against that also carries `tools/module_size_baseline.json`, so a cap can only shrink relative to a comparable tree | 2026-10-07 workflow audit; reproduced from a push whose pre-push revision predated the baseline file | Retire only when every comparison revision is guaranteed to carry the baseline file, or the tool resolves the reviewed base itself |
| Advisory `pr-title` | Pull-request titles keep the `type(scope): subject` convention that squash subjects and release grouping rely on | 2026-10-07 workflow audit; `tools/check_pr_title.py` | Promote to a required check by removing `continue-on-error`, or retire if the project adopts a different title policy |
| Advisory `size-label` | Every pull request carries exactly one canonical `size:*` label for review planning | 2026-10-07 workflow audit; `tools/pr_size_label.py` | Retire if review planning stops using size labels |
| Advisory `advisory-audit` | New advisories, leak-shaped changes and cap violations in unchanged `main` surface in one labelled issue between dependency updates | 2026-10-07 workflow audit; weekly re-audit | Retire only when an equivalent periodic re-audit exists, or dependency updates and scans become continuous |

The 2026-10-07 audit is
[`docs/audits/2026-10-07-ci-workflow-audit.md`](../../docs/audits/2026-10-07-ci-workflow-audit.md).

## Audit baseline

Measurements below are from the immutable
[`7d3a5e40a8e78d055888ec22d2a4eb9274464628`](https://github.com/cepeter/SillyTavern-Telegram-Bridge/tree/7d3a5e40a8e78d055888ec22d2a4eb9274464628)
tree. Counts use tracked files, include blank/comment lines, and exclude generated
files and environments.

| Measure | Baseline |
| --- | ---: |
| Production `bridge` Python files / lines | 362 / 63,061 |
| Python test and support files / lines | 295 / 74,043 |
| Test modules / support files | 285 / 10 |
| Functions named `test_*`, before parameter expansion | 2,704 |
| Direct source-inspection test functions / containing modules | 303 / 88 |
| Tracked `tools` files / lines, including policy JSON | 15 / 4,050 |

Tests and helpers contain about 1.17 lines for each production Python line. The
303 direct source-inspection functions are about 11.2% of the named test
functions. This is an audit heuristic: it counts direct source reads,
AST/symbol-table/signature inspection, and selected source helper calls inside
test functions. It does not fully follow helpers, and some of these tests also
exercise runtime behavior. Neither ratio establishes that a test is redundant.

| Largest test modules | Lines |
| --- | ---: |
| `test_composition.py` | 2,176 |
| `test_image_generation.py` | 1,530 |
| `test_operation_recovery.py` | 1,141 |
| `test_delivery_review_regressions.py` | 1,072 |
| `test_live_sync.py` | 911 |
| `test_memory_service.py` | 880 |

| Largest tooling files | Lines |
| --- | ---: |
| `leakscan.py` | 1,265 |
| `evaluate_story_memory.py` | 595 |
| `static_analysis.py` | 534 |
| `miniapp_ui_smoke.mjs` | 472 |
| `story_memory_eval_support.py` | 411 |

The [successful Linux baseline run](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/runs/37496980927)
passed 3,727 pytest cases and 811 subtests in 195.39 seconds, with 82.47% combined
statement and branch coverage. The former combined `test` job took 292 seconds:
its browser smoke step took 67 seconds, Node/npm setup took another 9 seconds,
and its Python test step took 197 seconds. The other jobs took 10-20 seconds.
These are observed timings from one run, not a promised performance target.

## Focused consolidation and future maintenance

Seven import/startup boundary modules contained identical fresh-interpreter
helpers. They now use `tests/python_process_test_support.py`, preserving a fresh
process for each probe, the same working directory, and captured output. Two
boundary modules also duplicated global-name analysis; they now use
`referenced_globals` in `tests/source_test_support.py`. The existing 71 affected
test bodies were compared as ASTs after normalizing those helper call names; all
assertions and inputs were preserved.

The two bound-name scanners have different handling for module-level loop
bindings, so they remain separate. Other similar-looking fixtures cover
persistence state, ownership, provider failures, and recovery contracts; their
names alone do not establish equivalence.

One provider health test supplied a 4,000,001-byte payload without a parameter ID.
Pytest expanded that data into the case name, making failure reports and JUnit
output unnecessarily large. Its four original inputs now have short explicit
IDs, including `oversized` for the size-limit case.

Use the JUnit report and slow-test list to identify expensive cases before
changing scheduling. Keep unique security, recovery, and import-isolation
coverage. Consolidate duplicated setup when its behavior is demonstrably equal;
retire an architectural guard only after identifying its protected invariant and
retaining equivalent enforcement. Avoid replacing readable, local fixtures with
a broad shared fixture that couples otherwise independent test domains.
