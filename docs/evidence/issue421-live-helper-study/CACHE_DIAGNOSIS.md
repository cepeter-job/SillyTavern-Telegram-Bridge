# Scoped cache diagnosis — 2026-10-10

Read-only observations from the deployed bridge, interval 2026-10-09 15:42 UTC through 2026-10-10 12:20 UTC. No conversation text or readable session identities exported. The runtime source was `5539643`; its signed v0.3.020 marker alone does not identify this later source revision.

Grouping uses the observer's session, model and provider fingerprints together, without joining unknown identities across process lifetimes. There are 29 story requests in three comparable cohorts (21, 7 and a singleton), not 29 independent stories.

| Cohort | Story requests | Complete input | Cached input | Requests within 5 minutes of preceding comparable request | Cache hits among those |
|---|---:|---:|---:|---:|---:|
| A | 21 | 408,172 | 2,432 | 9 | 1 |
| B | 7 | 115,992 | 49,344 | 5 | 4 |
| C | 1 | 12,774 | 3 | Not comparable | Not comparable |

Cohort A's stable prefix grew from 11,136 to 12,544 characters after its initial sample. Its input grew from 7,250 to 28,542 tokens. Cohort B's prefix varied from 30,563 to 14,976 characters. This is character-level prefix observation, not a provider cache-key or cache-retention guarantee.

The first cohort's single cache hit was 136 seconds after its preceding request. The second had hits at gaps of 106, 81, 41 and 80 seconds. Both cohorts also had non-hits inside five minutes. Request spacing alone therefore does not explain the difference. No complete repeated instruction messages were observed in the original inspected story subset. Provider-side routing/cache policy remains unknown; it is not justified to rewrite instruction order or claim a TTL from these samples.

## Engineering decision

Keep the story assembly byte-for-byte unchanged in this corrective PR. Do not merge unrelated dialogue occurrences or move dynamic information to gain a speculative cache benefit. Provider-reported cached tokens remain part of total input: they are not removed input and cannot be credited toward the >=20% story-input reduction gate.

Treat helper reliability and continuation costs as a separate accepted-work objective. The new matched trial includes unchanged story controls so a helper improvement cannot be mislabeled as story compression. A new pruning candidate still requires representative source-backed continuity proof and fresh independent human review.
