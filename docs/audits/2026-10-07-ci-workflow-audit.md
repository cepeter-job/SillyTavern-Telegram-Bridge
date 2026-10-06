# CI workflow audit — 2026-10-07

Baseline: `cc772c45ee854a19595a5bf3c3ffa6bd02a87409` (`main`, merge of PR #384,
"feat: expose story trackers and image styles with systematic CI").
Reference design: `zeroclaw-labs/zeroclaw` at
`e654b4b73f286201166cf574c7fe9ff342d3341d` (`master`, 2026-10-06), read through
the GitHub API.

The baseline already contains the 2026-10-06 CI/test restructure: five
independent jobs, an always-evaluated `test` aggregate check, and the registry in
`.github/workflows/README.md`. This audit reviews that result against the
reference design, records the one defect it still carries, and adds the
advisory and scheduled layers plus the hardening the baseline lacks. It extends
the registry rather than replacing it.

This is an engineering review of one repository at one commit, not a security
certification, and it does not claim every workflow path was executed: the
end-to-end workflows run in CI on the pull request that carries this change.

## Scope and evidence

- Read: `.github/workflows/ci.yml`, `.github/workflows/README.md`,
  `.github/dependabot.yml`, `tools/ci_gate.py`, `tools/check_module_sizes.py`,
  `tests/test_ci_gate.py`, the CI guards in `tests/test_ci_supply_chain.py`,
  `tests/test_coverage_contract.py`, `tests/test_governance.py` and
  `tests/test_static_architecture_policy.py`, and the CI guidance in
  `CONTRIBUTING.md`.
- Repository objects: `tools/module_size_baseline.json` and
  `tools/check_module_sizes.py` at `cc772c4`, `1722ae3` (which introduced the
  baseline file), `1722ae3^` and `4462d1c` (the pre-merge tip of the fork's
  `main`).
- A push run of this same workflow on the fork,
  [run `37539901061`](https://github.com/punzer4-code/SillyTavern-Telegram-Bridge/actions/runs/37539901061),
  head `4462d1c`, which is the head commit of the baseline's merge: `test`,
  `secret-scan` and `dependency-audit` succeeded and `static-analysis` failed at
  its module-size ratchet step, skipping the remaining static-analysis steps.
- Local execution at the baseline with Python 3.11.2: the five candidate
  scenarios for the ratchet base selection (table below), the repository
  ratchet and architecture scans, Ruff over 718 files, and 127 selected guard
  tests including `tests/test_ci_gate.py`.
- Static analysis of the workflow files with zizmor 1.30.1 (`--offline`,
  `--persona=pedantic`).

Job logs for run `37539901061` could not be downloaded (the results host returned
`EOF` for the job log object). The cause is therefore reconstructed from the
workflow definition, the failing step, and the repository objects, and is
reproducible from those objects alone.

## Findings

### 1. The ratchet can compare against a revision it must not use

At the baseline the ratchet step selects its comparison revision as:

```yaml
env:
  SIZE_BASE_REF: ${{ github.event.pull_request.base.sha || github.event.before }}
run: python tools/check_module_sizes.py --base-ref "$SIZE_BASE_REF"
```

`tools/check_module_sizes.py` treats a base revision that has no
`tools/module_size_baseline.json` as a first introduction: it bootstraps the caps
from the files measured in that old tree. When the revision is not the tree the
change was reviewed against, that bootstrap measures an unrelated history as
"today's caps", which is how a reviewed cap *lowering* can be reported as
"exception increases the reviewed base limit".

That is exactly what happened in run `37539901061`. The push's pre-push revision
was `34dfc98`, an older fork tip that predates `1722ae3`, the commit that
introduced the baseline file. Reconstruction from repository objects: the merge's
parent `2de0c217` carries caps `bridge/message_commands.py: 621`,
`bridge/npc_service.py: 616` and `tools/static_analysis.py: 534`, which the merge
then lowered to `619`, `575` and `533`. Comparing with that parent yields zero
errors; comparing with a revision that lacks the baseline file selects the
bootstrap path that produced the failure. The same field is the all-zero value on
a new-branch push, and `previous_limits` rejects an all-zero revision outright.

On upstream `main` the defect is latent rather than active: every push since
`1722ae3` has a pre-push revision that carries the baseline file. It still
affects any branch, fork or rewritten history whose pre-push revision predates
that file, and it contradicts the registry claim that the push-before commit is
always appropriate.

### 2. No advisory or scheduled layer

The baseline's five jobs all gate pull requests. Nothing re-audits unchanged
`main` between weekly dependency updates, so a new advisory against a pinned but
unchanged dependency only surfaces when someone opens a pull request. There is
also no PR metadata automation: no title convention, no size label.

### 3. Dependency updates have no cooldown

`.github/dependabot.yml` sets no `cooldown`, so a version update can be proposed
as soon as a release is published rather than after a short public-exposure
window.

### 4. Registry coverage was narrower than the executable workflows

`.github/workflows/README.md` listed the aggregate gate's invariant, origin and
retirement condition, and did not cover the ratchet base rule, the advisory
workflows or the entry files.

## Comparison with the reference design

| Design element | Reference (`zeroclaw`) | Baseline (`cc772c4`) | This change |
|---|---|---|---|
| Independent quality jobs | Separate lint/build/test/security jobs | Five independent jobs | Unchanged |
| Always-evaluated aggregate | `CI Required Gate` with `if: always()` | `test` aggregate with `if: always()` and `tools/ci_gate.py` | Kept, and now cross-checked against `test.needs` by a guard |
| Gate registry | `.github/workflows/README.md` with invariant, origin, retirement condition | Registry with one row | Corrected ratchet claim, entry-file table, four added rows |
| Size ratchet base | Not applicable | PR base or push-before, unvalidated | Validated, baseline-bearing revision, else head parent, else absolute caps |
| Advisory/scheduled scans | `daily-audit.yml`, `monthly-outdated.yml`, `trivy-scheduled.yml` | None | `scheduled-audit.yml` records findings in one labelled issue weekly |
| PR metadata | Title, path, size and risk workflows | None | `pr-title.yml` and `pr-size-labeler.yml`, unit-tested tools |
| Privileged trigger discipline | `pull_request_target` labelers fetch trusted scripts instead of checking out PR code | No `pull_request_target` workflow | Same pattern, guarded: no checkout, classifier fetched from the base revision |
| Unpinned actions | Every action pinned to a full SHA | Same | Same, guard extended across all entry files |
| Checkout credential persistence | Set in two files | `persist-credentials: false` everywhere | Unchanged |
| Dependency cooldown | Daily schedules with grouping | None | Seven-day version-update cooldown |
| Release/packaging/docs pipelines | Present | Absent | Deliberately absent; see below |

The baseline already applies the reference's core CI patterns, so this change
keeps them and adds the layers that were missing instead of re-cutting the
existing gate.

## Changes in this change

- **Ratchet base selection** (`ci.yml`): candidates are the pull-request base,
  the pre-push commit and the head commit's parent. A candidate is used when it
  exists as a commit and carries `tools/module_size_baseline.json`; otherwise the
  next candidate applies. With no usable candidate the step runs the ratchet with
  the absolute caps alone and says so. Observed selection per scenario:

  | Scenario | Selected revision |
  | --- | --- |
  | Pull request whose base carries the baseline | pull-request base |
  | Push whose pre-push commit carries the baseline | pre-push commit |
  | Push whose pre-push commit predates the baseline | head commit's parent |
  | New-branch push (all-zero pre-push value) | head commit's parent |
  | No usable revision (root commit) | none; absolute caps only |

- **Advisory workflows**: `pr-title.yml` (title convention, read-only
  `pull_request`), `pr-size-labeler.yml` (one canonical `size:*` label, the only
  `pull_request_target` workflow, no pull-request checkout) and
  `scheduled-audit.yml` (weekly re-run of the `dependency-audit` and
  `static-analysis` commands against unchanged `main`, reported in one
  `ci-advisory` issue that is closed when a run is clean).
- **Tooling with tests**: `tools/check_pr_title.py`, `tools/pr_size_label.py`
  (both with a `--dry-run` path that needs no credentials) and 30 collected test
  cases across `tests/test_ci_metadata_tools.py` and
  `tests/test_ci_workflow_layout.py`.
- **Guards**: workflow helpers in `tests/source_test_support.py`, plus guards that
  pin the required job set, the aggregate's `needs` against
  `tools/ci_gate.py`, the Mini App smoke command, the privileged-trigger rules,
  the ratchet inputs and the registry.
- **Registry and documentation**: `.github/workflows/README.md` now lists every
  entry file and records four added gates with their invariant, origin and
  retirement condition; the incorrect ratchet sentence is corrected.
  `CONTRIBUTING.md` describes the aggregate gate, the registry rule and the
  privileged-trigger pattern. `AUDIT.md` links this review.
- **Dependabot**: seven-day `cooldown` for version updates on both ecosystems.

## Verification

- 127 selected guard tests pass locally, including the baseline's own
  `tests/test_ci_gate.py`, `tests/test_ci_supply_chain.py`,
  `tests/test_coverage_contract.py`, `tests/test_governance.py` and
  `tests/test_static_architecture_policy.py`; the new modules contribute 30
  collected cases.
- The ratchet scenario table above was produced by running the step's shell with
  each scenario's `github.event` values against this repository's history; the
  ratchet itself passes with the selected revisions
  (`Module sizes: 685 files, 48 shrinking exceptions`).
- `python -m ruff check .`, `python -m ruff format --check .` (718 files),
  `python tools/check_module_sizes.py` and `python tools/static_analysis.py`
  (2023 edges, no cycles or reciprocal pairs) pass.
- Every entry file parses; every `uses:` stays a full commit SHA; every shell
  `run` block passes `bash -n`.
- zizmor at the baseline: 8 findings — 5 informational for jobs without a `name:`
  key, 1 permission without an explanatory comment (`secret-scan`), 2
  `dependabot-cooldown` warnings, 0 high. After this change: 10 findings — 8
  informational, the same permission comment nit, and 1 high for using
  `pull_request_target` at all, which is inherent to labelling fork pull requests
  and is mitigated by never checking out pull-request code. No
  `artipacked` or cooldown finding remains.

Limitations: the full suite was not run locally, because the available scratch
environment does not carry the locked runtime dependencies; CI runs the complete
suite and CodeQL on the exact head. The branch-protection read through the API
returned `403` for the installed token, so the protected check list rests on
`.github/workflows/README.md`, `CONTRIBUTING.md` and the check runs reported for
`main` (`test`, `python-tests`, `miniapp-smoke`, `secret-scan`,
`dependency-audit`, `static-analysis`) rather than on a protection snapshot. Run
`37539901061` is a fork push of the baseline commit, so it demonstrates the
ratchet defect on a real run of this workflow but not on an upstream push, where
the defect stays latent until a pre-push revision predates the baseline file. The
bootstrap-path explanation is analytic and reproduced from repository objects,
not a recorded upstream incident.

## Open items for a maintainer

1. Merge this change and confirm `static-analysis` reports the ratchet base it
   selected in the run log.
2. Confirm branch protection still requires `test` plus the CodeQL checks; the
   five dependency jobs remain visible check names but the protected context is
   the aggregate.
3. Decide whether the advisory title check should become required (remove
   `continue-on-error` and add `pr-title` to branch protection) or stay advisory.
4. Review the first scheduled run: it creates the `ci-advisory` label on first
   use and opens an issue only when a scan fails.

## Deliberately not included

No merge-queue wiring: this repository currently merges directly, and adding a
`merge_group` trigger without enabling the queue would change branch-protection
policy rather than CI. No release, packaging, container, SBOM or docs-deploy
pipelines: there is no registry, package-manager distribution or docs site to
publish to, and the reference's release automation depends on artefacts this
project does not produce. No path filters on the required jobs, matching the
baseline's decision and the reference's caveat about indirectly affected changes.
No new marketplace action, no `harden-runner`, no auto-merge, and no composite
gate job: the `test` aggregate already fills that role. Per-gate Markdown report
artifacts were not added; the baseline publishes coverage, JUnit, smoke and
leak-scan artifacts, `tools/ci_gate.py` already writes the aggregate table to the
job summary, and this document is the Markdown record of the audit itself.
