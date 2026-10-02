# Repository audit — maintainability and technical debt

**Date:** 2026-10-01
**Branch audited:** `arena/01a0f82a-sillytavern-telegram-bridge` (from `main` @ `f687e3b`)
**Focus:** maintainability and debt — dead code, duplication, module sprawl, docs/test drift, CI gaps
**Scope:** whole repository, excluding `.git` history (the clone is a single squashed commit, so
churn/hotspot analysis was not possible)

This audit is a point-in-time review. It is not a security assessment; see [SECURITY.md](SECURITY.md)
for the disclosure process. Security-relevant observations are only noted where they surfaced as
maintainability problems.

---

## 1. Verdict

The repository is in unusually good structural health for its size. Every gate the project defines
for itself passes, and the self-imposed governance tests are real (they fail when their invariants
break, not merely assert on files existing).

| Gate | Result |
|---|---|
| `ruff check .` | clean |
| `ruff format --check .` | clean (479 files) |
| `tools/static_analysis.py` | 261 modules, 1402 edges, **0 cycles**, 0 reciprocal pairs, 0 violations |
| `tools/check_dependency_lock.py` | ok |
| `mypy` (typed surface, 99 files) | clean |
| `pytest -n 4` | **2397 passed, 778 subtests passed, 0 skipped, 0 xfailed** |
| Coverage floor (`fail_under = 76`) | **79.02 %** combined statement+branch |
| `tools/check_security_coverage.py` | all 4 floors met |
| `git diff --check` | clean |

Notable structural results:

- **No dead modules.** Every `bridge/` module except the three legitimate entry points
  (`main.py`, `pdf_parser.py` subprocess worker, `tailscale_funnel.py` installer CLI) has inbound
  importers. `vulture` reports one unused name, and it is a false positive (see §5).
- **No import cycles and no reciprocal pairs** across 1 402 dependency edges — the architecture
  policy is not just documented, it holds.
- **Zero** `TODO` / `FIXME` / `HACK` / `XXX` / `DEPRECATED` markers in `bridge/`, `tests/`, `docs/`
  or `README.md`.
- **0 bare `except:`** clauses; 98 % of modules carry a module docstring.
- The `/help` catalog is exactly in sync with the Telegram command menu (40 commands, no drift in
  either direction), and `help_details.json` drilldown keys are a strict superset.

The debt that does exist is concentrated in **duplication** and **dispatcher size**, documented
below as M1–M4.

---

## 2. Metrics at a glance

| Metric | Value |
|---|---|
| `bridge/` modules | 261 (43 394 LOC) |
| `tests/` files | 200 (52 904 LOC) — 1.22× the production tree |
| Functions in `bridge/` | 1 607 (49 over 100 lines, 104 over 60) |
| Cyclomatic complexity | 1 710 blocks; 68 blocks ≥ 20, 117 blocks ≥ 15 |
| Maintainability index (radon) | 248 × A, 9 × B, 4 × C |
| Docstrings | modules 255/261 (98 %), classes 24/156 (15 %), functions 204/1 607 (13 %) |
| Duplicated function bodies (`bridge/`) | 27 groups covering 62 functions |
| Duplicated function bodies (`tests/`) | 49 groups covering 142 functions |
| mypy typed surface | 99 of 261 modules (38 %) |
| Suppressions in `bridge/` | 46 `# noqa`, 0 `# type: ignore` |

---

## 3. Findings

### M1 — `feature_panels.py` duplicates three live panel senders (Medium)

`bridge/feature_panels.py` is 72 lines and defines four functions. Three of them are near-identical
clones of functions that live in domain modules, and **all six are live**:

| `feature_panels.py` | Duplicate of | Both reached from |
|---|---|---|
| `send_scene_menu` (L15) | `scene_state.py:334` | `feature_callbacks.py:339,360` vs `scene_state.py:381` |
| `send_director_goal_menu` (L28) | `director_goals.py:89` | `feature_callbacks.py:380` vs `director_goals.py:134` |
| `send_curated_memory_menu` (L41) | `memory_curator.py:380` | `feature_callbacks.py:406` vs `memory_curator.py:423` |

Only `send_summary_menu` is unique to this module.

The clones are not byte-identical, and that is the problem: the domain-module versions send via
`delivery_port.send_panel_request(...)`, while the `feature_panels` versions go through the
`cards.send_panel_message(...)` wrapper. Today both bottom out in the same `telegram.py`
implementation, so behaviour agrees — but every future change to panel delivery now has two
entry points to keep in step, with no test asserting they stay equivalent.

**Recommendation:** pick one owner per panel. Either have `feature_callbacks` call the domain
versions directly and delete `feature_panels.py` (moving `send_summary_menu` to a memory/summary
owner), or invert it — make `feature_panels` the single owner and have the domain command handlers
call into it. Add a test that fails if a second sender for the same panel appears.

### M2 — Callback dispatchers are very large `if/elif` chains (Medium)

Twelve dispatcher functions total **3 314 lines** and **cumulative cyclomatic complexity 564**:

| Module | Function | Lines | CC |
|---|---|---:|---:|
| `character_callbacks.py` | `handle_character_callback` | 514 | 85 |
| `enum_callbacks.py` | `handle_enum_callback` | 271 | 77 |
| `provider_transport.py` | `generate_provider_text` | 309 | 70 |
| `update_message_routing.py` | `route_message_update` | 447 | 54 |
| `provider_callbacks.py` | `handle_provider_model_callback` | 319 | 45 |
| `feature_callbacks.py` | `handle_feature_panel_callback` | 288 | 42 |
| `persona_callbacks.py` | `handle_persona_callback` | 179 | 36 |
| `conversation_callbacks.py` | `handle_greeting_callback` | 146 | 35 |
| `callback_dispatch.py` | `process_callback` | 171 | 32 |
| `session_callbacks.py` | `handle_session_callback` | 174 | 28 |
| `npc_callbacks.py` | `handle_npc_callback` | 175 | 26 |
| `world_callbacks.py` | `handle_world_callback` | 132 | 22 |

These are flat `if data == "...": … return` chains. `handle_character_callback` alone has 17
top-level branches and is the single largest function in the codebase. `handle_enum_callback`
repeats the same three-line block (`discard_panel_binding` → `close_panel_message` → `return`) for
`enum:close`, `enum:stscript:cancel` and `enum:stscript:reset`.

**Impact:** a dispatcher this large cannot be reviewed as a unit, and "which callbacks does this
module own?" is only answerable by reading all 500 lines. It also explains the low coverage on
these modules (see M13) — the tail branches are hard to reach from tests.

**Recommendation:** convert each chain to an explicit routing table (`dict[str, Callable]`)
registered at import time, mirroring the pattern already used successfully by
`extension_registry.register_command_route`. Do the highest-CC one first; the others can follow
incrementally. Note that `panel_callback_routes.py` is already documented as "ordered dispatch
only" — extending that idea is in keeping with the existing architecture.

### M3 — The extension modules are copy-paste clones of each other (Medium)

`memory_curator.py` and `scene_state.py` contain at least four function bodies that are identical
apart from a constant or prefix:

- `_curator_source_rows` (L142) ≡ `_scene_state_source_rows` (L115) — 17 lines, byte-identical
  except `_MEMORY_CURATOR_TRANSCRIPT_MESSAGES` vs `_SCENE_STATE_TRANSCRIPT_MESSAGES`
- `_memory_curator_post_retain` (L358) ≡ `_scene_state_post_retain` (L291)
- `_memory_curator_command_route` (L460) ≡ `_scene_state_command_route` (L419)
- `normalize_director_goal` (`director_goals.py:30`) ≡ `_clean_curated_text` (`memory_curator.py:56`)

`director_goals.send_director_goal_menu` and `memory_curator.send_curated_memory_menu` are further
duplicated a third time in `feature_panels.py` (see M1).

**Impact:** these four modules (`director_goals`, `memory_curator`, `scene_state`, `npc_extraction`)
are registered through the same extension API and share the same lifecycle. A fix to the transcript
source-row query — for example a bound, ordering or injection-hardening change — must currently be
applied in at least two places, and nothing catches it if only one is updated.

**Recommendation:** extract the shared "read the last N transcript rows" query into a single owner
(`transcript_repository.py` already exists and is the natural home) and parameterise the
extension-registration lifecycle. Keep the domain-specific prompt/format logic in each module.

### M4 — `tests/test_pdf_worker.py` bundles three unrelated concerns (Medium)

`PdfWorkerTests.test_pdf_extraction_uses_bounded_worker` (68 lines) asserts on:

1. PDF extraction through the isolated worker (the stated purpose of the file),
2. per-session response-language persistence (`language.set_response_language` →
   `memory_curator.load_session(...)["response_language"]`),
3. new-session defaults (`create_session(...)["response_language"] == "auto"`).

Items 2 and 3 have nothing to do with PDF parsing, and near-identical coverage already exists in
`tests/test_language_runtime_boundary.py::test_set_response_language_uses_injected_session_update`.

**Impact:** a regression in session-language handling surfaces as a "PDF worker" failure, which
misdirects triage; conversely a PDF-regression failure is ambiguous. It also inflates the apparent
coverage of `pdf_parser.py`'s parent without testing the parser's own failure modes.

**Recommendation:** delete items 2 and 3 (they are already covered in the language boundary test),
leaving a focused PDF test, and add separate cases for the worker's byte-limit, page-limit,
character-limit and timeout paths.

*Not auto-fixed:* removing assertions from a passing test is a maintainer judgement call, so this
is raised as an issue rather than changed directly.

### M5 — Configuration governance guard had drifted (Low) — **FIXED**

`SILLYTAVERN_MEMORY_DIAGNOSTICS` (read at `bridge/memory_diagnostics.py:100`) was documented in
three docs (`configuration.md`, `operations.md`, `miniapp.md`) but was:

- absent from `.env.example`, and
- absent from `tests/test_governance.py::USER_ENVIRONMENT_VARIABLES`,

so the guard that is supposed to keep env vars, `.env.example` and the configuration guide in sync
could not see it. Three further operator-facing variables
(`ANTHROPIC_API_KEY`, `SILLYTAVERN_CODEX_AUTH_FILE`, `SILLYTAVERN_CODEX_CLIENT_VERSION`) were
likewise outside the allowlist, so drift on those would also have gone undetected.

**Fixed by:** adding the variable to `.env.example` and all four names to the allowlist. The
allowlist now covers every environment variable `bridge/` reads (62 names, no gaps).

The allowlist is still maintained by hand, so this class of drift will recur. Consider deriving it
from `bridge/settings.py` + a scan of `environ.get(...)` literals, or adding a test that every
`SILLYTAVERN_*` string literal in `bridge/` appears in the allowlist.

### M6 — `docs/configuration.md` had two orphaned table rows (Low) — **FIXED**

In the "Context planning and diagnostics" section, the rows for `SILLYTAVERN_PERF_LOG` and
`SILLYTAVERN_MEMORY_DIAGNOSTICS` were placed *after* the paragraph that follows the table. In
GitHub-flavoured Markdown a table ends at the first blank line, so those two rows rendered as
literal pipe-delimited text instead of table rows — the newest and least discoverable settings in
the table were also the only two that were not rendered as a table.

**Fixed by:** moving both rows inside the table, immediately after the
`SILLYTAVERN_CONTEXT_HISTORY_CANDIDATES` row.

### M7 — Test source-inspection helpers duplicated 22 times (Low) — **FIXED**

`imported_modules()` was defined **16 times** and `top_level_functions()` **6 times** across the
architecture/boundary tests, with 14 of the `imported_modules` copies byte-identical.

**Fixed by:** adding `tests/source_test_support.py` exposing `ROOT`, `BRIDGE`, `module_path`,
`parse_module`, `imported_modules` and `top_level_functions`, and migrating all 18 affected test
files. The shared helper accepts either a filename (`"callbacks.py"`) or an explicit `Path`, so both
existing calling conventions are supported without touching call sites.

Net effect: **22 files changed, 68 insertions, 231 deletions**; no behaviour change, full suite
still green.

Deliberately left alone: the `ROOT = Path(__file__).parents[1]` / `BRIDGE = ROOT / "bridge"`
one-liners that remain in ~13 files. Now that the shared module owns both constants they could be
imported instead, but some files use the resolved form (`Path(__file__).resolve().parents[1]`),
which is subtly different, so a blanket substitution was not safe to automate. Worth a follow-up
sweep if the owner wants the single source of truth to be complete.

### M8 — The coverage number wobbles between runs (Low)

Total combined coverage moved between 79.01 % and 79.02 % on identical trees. The cause is
`bridge/scheduler_safety.py`:

`DatabaseConnectionGate.connect` has a double-checked fast path; the arc `40 → 41` (hit outside the
lock) versus `43 → 45` (hit inside the lock) depends on which thread wins the `threading.Barrier(2)`
race in `test_concurrent_first_open_initializes_once`. Measured over repeated runs of that one file,
`scheduler_safety.py` alternates between 92.55 % and 94.68 % with no code change.

**Impact:** small today (the floor is 76 %, the value is ~79 %), but the project treats coverage as
a ratchet. If the floor is ever raised to within ~1 % of the measured value, an unlucky run will
fail CI for a reason nobody can reproduce.

**Recommendation:** either make the concurrency test deterministic (drive the gate through injected
callables so the losing thread always takes the in-lock path), or exclude the nondeterministic arc
with a documented `# pragma: no branch` on the specific line — *not* by lowering the floor.

*Partially addressed:* a new deterministic test,
`test_requeue_recovers_when_a_later_retry_succeeds`, now covers the
"requeue fails then succeeds on a later retry" path, which previously depended on real SQLite lock
timing. The barrier-race flakiness above is separate and remains.

### M9 — `bridge/pdf_parser.py` has 0 % measured coverage and no security floor (Low)

`pdf_parser.py` is the isolated, resource-limited worker that parses untrusted PDFs
(`RLIMIT_AS`/`RLIMIT_CPU`/`RLIMIT_FSIZE`, stdin/stdout JSON protocol). It reports **0 %** coverage,
and it is absent from `tools/security_coverage_baseline.json`.

The 0 % is structural rather than a test gap: `document_extraction.py:41` launches it with
`sys.executable -I` under `minimal_subprocess_environment()`, which strips `COVERAGE_PROCESS_START`,
so the worker can never be traced even though `patch = ["subprocess"]` is configured. Its parent is
covered end-to-end by `tests/test_pdf_worker.py`.

**Recommendation:** state explicitly (in `tools/coverage_baseline.json` or `CONTRIBUTING.md`) that
the subprocess worker is unmeasurable by design, and add direct unit tests of
`pdf_parser.extract()` and `pdf_parser.main()` in-process — those are importable and testable, and
would cover the page-limit, character-limit and byte-limit branches that the current end-to-end test
does not reach.

### M10 — CI matrix and triggers are narrow (Low)

`.github/workflows/ci.yml`:

- **Single Python version.** All four jobs pin `python-version: '3.11'`, while `README.md`
  advertises "Python 3.11+". Nothing verifies 3.12/3.13.
- **No `schedule` trigger.** The workflow runs on `push` to `main` and on `pull_request` only, so
  rot (a yanked action SHA, a newly disclosed advisory in a pinned dependency, an expired
  credential) is invisible until the next PR happens to open. Dependabot covers dependency *update
  proposals* weekly, not CI health.
- **`git diff --check` is not run**, although `CONTRIBUTING.md` lists it under "Required
  verification".
- **No `npm audit`** for `tests/miniapp-ui` (the lockfile is installed with
  `--ignore-scripts --no-audit --no-fund`).

Action pinning is otherwise exemplary — every external action is pinned to a full commit SHA with a
version comment, `permissions: contents: read` is set at workflow level, and `persist-credentials:
false` is used for the secret-scan checkout.

### M11 — mypy covers 38 % of modules (Info)

99 of 261 modules are type-checked. This is the documented progressive-typing strategy, and it is
genuinely guarded (`minimum_typed_files = 87`, currently 99; `test_static_architecture_policy.py`
asserts the count never shrinks and that specific modules stay in the set).

Two maintenance observations:

- `TYPE_TARGETS` is a hand-maintained list of 99 file paths. Renaming a module silently shrinks the
  mypy surface unless the list is updated in the same commit.
- The mypy result is **environment-dependent**. `bridge/rag_retrieval.py:16-18` declares
  `_numpy: Any` and then does `import numpy as _numpy` inside `try/except`; with numpy absent
  (it is only a transitive lock entry) mypy fails with `no-redef` on that file. CI passes only
  because numpy happens to be installed from `requirements.lock`. Worth a `# type: ignore[no-redef]`
  or restructuring the optional import.

### M12 — Low docstring density below module level (Info)

Module docstrings are at 98 %, but classes are at 15 % (24/156) and functions at 13 % (204/1 607).
For a codebase whose architecture rules are expressed in module docstrings this is defensible, but
the 49 functions over 100 lines in particular would benefit from docstrings stating their contract
and their dispatch order.

### M13 — Lowest-covered modules (Info)

The distribution is healthy (median 88.4 %, 73 modules at 100 %), but these sit well below it:

| Module | Coverage | Notes |
|---|---:|---|
| `pdf_parser.py` | 0.00 % | see M9 (unmeasurable by design) |
| `databank_commands.py` | 10.53 % | 59 of 69 statements uncovered |
| `group_callbacks.py` | 15.73 % | 42 of 55 uncovered |
| `system_prompt_panels.py` | 19.51 % | |
| `group_commands.py` | 25.34 % | |
| `preset_actions.py` | 26.92 % | |
| `session_panels.py` | 27.27 % | |
| `speech.py` | 29.55 % | |
| `databank_panels.py` | 35.58 % | |
| `settings_callbacks.py` | 35.66 % | |
| `feature_callbacks.py` | 35.71 % | overlaps M1/M2 |
| `provider_callbacks.py` | 53.69 % | overlaps M2 |

The group/databank/settings/speech clusters are the clearest concrete testing debt: whole command
surfaces with almost no direct coverage.

---

## 4. Recommendations, in priority order

1. **M2** — split `handle_character_callback` (514 lines, CC 85) into a routing table. Highest
   complexity concentration in the repo.
2. **M1** — collapse the duplicate panel senders; delete `feature_panels.py` or make it the sole
   owner. Small, well-bounded, removes a live two-path divergence.
3. **M3** — extract the shared transcript source-row query used by `memory_curator` and
   `scene_state`; parameterise the extension lifecycle.
4. **M13** — raise coverage on `group_commands` / `group_callbacks` / `databank_commands` /
   `speech` before adding more surface to those areas.
5. **M4** — split `test_pdf_worker.py` and add real worker-limit tests.
6. **M8** — make the concurrency test deterministic before the coverage floor is raised again.
7. **M9** — document the unmeasurable subprocess worker; unit-test `pdf_parser` in-process.
8. **M10** — add a Python 3.12 (or 3.13) job and a weekly `schedule` trigger.
9. **M5 follow-up** — derive the env-var governance allowlist from source instead of hand-editing.
10. **M11 follow-up** — make the `_numpy` optional import mypy-clean without numpy installed.

---

## 5. Checked and found clean

Recorded so the same ground is not re-covered, and so automated dead-code output is not mistaken
for findings:

- **`SILLYTAVERN_RAG_PRIVATE_HOSTS`** looks dead (it never appears as a literal in `bridge/`) but is
  real: `network_security.py:73` derives it as
  `allowed_env.removesuffix("_ALLOWED_HOSTS") + "_PRIVATE_HOSTS"`.
- **`bridge/miniapp_job_repository.py`** and **`bridge/tailscale_funnel.py`** show no inbound
  `bridge.X` imports; the first is imported as `from bridge import miniapp_job_repository as store`
  by `miniapp_jobs.py:14`, the second is an installer CLI invoked from `install.sh:478,496`.
- **`vulture`'s only 100 %-confidence hit** — `bridge/miniapp_runtime.py:74 unused variable 'tb'` —
  is the `tb` parameter of `__exit__`, required by the context-manager protocol. Not dead code.
- **Docs referencing `database.py`, `rag.py`, `rag_core.py`, `repositories.py`** — these are
  deliberate historical references in `CONTRIBUTING.md` describing retired modules, not stale links.
- **`/help` catalog vs Telegram command menu** — exact 40/40 match, no drift either way;
  `help_details.json` adds 26 subcommand drilldown keys on top.
- **Environment variable parity** — after the M5 fix, the governance allowlist covers every
  environment variable read anywhere in `bridge/`.
- **No orphan modules, no import cycles, no reciprocal import pairs, no bare `except:`, no
  `TODO`/`FIXME` markers, no `# type: ignore` suppressions.**

---

## 6. Method

Environment: Python 3.11.2 venv; `ruff 0.16.9`, `mypy 2.3.1`, `pytest 9.1.1`, `coverage 7.16.1`
(matching `requirements-dev.txt`), plus `vulture` and `radon` for dead-code and complexity analysis.

```bash
python tools/check_dependency_lock.py
python -m ruff check .
python -m ruff format --check .
python tools/static_analysis.py
python tools/static_analysis.py --print-type-targets | xargs python -m mypy
python -m pytest -q -n 4 --dist=loadfile --cov --cov-report=json:coverage.json
python tools/check_security_coverage.py coverage.json
git diff --check
```

Duplication was measured with an AST-normalised clone detector (identifiers and string literals
rewritten to placeholders, function bodies hashed, minimum 400-character AST for `bridge/` and 600
for `tests/`). Every reported duplicate was then read and confirmed by hand, which is how the four
false positives in §5 were eliminated. Coverage nondeterminism was established by repeating
identical runs and diffing per-branch execution sets rather than relying on the aggregate number.
