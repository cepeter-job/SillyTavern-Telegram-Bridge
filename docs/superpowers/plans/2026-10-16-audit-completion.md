# Workflow and Regression Audit Completion

## Scope

Complete the remaining 2026-10-16 audit actions after #539: reduce repeated
synthetic evaluator setup, retire historical filename restrictions, and measure
CI/shard/Mini App performance. Review and merge the verified changes. Keep the
independent migration/context research acceptance gates and production unchanged.

## Execution and acceptance

- [ ] Reuse an immutable full context replay plan only as observation-test input;
  run the real CLI for each validation and retain fresh-process end-to-end cases.
- [ ] Run generated-answer rejection matrices through the actual parser/main with
  per-test environment restoration; retain real subprocess and wire smoke cases.
- [ ] Limit replay-only retrieval tests to their asserted backend; preserve the
  full default study, corpus, real wire, lifecycle, and no-external-I/O assertions.
- [ ] Replace the historical filename blacklist with the existing specific helper
  ownership guard; avoid parsing unrelated source files without changing matches.
- [ ] Evaluate Mini App fixture isolation and current sharding using matched CI
  measurements. Keep the current algorithm unless measured evidence justifies change.
- [ ] Verify exact inventory differences, all security/coverage/warning gates,
  independent review, current head/base and merged-main CI before closing scope.

All execution stays on GitHub-hosted runners; local work is source/static only.
Do not lower coverage, suppress resource failures, weaken authentication, change
provider/production settings, or merge research branches without their evidence.
No speedup is established until runner measurements are recorded.
