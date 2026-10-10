# Python 3.14.8 migration — gated promotion

The previous Python 3.15 track (#487) was superseded by the
**Python 3.14.8** migration (#499). Production remains on
**Python 3.11.16** until the promotion criteria are met.

## Native dependencies and actual production CPU

The live VPS uses a KVM CPU exposing SSE2 but no x86-64-v2 features.
CPython 3.14.8 and CTranslate2 4.8.2/faster-whisper 1.2.1 are
installable on Linux x86-64. However, NumPy 2.4.6 imports fail
on this VPS with "RuntimeError ... X86_V2".

The separate config/python314-constraints.txt therefore pins
**NumPy 2.3.5** for compatibility with the existing CPU. With
this constraint, the isolated Python 3.14.8 hash-locked environment
successfully installed 42 runtime and 78 combined development
packages, preserving reviewed Python 3.11 transitive versions except NumPy. The dependency checker and native imports passed on the VPS.

Python interpreter provenance: Astral python-build-standalone,
GitHub release 20261001, CPython 3.14.8 standard x86-64 Linux
install_only artifact. Verified SHA-256:
813c89e2589fed92333e18bde230a43280962e6f24bb0b861dc8ce532cfd6567

The interpreter was placed under the normal user-managed Python
directory. No system Python or production virtual environment changed.

## Isolated reproducible installations

Use a **different** virtual environment to verify Python 3.14.
Do not recreate or alter the running Python 3.11 .venv.

    uv venv --python 3.14.8 .venv314-staging
    uv pip install --python .venv314-staging/bin/python --require-hashes -r requirements-dev-py314.lock
    uv pip check --python .venv314-staging/bin/python
    .venv314-staging/bin/python -c 'import numpy, ctranslate2, faster_whisper; assert numpy.__version__ == "2.3.5"'

The independent Python 3.14 locks are requirements-py314.lock
and requirements-dev-py314.lock. The Python 3.11 requirements.lock
and requirements-dev.lock are unchanged for rollback.

Reviewed regeneration procedure, which must not silently replace
the old locks:

    uv pip compile requirements.txt --python-version 3.14 --constraint config/python314-constraints.txt --generate-hashes --output-file requirements-py314.lock
    uv pip compile requirements.txt requirements-dev.txt --python-version 3.14 --constraint config/python314-constraints.txt --generate-hashes --output-file requirements-dev-py314.lock

## Validation and release gates

## Current blockers and diagnostic evidence

The original SQLite connection-lifecycle failures are repaired by
[#503](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/pull/503),
merged into this migration branch. The
[initial run](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/runs/38020696462)
(5,110 passed, 16 failed and one error) is historical pre-fix evidence,
not the current SQLite result.

At source `0700181f12063570f0babf9ebc26208d713a4b78`,
[ordinary CI passed](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/runs/38031945230).
The [strict Python 3.14 run](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/runs/38031945226)
reported **5,216 tests and 817 subtests passed**, with 84.39% coverage,
but correctly failed on a fatal native allocator error at shutdown.
Its separate published-wheel native probe exited **134**. Passing test
assertions do not establish a clean interpreter exit or completed security
coverage gates; the fatal-log guard stopped this run before those floors.

The native cause is established: upstream-pinned pybind11 2.11.1 allocated
heap-type `tp_doc` with `PyObject_MALLOC`, while Python 3.14 frees it with
`PyMem_Free`. Strict CPython 3.14.7/3.14.8 finalization exposes the mismatch
as `_PyMem_DebugRawFree: bad ID`. The same minimal binding exits cleanly
with pybind11 2.13.6 or 3.0.1. Upstream
[PR #2108](https://github.com/OpenNMT/CTranslate2/pull/2108) merged on
2026-10-10 and issue #2107 closed. The latest official release remains
**v4.8.2** at this checkpoint; the published wheel is still unfixed.
Do not disable the developer allocator or substitute plain imports for
strict clean-exit verification.

### Separate pinned-candidate CI for PR #500

`Python 3.14 pinned candidate for PR500` runs on GitHub-hosted Ubuntu 24.04
for updates to PR #500's same-repository migration branch. It checks out
the immutable PR event head and requires current PR/main refs to match
the event head/base both before and after testing. Upstream artifact
`11660198517`, its source identity, ZIP digest and exact regular cp314
wheel digest are verified before installation.

Only CTranslate2 is replaced inside a disposable Python 3.14.8 environment.
Strict native clean exit, serial native/audio tests, full regression,
original global/security coverage floors and fatal-log checks all gate
success. Candidate-specific evidence is retained for 14 days. This
experimental result does not waive the separate official published-wheel
promotion gate or change the production dependency locks.

### Completed isolated staging at source 0700181f

The separately approved unreleased CTranslate2 candidate wheel has SHA-256
`186f18a7204361767d5158d75f98d6b2a750d3fbebda44ca72432be6ceff9da6`.
It contains other upstream CPU/GPU changes in addition to the allocator
repair, and is not a fixed official release or an approved production lock.

On the actual old-CPU host, that exact candidate passed strict native
clean exit, three audio tests, and 83 selected continuity tests plus nine
subtests. A real local English transcription of synthetic audio matched
all ten reference words and exited cleanly under strict checks. The one
cold call took 129.50 seconds under a 25% CPU quota and reached 629.0 MiB
process peak RSS. This is one bounded speech smoke, not warm-repeat,
multilingual, production-latency or concurrent-load acceptance.

A disposable Python **3.11 → 3.14 → 3.11** backup/write/restore rehearsal
preserved all 75 tables and 280 schema objects, with integrity and
foreign-key checks passing. This does not establish production-sized
backup or live-service rollback readiness.

Three matched normal-mode offline bridge workload pairs verified 270
turns. Median paired mean CPU changed **−3.42%** and peak RSS **+2.95%**;
p95 results varied enough that latency acceptance remains inconclusive.
The 5% CPU-gain target is unproven. Earlier tiny synthetic SQLite results
remain historical; the later history-fetch wall-time changes were mixed,
so they do not isolate a consistent Python-specific SQLite slowdown.
Production promotion still requires the separate gates below.

To diagnose SQLite finalization warnings without slowing or contaminating
the entire suite, manually dispatch the Python 3.14 workflow with
`trace_resource_allocations=true` and `resource_trace_tests` set to
one to four existing `tests/test_*.py` paths. This optional mode runs
only the selected files serially under `PYTHONTRACEMALLOC=6`; the
full-regression job is explicitly skipped and diagnostics can fail.
It does not grant migration approval, nor relax resource warnings.
Regular pull requests still run the untraced full strict pytest suite.

The Python 3.14 compatibility GitHub Actions workflow performs a
**full voice-enabled dependency install**, not a transcription-disabled
core-only installation. Full pytest with native Python 3.14.8,
strict ResourceWarning handling, application coverage and
security-critical coverage are **real failing gates**: advisory green
summaries do not count as full-regression passes.

Before switching the live runtime, the following gates must pass:

1. Full Python 3.14 test suite with native transcription stack.
2. Offline staging acceptance for Telegram text, callbacks, choices,
   image, reset, SQLite migrations, session summaries and narrative
   causal continuity, Hindsight and provider fallbacks.
3. Matched same-host measurements of Python 3.11 vs 3.14.8 CPU,
   process RSS, p50/p95 Telegram end-to-end latency and model wait.
   Initial targets: local CPU improvement >=5%, no >5% p95 or RSS
   regression, and no story-quality regression.
4. Independent human narrative-quality review using frozen fixtures.
5. Verified backup, independent 3.14 virtual environment, a canary
   run, and tested immediate rollback to 3.11.

## Production deployment boundary

The live bridge uses signed source under
~/.local/share/sillytavern-telegram/source, with the
90-v0320-signed-source.conf systemd user override. It currently
runs Python 3.11 from the independent development checkout .venv.
Preserve that executable and override for rollback.

Do not restart the live service, replace the virtual environment,
or mutate story databases until all gates pass. The eventual switch
must use a separate 3.14 environment with a reversible systemd
override, and leave the 3.11 environment intact. The development
checkout contains unrelated uncommitted changes and must not be
force-reset or deployed as a substitute for signed source.

This stage is a migration candidate, not production activation.

### Isolated debugger workflow (Issue #501)

Manual `python314-compatibility.yml` with `native_debug_backtrace=true`
selects only the GitHub-hosted `native-debugger` job. It installs reviewed
CPython 3.14.8 dependencies, GDB in that disposable runner, and reproduces
the native extension finalization abort under `-X dev`, retaining its
native stack trace for 14 days. This diagnostic is intentionally **not**
a passing runtime gate or an authorization to deploy. It does not run on
normal pull requests and never accesses the live VPS or story data.

### Experimental pybind11-rebuilt binding

An isolated `native-debugger` trial also recompiles **only** the CTranslate2
v4.8.2 Python extension at verified upstream commit
`d44d2d069eb88c7b7804da864c10c201501cb4a9` using pybind11 2.13.6
instead of the upstream-pinned 2.11.1. It links against the *reviewed
official CTranslate2 core library* obtained from the existing hash-locked
wheel. This tests the actual fix to `tp_doc` allocator ownership
without shipping a private unreviewed native wheel or switching runtime.
Any successful diagnostic is not an authorization to deploy; wait for a
verified upstream build/release and the full resource/quality/performance
gates. See CTranslate2 upstream issue #2107.
