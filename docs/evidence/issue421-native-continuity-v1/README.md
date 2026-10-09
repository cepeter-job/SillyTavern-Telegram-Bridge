# Native context experiment checkpoint

**Completed first trial: measured synthetic savings above 30%; automated quality gate failed. No production activation.**

Start with [results and limitations](RESULTS.md). Full measurements are in
[report.json](report.json); the original provider responses and locked judgments
are in [state.json](state.json). The pre-dispatch [frozen plan](frozen-plan.json)
and [checkpoint](checkpoint.json) are unchanged.

For an independent human assessment, open the [anonymous review packet](human-blinded-review.md)
**before** the [unmasking key](unmasking_key.json) or unblinded journal. No human
approval is recorded. See [artifact digests](evidence-manifest.json),
[unchanged original replay summary](original-benchmark-summary.json), and
[read-only production history shape](live-shape-metadata.json).

The [native source snapshot archive](native-source-snapshots.zip) preserves all six
synthetic SQLite inputs; restored bytes and native receipts were revalidated. The
private operator copies also remain intact. No production transcript,
API key or account credential is included. A scanner false positive on the public
label-map checksum is documented with a single exact historical fingerprint in
`.gitleaksignore`; default detectors remain enabled.
