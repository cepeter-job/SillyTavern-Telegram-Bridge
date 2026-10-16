# Prefix profiler verification

This is structural and resource validation, **not** another compression trial.
`RESULTS.json` binds the four implementation files by SHA-256. Observations use
keyed private fingerprints internally; this public receipt contains only counts
from synthetic data. No live traffic, secrets, private source IDs or prompt text
were captured or exported.

The real native-builder demo produced three cohorts of four requests each. Its
unchanging system message retained 1,037 matching characters. A changing Summary
inside that first message correctly reduced the common-prefix lower bound to
896 characters and zero completely stable leading messages. The artificial
duplicate-instruction control produced exactly one duplicate group; the two
ordinary native-builder cohorts produced none. All prompts remained unchanged.

A 240-observation synthetic stress run retained 48 samples (three cohorts, each
with a 16-sample window), explicitly evicted 192 and retained 2,112 fingerprint
units. The receipt records one `tracemalloc` measurement; this is not total process
RSS, a production load test or a performance guarantee.

The profiler also read the 12 preserved `hybrid_history/story` requests from
PR #476, checking every body hash against its saved observation. They reconcile
to **43,249 previously reported input tokens**, including **10,619 cached input
tokens**, across six cohorts. **No complete instruction-message duplicates were
found in that subset.** These are reused synthetic records, not fresh model calls
or production traffic. Other model requests from the original experiment are
explicitly outside this diagnostic subset; no accepted-work saving is claimed.

Useful next measurements require explicitly supplied, correctly scoped final
provider captures. This implementation does not install a live capture hook,
reorder a prompt, remove a repeated instruction or enable history pruning.
