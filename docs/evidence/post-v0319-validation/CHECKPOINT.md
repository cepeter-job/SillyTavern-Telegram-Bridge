# Post-v0.3.019 validation checkpoint

User approved all recommended post-release work on 2026-10-09: acceptance, writing-profile comparisons, hybrid-history validation, release housekeeping and the preset-compatibility decision.

## Verified starting point

At 09:23:52 UTC the VPS source and remote main were clean at 4e3b31af8c6f8c9f81d97cf09051a2ba31477b03. The v0.3.019 user service was active/running, PID 1270090, with zero automatic restarts. This is a timestamped observation, not a claim about later host availability.

An isolated worktree was created at /home/punzme/sillytavern-telegram-bridge/.worktrees/post-v0319-validation on branch chore/post-v0319-validation. The running source checkout and user stories were not intentionally modified by the validation work.

## Interrupted checks: not passing evidence

A targeted pytest command was launched as remote process 1273713, with output redirected to .superpowers/acceptance-tests.log. Its requested files covered catalogue/placement/profiles, final budgets, continuation, Light Novel acceptance/flow, trackers, updater and hybrid source/shadow/runtime/evaluator regressions. Offline hybrid and read-only live-metadata evaluators were sequenced after a successful test exit. No complete test report or evaluator output was retrieved.

A separate read-only preflight process 1273963 attempted subscription usage/model checks, current-prompt metadata matching and Telegram getMe/getWebhookInfo/getMyCommands. Its returned exit status alone lacked output evidence; quota, model eligibility and API acceptance therefore remain unverified. It contained no model-generation POST and no Telegram send or story-modification command.

Remote filesystem operations then timed out, followed by shell/session operations. A network ping returned at 09:30:12 UTC, but a later tool response reported vm148 offline. An attempt to terminate only the task's own test process timed out; termination is not confirmed. Do not blindly relaunch a duplicate process on recovery.

## Work that can proceed independently

The published GitHub release collection was retrieved and all v0.3.007–v0.3.019 notes were read. Their substantive changes, release commits, migrations and unproven quality/savings qualifications are backfilled in the main changelog. The complete old changelog is preserved using the identical Git blob, not reconstructed text.

The architecture decision is native prompts only for this release line; full foreign-export interpretation remains separately gated. See [Preset compatibility decision](../../preset-compatibility-decision.md).

## Recovery sequence and remaining evidence

1. Verify vm148 connectivity and actual live process health. Inspect the known task process identities, private worktree ledger and existing output files; reconcile interrupted work before retrying. Do not stop unrelated services or erase partial evidence.
2. Complete isolated regression and read-only Telegram/API acceptance. A successful API check is not proof that a human clicked every Telegram/Mini App control. Record actual client acceptance separately.
3. Recheck NanoGPT subscription membership, active allowance and allowOverage=false. Freeze exact requests, model/settings, workloads, blinding map and total request/input/output ceilings before any story trial. No paid fallback, automatic repair or retry to hide failures.
4. Execute matched profile trials and a separate offline hybrid baseline/candidate comparison. Preserve negative controls, failed requests, unknown usage and reversed-order judge disagreements. Synthetic facts must never be extracted from or written to production stories.
5. Label automated blind review accurately. Independent human review remains required before a default/profile or live-pruning activation decision; no human approval has been supplied by this checkpoint.
6. Verify the previously preserved release archive at ~/.local/share/sillytavern-telegram/backups/release-retention/pre-v0.3.019-20261009T085801Z against current remote metadata and assets. Only after changelog merge, validated newest signature/assets and complete recovery backup may older releases/tags be retired. No cleanup deletion was performed while the VPS was unavailable.

The authorized work is incomplete. No provider benchmark was executed, no production quality winner or 30% saving is claimed, and no pruning/default was activated. Main documentation integration, actual live acceptance, provider measurements, independent review and retention deletion must be reported separately.
