# Python 3.15 compatibility and performance gate

Status: **experimental only**. Production, the installer, the CI merge gate, Ruff and mypy
remain on Python 3.11. This procedure never replaces the production virtual environment,
restarts a bridge, or makes a deployment decision from a microbenchmark alone.

## Why this is staged

The bridge is substantially network- and provider-I/O-bound. CPython 3.15's experimental
JIT gains on generic interpreter benchmarks do not imply proportional improvements in
Telegram or provider latency. The JIT is not part of every Python build; it must be
built with JIT support and reported as enabled before comparing JIT timings.

On 2026-10-09, a wheel-only resolution of the current `requirements.txt` for
Linux x86-64 / Python 3.15 failed because PyYAML had no usable wheel. Other native
dependency wheels (for example CTranslate2, PyAV, NumPy, tokenizers) also need
verification. A resolver result is not proof of runtime or service compatibility.

## Stage 1: optional compatibility workflow

The separate `python315-compat.yml` workflow is intentionally non-blocking.
It checks syntax, resolves separate SHA-256-hashed *Python 3.15* runtime and
development locks using binary distributions only, checks imports and the entire
test suite, then runs the offline synthetic CPU/RSS benchmark if resolution succeeds.
It does **not** change `requirements.lock` or `requirements-dev.lock` for 3.11.
Inspect the job logs and artifact: a non-blocking check is **not** a passing gate
when setup, resolution, installation, tests or benchmarking fail.

If a needed wheel is unavailable, leave Python 3.11 as the live default.
Do not unpin packages, change production locks, or install unreviewed native
source builds just to produce a green compatibility check. A source-build
exception requires explicit review and an appropriately tested release build.

## Stage 2: matched offline benchmark

Use one *idle* Linux x86-64 host and one code revision, with separate environments.
Run the normal validated Python 3.11 environment first. Only run Python 3.15 after
wheels, hashes, imports and test suite pass under that interpreter.

```sh
# In a clean staging checkout (never the live bridge environment).
uv venv --python 3.11 /tmp/sttb-benchmark-py311
uv pip install --python /tmp/sttb-benchmark-py311/bin/python --require-hashes -r requirements-dev.lock
uv pip check --python /tmp/sttb-benchmark-py311/bin/python
/tmp/sttb-benchmark-py311/bin/python tools/benchmark_python_runtime.py \
  --iterations 300 --warmup 40 --output /tmp/sttb-benchmark-311.json

# Only after a successful Python 3.15 wheel resolution and full test run:
uv pip compile requirements.txt --python-version 3.15 --only-binary :all: \
  --generate-hashes --output-file /tmp/sttb-runtime315.lock
uv pip compile requirements.txt requirements-dev.txt --constraint /tmp/sttb-runtime315.lock \
  --python-version 3.15 --only-binary :all: --generate-hashes \
  --output-file /tmp/sttb-dev315.lock
uv venv --python 3.15 /tmp/sttb-benchmark-py315
uv pip install --python /tmp/sttb-benchmark-py315/bin/python \
  --only-binary :all: --require-hashes -r /tmp/sttb-dev315.lock
uv pip check --python /tmp/sttb-benchmark-py315/bin/python
/tmp/sttb-benchmark-py315/bin/python -m pytest -q
/tmp/sttb-benchmark-py315/bin/python tools/benchmark_python_runtime.py \
  --iterations 300 --warmup 40 --output /tmp/sttb-benchmark-315.json
/tmp/sttb-benchmark-py311/bin/python tools/compare_python_runtime.py \
  /tmp/sttb-benchmark-311.json /tmp/sttb-benchmark-315.json
```

The benchmark uses synthetic character/card/history inputs through the real prompt
assembly/compaction and Telegram formatting code. It measures isolated wall time,
CPU time and process peak RSS; it makes zero network/provider calls, touches no
production data, and never exports prompts. Repeat three matched, interleaved
A/B runs on the same host to reduce thermal, load and cache confounders.
A comparison result is a **local CPU candidate gate only**, never production approval.

For a JIT comparison, independently provision a reviewed Python 3.15 JIT build
and confirm `jit.available=true` and `jit.enabled=true` in the benchmark
metadata. Do not claim a JIT benchmark from ordinary Python 3.15.

## Stage 3: mandatory production promotion gates

1. Separate Python 3.15 dependency locks resolve, verified hash-pinned wheels
   install on the target architecture, and all packages pass import/smoke checks.
2. All Python tests, native story continuity checks, memory/tracker/database,
   Telegram formatting, image and Hindsight integration acceptance pass.
3. Three matched A/B runs with identical workload/revision show at least 5%
   improvement in p95 prompt-building CPU time, no greater than 5% regression
   in other CPU p95 paths, and at most 10% increase in peak RSS. Verify output
   fingerprints are identical. Do not trade away narrative or causal continuity.
4. Separately collect real provider/Telegram p50/p95 end-to-end latency and
   time-to-first-token under matched provider/model/network conditions,
   including auxiliary requests, fallback and error rates. CPU gains alone are
   insufficient; require a meaningful *observed* end-to-end benefit.
5. Deploy an isolated staging service with an independent venv and data copy,
   then limited canary. Preserve the previous 3.11 venv and configuration so
   rollback requires switching the service back, not rebuilding in place.
   Document canary health, memory trend and successful rollback first.

**No production deployment** is authorized until every gate is satisfied.
