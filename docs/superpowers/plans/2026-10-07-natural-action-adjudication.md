# Natural action adjudication implementation plan

> Execute natively with Superpowers TDD; the user authorized implementation and merge.

**Goal:** Resolve meaningful player actions before narration, keeping randomness and persistent results in the bridge.
**Architecture:** Bounded Utility/Director proposal, durable SQLite receipt, mandatory Story context, then native task extraction from committed evidence.
**Tech stack:** Existing Python 3.11, SQLite, provider ports and Telegram/Mini App. No new dependencies.
**Spec:** ../specs/2026-10-07-natural-action-adjudication-design.md

## Constraints and review focus

No live deployment or private data changes. Preserve source/actor/epoch/lease ownership, existing manual checks, retry and rewrite safety, mandatory prompt results, and private audiences. Director plans are not evidence. No model may select dice/modifiers. Keep modules under 500 lines and do not grow exceptions.

## Task 1: contract and durable receipt

Files: action_contracts.py, action_repository.py, action_schema.py, schema.py; test_action_adjudication.py and test_action_lifecycle.py.
Interfaces: parse_action_proposal(raw, evidence); reserve_action(db, chat, session, request_key, actor_id, action, through_rowid); accept_action(db, ticket, proposal); validate_action(db, ticket); bind_action(db, ticket, source).
- [x] Saved contract tests fail before implementation (14 missing-module failures).
- [ ] Verify parser, single RNG/retry, contention, stale history/actor/session, bind and rewrite tests.
- [ ] Commit checkpoint.

## Task 2: preflight and generation

Files: action_adjudication.py, action_settings.py, message_commands.py, image_messages.py, edit_messages.py, regeneration.py, continuation.py; test_action_generation.py.
- [ ] Write ordering/routing/retry/manual/steering/malformed tests first.
- [ ] Implement prepare_action_context with one bounded model call before Story, mandatory result context, validation before transcript commit and binding after user insertion.
- [ ] Reuse manual results and original results for regeneration/continuation. Fail invalid preflight without fabricating success.
- [ ] Verify and commit.

## Task 3: native task state and controls

Files: native simulation extraction/service/context/view/snapshot, simulation_commands.py, trackers.js; test_action_tasks.py.
- [ ] Test attempted versus established progress, reversible source ownership, scope and check references.
- [ ] Extend native simulation with bounded task records; only committed assistant evidence updates established progress.
- [ ] Add /check mode auto|director|manual and read-only mode/tasks in tracker views.
- [ ] Verify and commit.

## Task 4: integration and merge

- [ ] Remove temporary source-transfer workflow and update docs/help with actual commands and costs.
- [ ] Full test/resource-warning/coverage run plus all lint/type/architecture/size/dependency/security gates.
- [ ] Review final diff; distinguish author self-review from independent review.
- [ ] Push to PR #400, verify exact-head required checks and merge without bypass. Report no live deployment.
