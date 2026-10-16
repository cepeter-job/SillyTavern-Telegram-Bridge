# Workflow and regression audit completion — 2026-10-16

## Scope and decision

This completes the remaining audit implementation after [#539](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/pull/539):
expensive evaluator setup, the historical filename guard, DOM fixture waiting,
and the next scheduling decision. [#540](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/pull/540) owns this change.
The runtime, Python/dependency locks, coverage floors, protected job names, and
executable workflow are unchanged. Python migration and context-quality research
remain separately gated workstreams; this report does not approve their promotion.
No production service, credentials, release key, release tag or deployment changed.

Keep the existing four disjoint whole-file shards and combined coverage gate.
Do not add workers, duration-hint configuration or another benchmark workflow.
The comparison evaluates the fixture change with the existing allocator; it does
not compare a new allocation algorithm or establish an optimum shard count.
The earlier duration-hint experiment remains historical, not new performance proof.

## Changes and retained contracts

Context observation tests share only a JSON string produced once by the real CLI.
Each test deserializes its own copy; every validator still runs its real CLI with
a fresh corpus. Generated-answer rejection matrices execute the real parser,
main, corpus, loopback transport and report in-process with function-scoped
environment restoration. Default and full-wire subprocess tests remain separate.
Blob-specific tests select the blob backend; the full default and combined wire
studies retain all 42 facts, 24 queries, replay and fresh-attestation assertions.

The metadata guard no longer reserves 16 historical test filenames. Its seven
specific helper-owner restrictions remain. ASCII sources without relevant module
names avoid unnecessary parsing; every non-ASCII source is parsed because Python
normalizes identifiers. Comments and strings do not become false import findings.

Only the DOM smoke child explicitly enables `MINIAPP_FIXTURE_RATE_CLOCK=1`.
A test-support context replaces the HTTP adapter's module reference to its clock,
not stdlib time or the process scheduler. Production authentication, the actual
120-request threshold and request history remain active. A random owner key
protects an opt-in, loopback-fixture-only window-advance route. Default fixtures
omit the route and key. ExitStack restores the module reference after cleanup.
Real Chromium/WebKit fixtures keep real clocks. Controlled-clock smoke timing is
not evidence about production request latency or production rate-window timing.

Two HTTP tests prove unauthenticated rejection, 120 accepted requests then 429,
failed clock-control authentication without reset, authorized 61-second expiry,
a second full rate window and default absence of the control endpoint. The DOM
smoke retains its original session, recovery and mutation assertions, and logs
that controlled window expiry was actually exercised.

## Runner comparison

Baseline source: `f85787f09173fbcbf953d9a735bf60dcdb1cdc3b`.
Candidate implementation: `1287890ddb844e09ab27e3b8eb1647df8d74426f`.
Three attempts were retained per exact source revision. Workflows, dependencies,
Python selectors, browser images and shard algorithm are identical. The baseline
is a push run; the candidate is a PR run. GitHub-hosted machines, cache conditions
and queue delays are not controlled. These six observations are not a randomized
experiment, confidence interval, service-latency benchmark or promised speedup.

Job span is earliest job start to latest job completion for that attempt, rather
than original run creation (which would distort reruns). Runner-seconds sum all
job durations, including setup; they are not CPU time or a billing estimate.
File figures sum JUnit testcase durations and can overlap across workers. Fixture
setup may be charged to the first consuming case. Do not add them to job wall time.

| Metric | Baseline median | Candidate median | Change |
| --- | ---: | ---: | ---: |
| CI job span, seconds | 330.00 | 299.00 | -9.4% |
| Summed runner-seconds | 1057.00 | 978.00 | -7.5% |
| Chromium job, seconds | 207.00 | 92.00 | -55.6% |
| WebKit job, seconds | 68.00 | 73.00 | +7.4% |
| DOM unit/integration step, seconds | 138.00 | 16.00 | -88.4% |
| Context-efficiency file: summed testcase seconds | 115.98 | 141.84 | +22.3% |
| Generated-answer file: summed testcase seconds | 101.66 | 48.88 | -51.9% |
| Retrieval CLI file: summed testcase seconds | 159.01 | 136.26 | -14.3% |
| CI-gate file: summed testcase seconds | 8.17 | 4.80 | -41.2% |

The deterministic saving is removal of real-minute fixture waits, not a change to
production throttling. Per-file and whole-pipeline measurements are reported even
where they do not improve. Reused setup does not guarantee a faster individual
hosted run; the current allocator still has indivisible expensive files.

| Source | Run / attempt | Job span (s) | Runner-seconds | Coverage artifact |
| --- | --- | ---: | ---: | --- |
| Baseline | [38857342990 / 1](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/runs/38857342990/attempts/1) | 345 | 1080 | `12027603468` |
| Baseline | [38857342990 / 2](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/runs/38857342990/attempts/2) | 330 | 1057 | `12028471257` |
| Baseline | [38857342990 / 3](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/runs/38857342990/attempts/3) | 256 | 1050 | `12027834413` |
| Candidate | [38859751494 / 1](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/runs/38859751494/attempts/1) | 311 | 978 | `12029211251` |
| Candidate | [38859751494 / 2](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/runs/38859751494/attempts/2) | 299 | 1032 | `12028119385` |
| Candidate | [38859751494 / 3](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/runs/38859751494/attempts/3) | 250 | 842 | `12028294546` |

Each coverage archive was downloaded by exact artifact ID and its SHA-256 checked
against the GitHub artifact metadata before reading manifests, JUnit, logs and
coverage JSON:

- Run `38857342990`, attempt 1, artifact `12027603468`: `sha256:efe00369e85a26170c05b338b26945bdb3383e319cad128dff27562033c03023`.
- Run `38857342990`, attempt 2, artifact `12028471257`: `sha256:adaf065c67e51243c18ac62ecf558d4afa330dca4ea82be4f32e26e8fd4f9b61`.
- Run `38857342990`, attempt 3, artifact `12027834413`: `sha256:7c00ddd023e7465bb7f78f87940b5f591c649c1a83d611e64188e282ecef64ef`.
- Run `38859751494`, attempt 1, artifact `12029211251`: `sha256:c99bad597e2736018ae348fce748909f7242cb26426fc2364b10c53304dd238c`.
- Run `38859751494`, attempt 2, artifact `12028119385`: `sha256:474a9da8cf60c4ee66d87354e3b7b216a2ef8ceeff3c8737a6921a69250ede93`.
- Run `38859751494`, attempt 3, artifact `12028294546`: `sha256:8eccd521844dbd48a41da30db1144c446ff34d6f6bad3b73a783e2810380a97c`.

## Execution and collection evidence

All three candidate attempts passed **5,714 pytest cases and 851 subtests**.
JUnit records 6,565 entries with zero failures, errors or skips. Combined
statement/branch coverage was 84.87%, 84.86%, 84.88%; the unchanged 76% floor and
security-critical coverage checks passed. All four shard results, complete
collection union, readable coverage data, Chromium/WebKit checks, strict dependency
audit, static analysis and secret scan passed. The required CodeQL result and
analysis checks must also be current at the final reviewed head before merge.

Exact inventory comparison against baseline shows one renamed metadata test and
two added fixture-clock tests. No original behavioral case is removed, no other
case is added, and there are no duplicated IDs. All 851 subtests remain.

- Rename `test_ci_gate.py::test_reviewed_test_consolidation_does_not_fragment_again`
  to `test_shared_support_helpers_are_not_imported_from_feature_tests`.
- Add `test_miniapp_browser_recovery_fixture.py::test_fixture_clock_preserves_auth_rate_limit_and_next_window_recovery`.
- Add `test_miniapp_browser_recovery_fixture.py::test_fixture_rate_clock_control_requires_explicit_opt_in`.

The expected RED was observed on `bf8bb09` in run `38859163909`, job
`116837529656`: the new clock-contract test failed with
`Opt-in fixture clock control is missing` before implementation. That superseded
run is not counted as success or included in the performance comparison.

## Review and integration boundary

Independent automated source review approved the immutable evaluator setup and
in-process rejection harness. Another review found a Unicode prefilter regression;
it was reproduced with stdlib AST and corrected before final validation. Follow-up
review checked 77 import variants and retained all seven helper restrictions.

The clock review initially reported a possible non-JSON 403/404 parsing failure.
That finding was withdrawn after inspecting the actual application middleware,
which converts HTTP exceptions to JSON, and reconciling the successful real HTTP
fixture tests. Its remaining source review confirmed opt-in, owner authentication,
clock isolation, default-route absence and cleanup. These are automated source
reviews, not independent human narrative-quality or production acceptance.

Only source/static checks ran locally. All product, pytest, HTTP/browser and
coverage execution ran on GitHub-hosted runners. Final documentation changes do
not alter the benchmarked Python/JavaScript tree. The exact final-head checks,
squash commit and successful post-merge main run are recorded in PR #540 rather
than written prematurely into this pre-merge document.
