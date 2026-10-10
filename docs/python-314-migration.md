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
