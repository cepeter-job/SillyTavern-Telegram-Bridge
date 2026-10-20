# Python 3.14.8 migration — gated promotion

The previous Python 3.15 track (#487) was superseded by the
**Python 3.14.8** migration (#499). Production remains on
**Python 3.11.16** until the promotion criteria are met.

## Native dependencies and actual production CPU

The live VPS uses a KVM CPU that exposes SSE2 but lacks x86-64-v2 support.
CPython 3.14.8 and CTranslate2 4.8.3/faster-whisper 1.2.1 are
installable on Linux x86-64. However, NumPy 2.4.6 imports fail
on this VPS with "RuntimeError ... X86_V2".

The separate config/python314-constraints.txt therefore pins
**NumPy 2.3.5** for compatibility with the existing CPU. With
this constraint, the isolated Python 3.14.8 hash-locked environment
initially installed 42 runtime and 78 combined development packages.
Those older import results predate the official allocator repair and do not
establish strict clean exit for the current release.

### Official release refresh: October 15, 2026

The migration locks now follow reviewed `main` at
`a612a6b4a1e7ac8dfa4c403a75377a52d9d0d37b`: aiohttp **3.14.4**,
yarl **1.25.1**, uv **0.12.23**, and NumPy **2.3.5** (also the
Python 3.11 baseline). Only the separate Python 3.14 CTranslate2 pin
advances from **4.8.2** to **4.8.3**. The Python 3.11 manifests and
locks remain byte-for-byte identical to that main revision.

Official [v4.8.3](https://github.com/OpenNMT/CTranslate2/releases/tag/v4.8.3),
published October 13, includes the pybind11 2.13.6 allocator repair from
[upstream #2108](https://github.com/OpenNMT/CTranslate2/pull/2108).
The regular CPython 3.14 Linux x86-64 wheel is
`ctranslate2-4.8.3-cp314-cp314-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl`.
Its SHA-256, checked against [official PyPI metadata](https://pypi.org/pypi/ctranslate2/4.8.3/json)
and independently downloaded bytes before execution, is:

`7039b9b9f0520a891108b795c7bd960413cd54df9db319f9afc4c164d28336dc`

Use the official hash-locked environment for current compatibility evidence.
The pinned unreleased candidate and old debugger results below remain historical
evidence; neither substitutes for the official-wheel full regression gate.

A new disposable Python **3.14.8 final** environment installed all **42** runtime
and **78** combined development packages with mandatory hashes and passed
`uv pip check`. On the actual old CPU, the combined CTranslate2/faster-whisper,
NumPy, AV, ONNX Runtime and bridge dependency imports exited **0** under
`-X dev -W error::ResourceWarning`. The loaded extension was inside the new
environment and its bytes matched the verified official wheel. Native threads
were limited to one and the process ran at nice 19. An ONNX Runtime telemetry-ID
persistence warning was retained unsuppressed. This establishes import and shutdown
compatibility only; performance, full regression and promotion gates remain open.

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
    uv pip install --python .venv314-staging/bin/python --require-hashes -r requirements-py314.lock
    uv pip install --python .venv314-staging/bin/python --require-hashes -r requirements-dev-py314.lock
    uv pip check --python .venv314-staging/bin/python
    PYTHONDEVMODE=1 PYTHONWARNINGS=error::ResourceWarning \
      OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
      .venv314-staging/bin/python -X dev -W error::ResourceWarning - <<'PY'
    import sys
    from pathlib import Path
    import numpy, ctranslate2, ctranslate2._ext as ext, faster_whisper, av, onnxruntime
    assert sys.version_info[:3] == (3, 14, 8) and sys.version_info.releaselevel == "final"
    assert numpy.__version__ == "2.3.5"
    assert ctranslate2.__version__ == "4.8.3"
    assert Path(ext.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    print(ext.__file__)
    PY

The independent Python 3.14 locks are requirements-py314.lock
and requirements-dev-py314.lock. The Python 3.11 requirements.lock
and requirements-dev.lock remain the separate Python 3.11 rollback baseline.

Use uv **0.13.0** for the reviewed regeneration procedure. Reconcile
constraints with the current reviewed baseline first; the combined development
resolution also uses the exact Python 3.14 runtime lock:

    uv pip compile requirements.txt --python-version 3.14 --constraint config/python314-constraints.txt --generate-hashes --output-file requirements-py314.lock
    uv pip compile requirements.txt requirements-dev.txt --python-version 3.14 --constraint config/python314-constraints.txt --constraint requirements-py314.lock --generate-hashes --output-file requirements-dev-py314.lock

## Validation and release gates

## Historical failures and remaining acceptance gates

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
2026-10-10 and issue #2107 closed. At that historical checkpoint,
the latest official release was **v4.8.2**, whose published wheel was unfixed.
Official **v4.8.3** now includes the repair; its current full regression
and remaining promotion criteria still require their own evidence.
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

This retained manual diagnostic was designed for the **4.8.2** allocator
failure. The current official locks install **4.8.3**, so its expected-abort
check no longer describes the release being validated. Do not dispatch it or
the custom binding rebuild as part of official-release acceptance. A future
debugger experiment needs its own reviewed historical inputs.

Manual `python314-compatibility.yml` with `native_debug_backtrace=true`
selects only the GitHub-hosted `native-debugger` job. It was designed to
install reviewed CPython 3.14.8 dependencies and GDB in a disposable runner,
reproduce the native extension finalization abort under `-X dev`, and retain
its stack trace for 14 days. This diagnostic is intentionally **not** a
passing runtime gate or an authorization to deploy. It does not run on
normal pull requests or access the live VPS or story data.

### Experimental pybind11-rebuilt binding

An isolated `native-debugger` trial also recompiled **only** the CTranslate2
v4.8.2 Python extension at verified upstream commit
`d44d2d069eb88c7b7804da864c10c201501cb4a9` using pybind11 2.13.6
instead of the upstream-pinned 2.11.1. It historically linked against the *reviewed
official CTranslate2 4.8.2 core library* from the then-current hash-locked
wheel. That experiment tested the fix to `tp_doc` allocator ownership
without shipping a private unreviewed native wheel or switching runtime.
Any successful diagnostic is not an authorization to deploy. The upstream
release is now available; full resource/quality/performance gates remain.
See CTranslate2 upstream issue #2107.
