# Audit consolidation — 2026-10-06

Baseline: `28e61d5d323e6ea7f2ea3ef2031372a613feede6` (`v0.3.005`). This review
consolidates the repository audit against actual imports, installer commands,
repository contracts and reproducible regression tests. It does not certify a
production deployment or prove the absence of every memory leak.

## Corrected findings

| Original interpretation | Verified disposition |
|---|---|
| The raw memory snapshot repository is legacy or unused. | Keep it. `memory_snapshot_store` imports and calls both raw snapshot functions; the snapshot tests also exercise the raw layer. The store adds scope, provenance and classification policy above SQL. |
| `tailscale_funnel` has no production caller. | Keep it. `install.sh` invokes the module for `prepare` and `enable`. Python-only importer scans miss this installer entrypoint. |
| Matching names across Mini App and domain modules cause shadowing. | Separate Python modules have separate namespaces. Keep the existing adapter names and explicit route ownership. |
| All three JSON unfence implementations are interchangeable. | Share only the identical episodic/NPC implementations. The Light Novel parser retains its protocol bound and stricter syntax. |
| Both `known_by` decoders have identical semantics. | Share application-level normalization only. The NPC SQL decoder deliberately preserves stored strings, while the episodic decoder normalizes and deduplicates them. |
| `image_prompt_max_chars` is a duplicated constant. | The two functions accept different inputs. The actual constants already have one owner in `image_routing`; retain the public facades. |
| Backup sidecars prove raw, inconsistent database copying. | The code already uses SQLite's online backup API. The verified defect is connection lifetime and destination journal-mode finalization, not the choice of backup API. |
| Latest-only retention is accidental policy drift. | Latest-only GitHub releases/tags are intentional. Preserve the cumulative changelog and private recovery backups; do not change this into history retention or rewrite `main`. |

## Changes in this consolidation

The backup/restore regression holds references to the actual SQLite connections,
then proves that using them after the operation raises the closed-connection
error. This failed on both successful and failed backup/restore paths before the
fix. A separate test reproduced destination WAL/SHM leftovers.

Backup and restore now close every owned connection explicitly, finalize only
the destination with DELETE journal mode, and validate it before activation.
The source remains in WAL mode. A copied backup file is tested independently of
any sidecars, and injected validation/activation failures must clean temporary
files while preserving the current target. New backups are private from creation.
Historical backups with sidecars are not blindly pruned or split apart.

Pure shared helpers now own Telegram UTF-16 length, backslash parity,
episodic/NPC JSON unfencing and actor-name normalization. HTML parsers share the
same newline primitive. The raw snapshot layers, per-domain callback route
tables, repository transaction ownership and stricter parser behavior remain.
No runtime dependency lock or database migration is changed.

The size gate covers maintained Python files at the repository root and under
`bridge`, `tests` and `tools`. It permits cohesive short modules. Files above 500
lines have explicit, exact baseline exceptions: no growth, no new exceptions
relative to the reviewed base, lower caps after shrinkage, and removal after a
file reaches the limit or disappears. The initial baseline is checked against
the actual pre-change files, not accepted as a self-authorizing new limit.

The reference-evidence tool reuses the architecture scanner's import resolution
and adds installer module commands and literal worker paths. It labels weaker
literal evidence separately and never deletes code. Nonliteral callback/dynamic
resolution, runtime entrypoints, production versus test-only use and behavioral
tests still require human review.

## Release and operational boundaries

Six missing historical entries are reconstructed from published release notes
before latest-only cleanup. Their recorded test results describe those releases,
not a fresh execution. The consolidated release must pass full CI and CodeQL on
the exact reviewed head and merged commit, then use an independently trusted
SSH-signed annotated tag and a verified source archive.

Release/tag deletion is a separate, explicitly authorized operator action after
backing up refs, release metadata and assets. Publish and verify the replacement
before deleting its predecessor. Delete only merged branches without open PRs,
retain dirty or unmerged worktrees, and verify remote refs after deletion.
Latest-only release/tag retention does not authorize force-pushing a new root
commit or weakening tag/branch protection.

Storage retention is not a RAM optimization. Do not delete active WAL/SHM files,
rotate an open log, or remove a recovery mirror merely from its age. Verify open
handles and rollback coverage first, preserve a recoverable archive, and leave
private configuration, story data and the running service untouched unless a
separate deployment or state-maintenance action has been authorized.

## Deliberately not included

No mass split of the 21 oversized baseline runtime modules, no forced merging of
small cohesive modules, no generic callback megabinder, no adapter renaming for
AST-name uniqueness, and no deletion of the memory snapshot or installer layers.
Future domain refactors should follow demonstrated cohesion problems under the
size ratchet, one independently tested boundary at a time. A passing static leak
scan or these focused tests is not a universal no-leak guarantee.
