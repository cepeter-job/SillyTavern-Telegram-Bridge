# Extractive v2 screening — results and disposition

**Decision: do not run a provider trial or activate this candidate.**

Source code: `e17a7d2f02b9b230ae1cadf60b2269c271438ad6`. Python 3.11.16. All figures below are character-count estimates, not provider-reported tokens.

## Complete declared synthetic workload

| Case | Baseline | Candidate | Estimated reduction | Outcome |
|---|---:|---:|---:|---|
| unique_dialogue | 3,934 | 2,939 | 25.29% | preview |
| commitment_dense | 4,775 | 4,082 | 14.51% | preview |
| indonesian | 3,583 | 2,737 | 23.61% | preview |
| unsupported_script | 3,867 | 3,867 | 0.00% | fallback |
| short_history | 1,273 | 1,273 | 0.00% | fallback |
| large_character_card | 8,912 | 7,917 | 11.16% | preview |

All six cases, including unchanged controls: **26,344 → 22,815**, **13.40% aggregate** and **12.43% equal-case-weighted** reduction. The 30% screening target fails.

This workload is complete as declared, not a sampled production traffic distribution. The stronger preservation of user turns, quoted speech and original roles makes this policy different from the earlier statement-packet candidate. No semantic-equivalence or quality gain is inferred from its numerical result.

## Partial local-core live replay

These are read-only counterfactual continuations from two native SQLite scopes, not recorded provider requests. **Full live prompts were not captured.**

| Sample | Source rows | Local-core baseline | Candidate | Estimated reduction | Outcome |
|---|---:|---:|---:|---:|---|
| 1 | 73 | 38588 | 38110 | 1.24% | preview |
| 2 | 15 | 13707 | 13707 | 0.00% | fallback |

Across both local-core denominators: **52,295 → 51,817**, **0.91%** estimated reduction. The unavailable candidate remains in the denominator as full baseline.

Included: card, system, world, persona, author_note, history, scoped_summary, scoped_scene, scoped_episodes, narrative, current_input.

Not reconstructed: external_recall, npc_context, simulation_context, rag_context, group_context, locked_action_and_novel_contracts.

Under an unchanged selection, adding those missing fixed payloads equally to both variants can only lower the savings fraction. This is not a substitute for an actual full-prompt provider measurement.

## Continuity and privacy boundaries

Every user turn, first/recent eight turns, quoted paragraphs with neighbors, unknown markup, original speaker role/order, and non-history payload stays intact. Other older assistant passages may be shortened to exact source excerpts with omission markers. Native receipts verify baseline provenance and retained excerpts, **not semantic redundancy of omitted material**. Normal dispatch rejects all marked candidates and receives the unchanged baseline.

All-source preservation and semantic continuity are unproven. No independent human narrative review occurred; there are no newly generated outputs to score. The earlier frozen experiments and their quality failures are unchanged.

No provider requests, production database writes, transcript exports, deployment, restart, release or pruning activation occurred. Only scalar live metadata is published.

## Verification and replay

The core source passed 126 focused native, statement and new-excerpt tests. Full-tree Ruff lint and formatting passed. These are code checks, not independent narrative review. A separate evidence-integrity test verifies this checkpoint manifest. Exact-PR-head CI status is tracked on PR #477.

```bash
python tools/evaluate_extractive_context.py --output /private/new-synthetic.json
python tools/evaluate_extractive_context.py --database /authorized/live.sqlite3 \
  --metadata-only --output /private/new-local-core.json
```

Reports refuse overwrites. Use the pinned source commit to reproduce the exact code fingerprint; source data may evolve. Any different candidate or provider experiment requires a separate recorded plan, not a rewrite of these results.
