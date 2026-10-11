# Maintenance and release handoff

[Development guide](README.md) · [Workflow](workflow.md) · [Testing](testing.md)

Use this guide when preparing dependency changes or handing a reviewed change to
a maintainer. Contributor work happens in an isolated source checkout. Signing,
publishing and deployment are separate maintainer/operator actions; a merged PR
does not perform or authorize them. The governing rules remain in
[CONTRIBUTING.md](../../CONTRIBUTING.md), and host procedures belong in
[Operations](../operations.md).

## Maintain the complete dependency environment

The protected target is Python 3.11 on Linux. A migration proposal or an
experimental workflow does not change that baseline. The dependency files have
different responsibilities:

| File | Responsibility |
| --- | --- |
| [`requirements.txt`](../../requirements.txt) | Runtime input ranges |
| [`requirements.lock`](../../requirements.lock) | Exact resolved runtime versions and artifact hashes; installation authority |
| [`requirements-dev.txt`](../../requirements-dev.txt) | Exact direct development-tool pins, including uv and pip-audit |
| [`requirements-dev.lock`](../../requirements-dev.lock) | Complete runtime-plus-development resolution constrained to the runtime lock |
| [`tests/miniapp-ui/package-lock.json`](../../tests/miniapp-ui/package-lock.json) | DOM test toolchain |
| [`tests/miniapp-browser/package-lock.json`](../../tests/miniapp-browser/package-lock.json) | Playwright browser test toolchain |

In the isolated development environment described in [Setup](setup.md), edit the
appropriate input and regenerate Python locks from the repository root:

```bash
uv pip compile requirements.txt --python-version 3.11 --generate-hashes \
  --output-file requirements.lock
uv pip compile requirements.txt requirements-dev.txt --constraint requirements.lock \
  --python-version 3.11 --generate-hashes --output-file requirements-dev.lock
```

For an intentional selected upgrade, add `--upgrade-package PACKAGE`, replacing
`PACKAGE` with the actual dependency name. Reserve `--upgrade` for a reviewed broad
refresh. If only development inputs change, regenerate the combined development
lock against the unchanged runtime lock. Review transitive changes, platform
markers and hashes as well as the requested direct version. Include necessary
input and lock changes in the same PR; do not remove hashes or introduce private
credential-bearing index URLs.

Install the result in an isolated environment with hash enforcement, then check
the installed metadata:

```bash
python -m pip install --require-hashes -r requirements.lock
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip check
python tools/check_dependency_lock.py
```

An existing environment can retain packages that are no longer required. Use a
fresh environment when verifying that the changed locks reproduce a complete
installation. The offline lock checker validates pins, hash metadata, direct
requirement compatibility and runtime/development agreement. It does not resolve
the dependency graph, authenticate packages or check downloaded bytes.

Run the [required verification](../../CONTRIBUTING.md#required-verification) and
the dependency audit. The shared coordinator is
[`tools/audit_dependencies.py`](../../tools/audit_dependencies.py); the required
CI job audits both Python locks and both npm toolchains, retaining individual
results even if a scan fails. Installation hashes, `pip check`, vulnerability
audits and behavior tests establish different facts. An advisory-free audit is
not proof against unknown vulnerabilities.

For npm changes, update the relevant package manifest and lock together, install
with the documented `npm ci` commands, and run its DOM/browser checks in
[Testing](testing.md). A Playwright update also needs the matching pinned CI image
and engine verification in [`ci.yml`](../../.github/workflows/ci.yml).

[`Dependabot`](../../.github/dependabot.yml) proposes weekly pip, npm and Actions
updates. It does not automatically merge them. Its seven-day version-update
cooldown does not delay security updates. Do not assume a `.txt` manifest update
regenerates this repository's custom Python `.lock` files.

Review Actions as executable dependencies. Resolve their full SHA pins from the
official upstream; a trailing version comment is only a review aid. When changing
the development uv pin, update the explicit `setup-uv` workflow version and the
combined lock in the same PR. The installer's independently pinned uv bootstrap
has its own download checksum; inspect it separately if changing installation.
Keep workflow permissions minimal and follow the
[workflow registry](../../.github/workflows/README.md) before adding or retiring a
gate. Do not add privileged automation that pushes unreviewed lock changes.

## Record change and version expectations

Describe pending user-visible behavior, maintenance implications and verification
in the current [`CHANGELOG.md`](../../CHANGELOG.md) `Unreleased` section when
appropriate. A contributor should not invent a release date, claim deployment or
assign a published release merely because a PR is ready. The maintainer prepares
the versioned release entry and notes for the chosen reviewed source.

Current entries use headings such as `## [0.3.024] - 2026-10-11`, with tags such as
`v0.3.024`. These are examples from existing history, not the next release number.
[`bridge/update.py`](../../bridge/update.py) reads version headings from the
changelog and detects substantive `Unreleased` content. There is no separate
package-version manifest to bump. Preserve descending release history and the
byte-for-byte [`CHANGELOG-legacy.md`](../../CHANGELOG-legacy.md): its historical
Unreleased section is not pending work. The
[release-history tests](../../tests/test_release_history_preservation.py) protect
that distinction.

State exactly what was measured. Tests passing is not Telegram acceptance,
provider quality approval, signature verification or a deployment result. Retain
frozen research failures and limitations rather than replacing them with a later
success claim. Use public examples and redact private configuration, logs and
host details before attaching evidence.

## Hand off an exact reviewed revision

Give the maintainer the PR/head SHA, behavior and operational impact, verification
commands and actual results, unresolved limitations, and any dependency/schema
changes. Identify author self-review separately from independent review. Link
relevant evidence and the existing recovery runbook.

Require `test`, `dependency-audit`, `static-analysis`, `secret-scan`,
`Analyze (actions)`, `Analyze (python)`, `Analyze (javascript-typescript)` and the
separate `CodeQL` security-result check on the exact reviewed head. The
[workflow guide](workflow.md) explains the merge path. Preserve strict up-to-date
protection; a changed head needs fresh review/check evidence. GitHub CodeQL
Default Setup owns security analysis; do not create a second checked-in CodeQL
workflow.

After merge, verify CI and CodeQL on the resulting `main` revision before release
handoff. Record that revision explicitly: the pre-merge PR SHA and resulting
`main` SHA need not match. Advisory title/size/scheduled checks do not substitute
for required results.

## Separate source, release and runtime state

| State | What it establishes |
| --- | --- |
| Reviewed source commit | A specific tracked tree with review and check evidence |
| Source-snapshot synchronization | An operator-approved copy/advance of specified source; it does not create a release or prove running acceptance |
| Published release | Reviewed notes/assets associated with an authenticated signed tag |
| Installed files | Code currently on disk, which may differ from loaded code |
| Running process | The code loaded at startup, its revision and observed polling/acceptance |

A source checkout includes `.git`, development tooling, tests and documentation;
its virtual environment is local. The signed updater's managed payload contains
`bridge/`, `sillytavern_telegram_bridge.py`, `CHANGELOG.md`, `requirements.lock` and
the `.bridge-deployment.json` identity marker. See
[`deployment_payload.py`](../../bridge/deployment_payload.py). The historical
`application` marker identity is intentionally stable even though the canonical
GitHub repository is now `cepeter-job/SillyTavern-Telegram-Bridge`.

Do not assume the managed mirror is the service's code root. The installer-created
unit launches the source checkout's entrypoint using its `.venv`; custom units may
differ. [`runtime_health.py`](../../bridge/runtime_health.py) captures the loaded
package root at startup. Check Mini App **System → Running**, its revision,
**Installed files**, and resumed polling after a change.

If an operator explicitly approves source-snapshot synchronization, first review
the exact source/destination inventory, additions, replacements and deletions in
the operator's chosen tool's dry run. This repository provides no general source
sync command. Keep private state and local environments out of the snapshot and
do not mirror a development worktree into the runtime. Synchronizing a checkout
used by a service needs the same maintenance and recovery review as any code
replacement. Neither a copied changelog nor a deployment marker authenticates
unsigned source as a release.

## Release trust and operator handoff

The repository has no checked-in automatic release-publishing workflow. Follow
[latest-only release housekeeping](../operations.md#latest-only-release-housekeeping)
for an explicitly authorized release: preserve recoverable refs, metadata and
assets; verify the resulting `main` checks; create an SSH-signed annotated tag;
archive that exact commit; inspect for private state; publish multiline notes and
the versioned ZIP/checksum assets; then download and verify them. Only retire older
releases/tags after the new assets are verified. Preserve cumulative history and
private rollback data, and never move or re-sign historical tags.

Private signing material belongs under maintainer custody outside the repository,
runtime host and assets. The external allowed-signers file contains public keys
only. Use the existing [trust model](../installation.md#trust-model) and
[rotation/revocation runbook](../operations.md#release-signing-key-rotation-and-revocation);
do not invent a backup signer or silently replace an operator's trust. GitHub's
SSH server identity is separate from release-signing trust. The downloaded
installer's first-key provisioning uses trust on first use; independent
provisioning requires independent fingerprint authentication.

Before deployment, the operator reviews the target/unit, signed release,
dependency changes, private backup coverage, maintenance window and recovery
revision. `./install.sh --no-start` prepares and can modify files/dependencies;
it is not a dry run. The operator performs the
[manual signed update](../operations.md#manual-update) when runtime locks change.
Normal `/update` verifies the approved tag and forward ancestry, checks source
and target ownership, compiles staged code, snapshots the database, advances the
clean `main` checkout and replaces the managed mirror before requesting restart.
It refuses changed `requirements.lock`; it does not silently upgrade dependencies.

Retain the pre-update source revision, dependency environment, previous code mirror
and matching database snapshot. The updater's sibling `.bridge-previous-*`
directory is recovery material, not a complete installation backup. A restart
failure can leave source/live files changed; inspect the outcome and running
revision before retrying. Across schema changes, code downgrade alone is unsafe:
use the matching pre-upgrade snapshot and the
[offline restore procedure](../operations.md#database-migrations-backup-and-restore).
Separately preserve private settings, provider catalog, native data and external
trust, plus Hindsight data if used. Finish operator acceptance with configuration
validation, service status, running revision, resumed polling and a supervised
Telegram exchange. Publishing and merging do not establish those results.
