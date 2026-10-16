# Workflow and Regression Audit Completion

## Scope

Complete the remaining 2026-10-16 audit actions after #539: reduce repeated
synthetic evaluator setup, retire historical filename restrictions, and measure
CI/shard/Mini App performance. Review and merge the verified changes. Keep the
independent migration/context research acceptance gates and production unchanged.

## Execution and acceptance

- [x] Reuse an immutable full context replay plan only as observation-test input;
  run the real CLI for each validation and retain fresh-process end-to-end cases.
- [x] Run generated-answer rejection matrices through the actual parser/main with
  per-test environment restoration; retain real subprocess and wire smoke cases.
- [x] Limit replay-only retrieval tests to their asserted backend; preserve the
  full default study, corpus, real wire, lifecycle, and no-external-I/O assertions.
- [x] Replace the historical filename blacklist with the existing specific helper
  ownership guard; avoid parsing unrelated source files without changing matches.
- [x] Replace DOM smoke rate-window wall-clock sleeps with explicit fixture-only
  HTTP clock control. Prove auth, 120-request limit, unauthorized-reset rejection,
  expiry and default absence of the control route; real browser fixtures stay unmodified.
- [x] Evaluate current sharding using matched CI measurements. Keep the current
  algorithm unless measured evidence justifies change.
- [x] Verify exact inventory differences, all security/coverage/warning gates,
  independent source review and pre-merge evidence. Final-head and merged-main
  checks are integration gates recorded in PR #540, not preclaimed here.

All execution stays on GitHub-hosted runners; local work is source/static only.
Do not lower coverage, suppress resource failures, weaken authentication, change
provider/production settings, or merge research branches without their evidence.
No speedup is established until runner measurements are recorded.

Measured outcomes and retained design decisions: [audit completion report](../../audits/2026-10-16-regression-audit-completion.md).
