# Testing and CI

Use a focused regression while developing, then collect the complete verification
evidence before review. Run commands below from the **repository root**, in the
isolated Python 3.11 environment from [setup](setup.md). In these examples,
`python` means that environment's interpreter. Node 24+ and npm are needed only
for development tooling involving the Mini App or npm dependency audits.

[CONTRIBUTING.md](../../CONTRIBUTING.md) governs the contribution and merge
requirements. This guide explains how to apply them; the executable authority for
CI steps is [ci.yml](../../.github/workflows/ci.yml).

## Choose the smallest useful regression

Start with the behavior that would fail without your change. Locate related tests
by the owning module, use case or visible action, then inspect their fixtures:

```bash
rg -n 'transaction|offset' tests/test_sqlite_contention.py
python -m pytest --collect-only -q tests/test_sqlite_contention.py
python -m pytest -q tests/test_sqlite_contention.py
python -m pytest -q tests/test_sqlite_contention.py -k durable_offset
```

Collection lists the actual node IDs; use one of those IDs after the filename's
`::` to select an individual test. `-k` matches names and can select no tests, so
check the reported selection. A focused pass proves the selected regressions,
not the whole application. For a bug, establish that the regression fails for
the expected behavior before changing the implementation.

Useful starting points include:

| Change | Inspect first |
| --- | --- |
| Transactions, connections or lock ownership | [test_sqlite_contention.py](../../tests/test_sqlite_contention.py), [test_repository_transactions.py](../../tests/test_repository_transactions.py) |
| Dependency boundaries or injected collaborators | [test_application_dependency_boundaries.py](../../tests/test_application_dependency_boundaries.py), [test_port_contracts.py](../../tests/test_port_contracts.py) |
| Native character or memory storage | [test_character_mutation_safety.py](../../tests/test_character_mutation_safety.py), [test_memory_native_backend.py](../../tests/test_memory_native_backend.py) |
| Mini App server/browser recovery | [test_miniapp_browser_recovery_fixture.py](../../tests/test_miniapp_browser_recovery_fixture.py), [recovery.spec.mjs](../../tests/miniapp-browser/recovery.spec.mjs) |
| Installation documentation or repository governance | [test_installation_docs.py](../../tests/test_installation_docs.py), [test_governance.py](../../tests/test_governance.py) |

These are starting points, not a complete impact map. Follow callers and adjacent
behavior using [architecture](architecture.md), and add assertions for results,
identity, rollback or recovery rather than merely reproducing implementation
shape. Existing source-shape checks still protect ownership boundaries; a real
owner move needs their updates and behavioral coverage together.

## Keep fixtures isolated

Tests use temporary databases, native files, Git repositories and synthetic keys.
Use `tmp_path` or the existing temporary-directory patterns instead of an installed
bridge home. Git and `ssh-keygen` are prerequisites for relevant integration
tests. No real bot token, provider account or private conversation should be
needed. Inject narrow collaborators or patch the actual defining/calling owner;
retired aggregate modules are not supported patch surfaces.

[tests/conftest.py](../../tests/conftest.py) provides `app_settings_builder`, which
uses [SettingsBuilder](../../tests/settings_test_support.py), and an autouse
network guard. That guard rejects external socket/HTTP attempts and fails teardown
even when application code catches the blocked error. Numeric loopback addresses
and Unix sockets support local fixtures; bind local HTTP servers to loopback.
The guard is **not an operating-system sandbox**: DNS and child-process I/O remain
the test author's responsibility. Mock external transports before constructing or
sending their requests. See [network_test_support.py](../../tests/network_test_support.py)
and [test_network_test_guard.py](../../tests/test_network_test_guard.py).

Shared fixture implementations belong in focused `tests/*_test_support.py` modules,
not in another test module. Import `session_db` explicitly from
[session_test_support.py](../../tests/session_test_support.py) when a case needs
the standard temporary session database. It remains function-scoped: every test
gets its own settings, persona seed and SQLite connection, closed at teardown.
Keep domain-specific autouse transport guards local to the tests that need them;
sharing a database fixture must not silently enable or disable those guards.

Release every resource the test owns: connections, cursors, response bodies,
threads, executors, servers and child processes. Fixture teardown must also work
after a failed assertion. The session-finish hook drains background work and
fails the session if it cannot finish. [pyproject.toml](../../pyproject.toml)
treats resource, unhandled-thread and unraisable-exception warnings as errors;
a green assertion followed by a teardown warning is still a defect.

For a failure involving ordering, concurrency or a native extension, first
reproduce it without xdist:

```bash
python -X dev -W error::ResourceWarning -m pytest -q tests/test_memory_native_backend.py
```

Serial pytest has no `-n` argument. For a broader local run, use the bounded
`-n 2 --dist=loadfile` command below; whole-file scheduling keeps each file's
fixtures together. A serial pass alone does not clear a parallel failure. Check
shared paths, environment restoration, background jobs and process lifetimes.
Native dependency import/install failures should first be checked against the
Python version and locked environment; changing pins to make one machine work is
a separate dependency change.

Do full development testing on an isolated development machine or in CI. The
`PRODUCTION_HOST` marker activates a safeguard of at most two xdist workers and
512 collected tests in `conftest.py`. Treat that safeguard as a reason to move
verification to the development environment, not as the normal testing setup.
CI's four independent machines, each with up to four workers, are not a suitable
single-machine concurrency setting for a shared VPS.

## Keep expensive regression setup proportional

Reuse immutable synthetic observation input rather than rebuilding the same plan
before every validation. Decode a fresh copy per test; do not share SQLite
connections, mutable reports, provider transports or corpus state. Keep actual
CLI entrypoint subprocess cases and full default/wire replays, while rejection
matrices may exercise the real parser and main function in-process with restored
environment and captured output. Report those as in-process tests, not subprocess
execution evidence. Backend-specific replays need only their asserted backends.

Test filenames do not encode permanent architecture. The specific helper-owner
check in `test_ci_gate.py` protects existing extracted support functions; it does
not reserve old test filenames. Keep current behavioral gate tests independent
of historical refactor naming. Never skip coverage or security checks to improve
CI timing.

The DOM smoke runner opts into a loopback-fixture-only HTTP clock so reserving a
new request window does not sleep for a real minute. The production limiter,
authentication and request history are unchanged. The fixture control requires an
ephemeral owner key and is absent by default. Dedicated HTTP tests exercise 120
accepted requests, rejection of request 121, rejection of unauthorized clock
changes, expiry and a second complete rate window. Chromium/WebKit fixtures keep
real clocks. Controlled-fixture timing is not production request-latency evidence.

## Run the Python and static gates

Run the required local verification from
[CONTRIBUTING.md](../../CONTRIBUTING.md#required-verification), with the additional
CI checks shown here when preparing a complete review packet:

```bash
python -m pip check
python tools/check_dependency_lock.py
python -m ruff check .
python -m ruff format --check .
python tools/static_analysis.py
python tools/static_analysis.py --print-type-targets | xargs python -m mypy
python tools/check_module_sizes.py --base-ref "$(git merge-base HEAD origin/main)"
python -m tools.audit_module_references --output /tmp/bridge-module-references.json
python tools/leakscan.py bridge/ --fail-on high --format markdown -o /tmp/bridge-leakscan.md
python -X dev -W error::ResourceWarning -m pytest -q -n 2 --dist=loadfile --cov \
  --cov-report=term-missing:skip-covered \
  --cov-report=json:coverage.json --cov-report=xml:coverage.xml
python tools/check_security_coverage.py coverage.json
git diff --check
```

The size command assumes an available, current `origin/main`; fetch it as described
in [workflow](workflow.md) before choosing the comparison base. Ruff checks the
complete Python tree. `static_analysis.py` checks cycles and dependency-direction
rules; its printed mypy targets are the selected typed surface, not a claim that
every Python file is type checked. CI additionally compares the target count with
[type_surface_baseline.json](../../tools/type_surface_baseline.json), so shrinking
the list cannot silently remove that coverage.

The module-size gate protects the 500-line ceiling and shrinking exceptions.
The reference report records static and literal evidence for review; it does not
authorize deletion of dynamically called code. The leak scan reports suspicious
production patterns, with high-severity findings failing CI. Read the actual
finding and ownership path rather than suppressing it to obtain a pass.

Coverage measures statement **and branch** coverage over all `bridge/` modules,
including subprocess work via Coverage.py's configured subprocess patch. The
global floor is 76%; selected security modules have separate floors in
[security_coverage_baseline.json](../../tools/security_coverage_baseline.json).
Keep the JSON report for the security check. A focused run with `--cov` will often
fail the global floor because it has not exercised the complete application.
Do not lower the floor, omit modules or treat that partial report as full-suite
evidence.

For documentation-only work, the focused check prescribed by CONTRIBUTING is:

```bash
python -m pytest -q tests/test_installation_docs.py tests/test_governance.py
git diff --check
```

Also inspect rendered Markdown and links. Normal PR checks still apply.

## Test the Mini App at the right layer

DOM regressions are fast checks of navigation, model inheritance, drafts and
uncertain operations against explicit mocked APIs. The separate jsdom smoke
harness exercises actual page modules with a temporary authenticated loopback
server. From the repository root, with Node 24+ and the Python environment active:

```bash
npm ci --prefix tests/miniapp-ui --include=dev --ignore-scripts --no-audit --no-fund
npm test --prefix tests/miniapp-ui
MINIAPP_JSDOM_ROOT=tests/miniapp-ui PYTHON=python \
  node --experimental-vm-modules tools/miniapp_ui_smoke.mjs
```

Real-browser checks add native HTML dialogs, focus, mobile viewport behavior,
portrait cancellation and accepted-request/polling recovery. Install the separate
locked Playwright tooling, then its browsers and OS prerequisites:

```bash
npm ci --prefix tests/miniapp-browser --include=dev --ignore-scripts --no-audit --no-fund
node tests/miniapp-browser/node_modules/@playwright/test/cli.js install --with-deps chromium webkit
PYTHON=python npm test --prefix tests/miniapp-browser
python -X dev -W error::ResourceWarning -m pytest -q tests/test_miniapp_browser_recovery_fixture.py
```

The browser installer requires downloads and may need permission to install
system libraries; use a suitable development host. If an engine cannot launch,
record the missing prerequisites and that engine as untested. To investigate
one engine, append `-- --project=chromium` or `-- --project=webkit` to the npm
test command. The checked-in [Playwright configuration](../../tests/miniapp-browser/playwright.config.mjs)
uses one worker and no retries. `PYTHON=python` ensures the fixture child uses
the active locked interpreter.

Each browser test launches an isolated loopback fixture with synthetic Telegram
authentication, actual API/job handling and a synthetic external provider. The
browser fixture blocks external requests. See
[Mini App browser validation](../miniapp-browser-validation.md) for the measured
scenario boundaries. CI currently uses a digest-pinned Playwright container with
both engines already installed; it verifies those binaries and runs each engine
in a separate matrix job. DOM checks run once in the Chromium job.

Browser emulation does not establish actual Telegram Android/iOS WebView,
keyboard, viewport or native API behavior. Changes at those boundaries also need
manual acceptance in the relevant Telegram clients using an isolated test bot
and synthetic data. Record the client/platform, steps and observations separately
from automated results; do not infer model-answer quality from a mocked provider.

## Security scans and network-dependent evidence

Dependency installation, browser download and vulnerability scanning need network
access; the Python regressions themselves must remain offline except for local
fixtures. With npm available, run the same four-lock audit coordinator as CI:

```bash
python tools/audit_dependencies.py --output-dir /tmp/bridge-dependency-audit-reports
```

It attempts runtime Python, combined development Python, DOM npm and browser npm
scans independently, retaining per-scan reports and summaries. Findings, errors,
timeouts, unauditable packages or missing output fail the result. A connectivity
failure is incomplete audit evidence. The offline lock-consistency check, hash
verification during installation, `pip check` and vulnerability audit answer
different questions; none substitutes for the others.

CI also validates workflow syntax with a verified Actionlint binary and scans
full Git history with verified Gitleaks. Use
[ci.yml](../../.github/workflows/ci.yml) for their exact download versions,
checksums and arguments rather than installing an unpinned replacement. GitHub
CodeQL Default Setup supplies additional required checks outside that file;
[CONTRIBUTING.md](../../CONTRIBUTING.md#required-verification) lists their names.
Report security issues through [SECURITY.md](../../SECURITY.md).

Live memory/provider evaluations and postrelease tools are separate experiments,
not routine regression commands. Tests with `live` in their filenames can still
be offline tests of those tools. Running the underlying evaluation can contact
real services or mutate owned test resources, so follow its specific protocol
and authorization; ordinary contributor testing does not require doing so. See
[maintenance](maintenance.md) and the relevant evaluation runbook.

The bridge entrypoint's `--check` is also a **connected smoke check**: it requests
Telegram `getMe` and authenticates configured optional Live Sync. It is not an
offline configuration validator or a replacement for pytest. Use it only for an
explicitly intended connected test environment; see [setup](setup.md).

## Read a CI failure

The required `test` job aggregates `python-tests`, `miniapp-smoke`, `secret-scan`,
`dependency-audit` and `static-analysis`. Every dependency must succeed; missing,
skipped or cancelled work is not a pass. `python-tests` first requires all four
`python-test-shards`, validates their complete disjoint test inventory, then
combines raw coverage and applies both coverage policies. Each partial shard
uses a zero floor only because the combined gate owns whole-application coverage.

Open the failed dependency and its first failing step. Reproduce its command with
the same interpreter, locks and selected test. Inspect these artifacts when the
step output is insufficient:

| Failure | Artifact/evidence |
| --- | --- |
| Python assertion, teardown, warning or shard inventory | `pytest-shard-N`: log, JUnit XML, test-ID manifest and raw coverage; `application-coverage`: combined JSON/XML and shard reports |
| Browser assertion or fixture startup | `miniapp-smoke-chromium` / `miniapp-smoke-webkit`: logs, HTML report, failure traces/screenshots |
| Dependency scanner | `dependency-audit`: each scan's status and full report |
| Leak/reference scanner | `memory-leak-scan`: Markdown findings and reference JSON |
| Aggregate red with no new assertion | `test` job summary: every dependency result |

Artifacts are retained for 14 days; failed early steps may not produce all files.
A newer run cancels an older run for the same ref, so review evidence for the
exact current commit. Do not claim a cancelled run passed or merge using checks
from an earlier head.

The [workflow registry](../../.github/workflows/README.md) owns the complete
workflow inventory. PR title/size and weekly audit jobs are advisory; the manual
Python 3.15 compatibility workflow is experimental and does not replace the
Python 3.11 delivery gate. Record actual commands, selections, outcomes and
environment limitations in the PR using [workflow](workflow.md).
