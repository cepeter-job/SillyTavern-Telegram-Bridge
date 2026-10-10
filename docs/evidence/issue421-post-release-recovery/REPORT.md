# Post-release recovery and final helper validation — October 10, 2026

**Disposition: recovery and semantic acceptance have not passed. Keep production pruning off.**

This records the owner-authorized checks on deployed v0.3.021, not a new optimization or a human quality approval. The frozen plan was pushed to GitHub as 6d2543f68136b7f65d2af280c87b235cb657b7bd before inference. Baseline: 5370e435f56804fd37b1d57f21b82a3bba6efd7b. Candidate: exact deployed release 3c94d3ca5ee91e1574a0db2c47b415de2fc67188, including the later NPC-root repair. Native source and protocol files were clean. Historical studies and human review records remain unchanged.

## Live recovery

Final read-only checkpoint: 2026-10-10T15:24:51Z (22:24:51 WIB). Service active at the expected release revision.

| Layer | Pending queue classification |
|---|---|
| NPC | One in backoff, one inactive, one parked |
| Scene | One in backoff, one inactive, one parked |
| Summary | One parked after eight attempts; malformed_json |
| Hindsight | One in backoff; retain_failed |
| Curator | One in backoff; work_failed |

These categories use the production queue predicates; a pending inactive or parked job is not automatically eligible for catch-up. Summary invalidation count is zero, but one current summary layer still lags and catchup_complete is false. The intermediate eligible summary job disappeared by the final checkpoint; complete durable recovery nevertheless remains unverified.

There are still 604 undeleted retired external documents, none due at the final checkpoint, and 19 pending native-fact external indexes. Outstanding archival/late-write evidence was not cleared, expired, force-completed or manually edited. Eight valid post-restart episode source segments had no valid associated facts. That count is a warning rather than proof each live source contained a durable fact; no production transcript was exported or used in the synthetic trial.

## Bounded provider accounting

The unchanged two-scenario native fixture covers episodes, NPC, scene, curator and identical story controls. Both arms used fresh disposable SQLite databases. Active subscription admission, overage-disabled checks and the existing account reserve ran before physical calls. All initial calls and repairs count. No external Hindsight inference is included in this ledger.

| Arm | Physical calls | Reported input | Reported output | Native complete cases |
|---|---:|---:|---:|---:|
| Baseline | 12 | 6,939 | 5,786 | 7 of 10 |
| Deployed candidate | 12 | 7,550 | 5,216 | 9 of 10 |
| Total | 24 | 14,489 | 11,002 | — |

Usage is known for all 24 physical calls. The baseline exhausted its twelve-call allowance before the final ferry story control: that logical record is failed:ValueError with **zero physical requests**, not a delivered result. Two baseline NPC cases also failed. The candidate completed the declared physical workload but one NPC case failed. The ledger field logical_completed means records processed, not accepted deliveries.

**Do not compare these arm totals as aggregate matched-work savings.** The baseline did not deliver the complete workload, and native completeness is not semantic acceptance. The one delivered matched story control used 597 input tokens in each arm (0% reduction); the other story comparison is unavailable. The 20% story-input gate remains unmet. No automatic trial resumption or additional provider call was made.

## Essential-fact and NPC findings

1. Candidate English episodes retained six memories, including the promise, refusal, ownership and restricted dispatch knowledge. Candidate Indonesian episodes again returned an empty memories array: zero stored episodes, covered_id advanced to 1, dirty_version=completed_version=1. The declared source contains unpaid debt, a promise, a refusal/retained key, and causal route choice. This **fails essential-fact completeness**, despite native complete status. The fresh English nonempty result also means the earlier two empty results are not proof the prompt deterministically forces empty output.
2. Baseline Indonesian episodes stored four memories after a repair, but the provider assigned the unpaid debt importance 0.6. The production 0.65 filter removed that required fact. Thus the baseline is not an equivalent-quality accepted comparator either.
3. Candidate English NPC output first omitted both roots. The existing repair returned the roots, but the result still failed native NPC/tracker validation. The durable NPC job remained pending at covered_id=0; it was not falsely completed.
4. Candidate Indonesian NPC output supplied a populated supporting-NPC list with invalid field modes. The parser discarded every supporting-NPC operation while treating the list shape as valid. Tracker repair then succeeded: **zero NPC entities/fields were stored, but the NPC job completed and covered_id advanced to 1**. The source explicitly establishes supporting-character state. Missing-root repair alone therefore does not establish successful NPC recovery.

The fourth finding is traced to bridge/npc_extraction.py:_parse_payload: it returns valid=True for an array even when all offered supporting-character operations are rejected. Primary/user exclusions remain intentional. A follow-up should distinguish legitimate empty updates or excluded primary/user entries from a populated supporting-character update whose operations all fail validation; any re-extraction must stay inside the existing single repair allowance and preserve valid simulation/source/publication fences. This report does not implement or provider-certify that follow-up.

These are technical source/fixture inspections, not independent human ratings. No preferences, scores or reviewer declaration were generated.

## Hindsight failure diagnosis

The installed Hindsight server's structured OpenAI-compatible path repeatedly received the same 18-character apology/retry prose with finish_reason=stop. At inspection there were 49 identical logged previews; the response digest is recorded in HINDSIGHT_RESPONSE_SIGNATURES.json. JSON parsing failed at line 1, column 1, character 0. The stack runs through structured provider parsing and retain fact extraction, then returns a service failure to the bridge.

This is **completed upstream error prose rather than a client retention deadline**. JSON mode/schema guidance and structural cleanup were already present in the installed provider code. Increasing the client timeout to 90 seconds cannot make that prose valid extracted facts. A separate authenticated, read-only model-metadata GET returned HTTP 403; it made no generation call and does not by itself identify why the generation route is failing. No credentials, route URLs, private config or production source text are published.

The upstream route must return actual supported structured responses before retention recovery can be claimed. No route switch, credential change or permissive empty-result fallback was applied.

## Verification and scope

A fresh Python 3.11 focused regression run passed **53 tests** covering NPC extraction/partial publication, episodic extraction, memory-response repair, frozen trial plans, physical-call budgets and native fixtures (77.74 seconds). These tests validate the existing structural safeguards; they do not erase the provider/semantic failures above.

The final provider ledgers, exact source revisions, plan hashes, semantic-file hashes and reported totals were rechecked. Raw provider responses and private fixture databases remain on the VPS inside the protected trial directory. Public files contain only synthetic frozen requests, content-free live aggregates, derived fixture-state evidence and accounting.

No production database write, forced queue completion, source deployment, restart, default change or pruning activation was performed by this task. Ordinary live workers continued during the read-only observations, so snapshots are time-scoped. Puntoap was offline; checks ran on vm148. The requested verification and investigation are completed with a failed recovery/quality disposition, not a claim that the outstanding memory failures are fixed.
