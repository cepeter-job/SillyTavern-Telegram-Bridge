# Post-release memory recovery and final-revision validation

Frozen before new inference on 2026-10-10T15:14:18.370541+00:00.

Compare baseline 5370e435f568 with deployed v0.3.021 3c94d3ca5ee9 using the unchanged helper-study protocol and two synthetic scenarios. Ten logical cases per arm cover episodes, NPC, scene, curator and unchanged story controls. No production transcript is used. The later NPC-root repair is included in this candidate.

Plan SHA-256: 0bced88e5d1405d78b4deca3009bee203addc952239a46662a99ed890d406fe4. Strict combined caps: 24 physical calls, 300,000 input and 32,000 output tokens (12 / 150,000 / 16,000 per arm). Existing subscription admission rejects overage and reserves account capacity. All continuation, repair and retry calls count; pending/unknown delivery stops the arm without automatic resumption.

Native shape acceptance is separate from essential-fact completeness. Inspect accepted SQLite fixture state for the required promises, refusals, debts, ownership, active-branch causality and restricted knowledge. Empty episode extraction from these fact-bearing sources fails semantic acceptance. Inspect NPC repair, valid simulation preservation and durable completion independently. Checks are technical evidence, not independent human adjudication.

The trial does not activate pruning or change live state/routes. Identical story controls cannot demonstrate story-input reduction. No efficiency or production-equivalence claim follows from JSON validity alone. Hindsight upstream inference is outside this ledger and remains separately diagnosed.
