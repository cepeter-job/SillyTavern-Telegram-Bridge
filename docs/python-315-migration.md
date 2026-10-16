# Python 3.15 migration — experimental validation

**Production remains on Python 3.11.** The existing installer, main CI checks,
release process, service, and Python 3.11 hash-locked dependencies remain unchanged.

## Compatibility checkpoint: October 9, 2026

The current CI, installer, pyproject.toml and hashed dependency locks target
Python 3.11. Python 3.15.0 stable was released October 9, 2026.

In a temporary Linux x86-64 clone, uv installed CPython **3.15.0rc2**.
Installing the existing full requirements.lock failed: the pinned
**ctranslate2==4.8.2** does not offer a CPython cp315 wheel.
ctranslate2 is a native dependency of faster-whisper, so voice-transcription
support cannot be declared compatible. This is a **production blocker**.

Core-only dependency resolution (omitting faster-whisper) can identify more
independent issues, but it is **not** a supported installation and must never
be used to deploy the live bridge.

## Nonblocking CI experiment

The [experimental workflow](../.github/workflows/python315-experimental.yml)
runs only by manual dispatch while Python 3.14 is the active migration focus;
it no longer runs on pull requests. It does not participate in required
Python 3.11 CI or alter existing lockfiles.

1. Resolve a fresh, hashed full Python 3.15 lock into runner temporary storage;
   install all packages and import critical integrations. Failure is retained
   as evidence. Do not silently downgrade native packages.
2. Independently resolve temporary 3.11 and 3.15 *core-only* dependency locks
   and run synthetic/offline context selection, compaction and benchmark tests.
3. Compare interpreter work for context-token estimation, provenance-preserving
   memory deduplication, JSON serialization, and SQLite context lookup.
   The benchmark uses invented text, no provider calls, and no production data.
4. Store exact interpreter version, JIT status, median CPU/wall time, batch p95,
   and process peak resident memory as short-lived CI artifacts.

These timings **do not measure** Telegram response latency, AI provider
generation, story quality, or token savings. The CPython JIT is experimental
and requires a separate JIT-enabled build and comparison.

To run the offline benchmark in a disposable environment where dependencies
are installed:

    python tools/benchmark_python_runtime.py --samples 15 --iterations 100

## Manual stable-release regression gate (Issue #487)

The experimental workflow now includes a manually dispatched
`stable-final-core-regression` job (it does not run on every PR). This job:

1. Downloads the **official Python 3.15.0 final source tarball**, checks
   its SHA-256 against the value published on python.org, and builds it into
   the temporary GitHub runner directory (never on the live VPS).
2. Resolves and installs a **temporary, hash-locked** core + development
   dependency set for 3.15 with `faster-whisper` explicitly excluded
   because `ctranslate2` currently lacks a compatible wheel.
3. Runs full pytest discovery with up to four isolated workers and records
   JUnit, coverage and captured output. If tests fail, the job displays a
   warning; the failure is not an authorization to drop regression tests.
4. Retains results for 14 days. These are **provisional core-only results**
   and do NOT establish the full voice transcription and native-extension gate.

### Locating remaining SQLite resource owners

The core-only Python 3.15 final run can report a leaked SQLite connection
*during a different test* from the one that created it, since finalizers run
when the garbage collector executes. Do not assume the failing test owns the
connection merely because it received the warning.

For a focused diagnostic run, manually dispatch **Python 3.15 experimental
compatibility** with the optional `trace_resource_allocations` switch enabled
and `resource_trace_tests` set to one to four existing `tests/test_*.py`
modules separated by spaces (default: `tests/test_expressions.py`). This
runs **only those tests**, serially (`-n 0`), with `PYTHONTRACEMALLOC=8`;
subprocess-heavy unrelated tests and the full suite are not traced. The
strict `ResourceWarning` checks remain active. Inspect the resulting
`python315-final-core-regression` artifact for allocation stacks and
fix resource owners, not the tests in which garbage collection happens.

**Diagnostic success is not a Python 3.15 regression pass.** With the
trace switch disabled, the separate untraced full pytest suite still runs
with its original xdist coverage and warning gates. The trace run has
additional memory and CPU overhead, so compare its findings with an
untraced final-runtime regression; never compare its speed with production.
In [run 37972376367](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/runs/37972376367),
tracing the entire suite caused many subprocess timeouts and took about
38 minutes; this is why the diagnostic mode is now explicitly targeted.

This locally compiled release is not a like-for-like PGO/JIT performance
build. Do not compare its benchmark timings with production or interpret its
test success as better end-to-end latency. The source fingerprint used is
`ba4bed1ba346b916890b76d9e320451420aa69f6408997d33c66482eeae3d575`
for the official 3.15.0 XZ archive.

See [promotion tracking issue #487](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/issues/487)
for the native wheel blocker and remaining acceptance gates.

## Promotion criteria and rollback

- [ ] Full runtime and test dependencies install from reviewed, verifiable
      hash-locked resolutions on stable Python 3.15, including faster-whisper
      and ctranslate2 native extensions.
- [ ] Complete suite, CI type/security/lint checks and Telegram integration
      tests pass under 3.15 without weakening Python 3.11 coverage.
- [ ] Hindsight, NanoGPT/OpenAI, voice and image paths, SQLite migrations,
      director/miniapp, reset and narrative causal continuity validated.
- [ ] Matched workloads on the same hardware show meaningful local CPU benefit
      (initial target at least 5%), no more than 5% RSS or end-to-end p95
      latency regression, and no functional or story-quality regression.
      Offline microbenchmarks alone do not satisfy this gate.
- [ ] A separate production virtual environment, backup and canary are ready.
      Keep Python 3.11 as an immediate rollback option.

**Decision:** Do not change installer defaults, deploy Python 3.15, or restart
the live service until every promotion criterion has evidence.
