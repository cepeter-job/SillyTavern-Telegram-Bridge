# CI, story tracker views and image styles

## Goal and scope

Audit regression-test maintenance costs, improve CI diagnosis and merge gating,
expose the canonical trackers introduced by PR #380, and let the user choose
Realism or Anime when generating images. The user authorized implementation,
testing, a pull request and merge. Development takes place on puntoap in a
linked worktree inside the requested repository directory.

## Decisions

### Regression tests and CI

Preserve unique regression coverage. Consolidate identical subprocess/source
helpers where an AST comparison proves duplication. Keep large test cases out
of generated parameter IDs. File size alone does not justify deleting tests or
splitting them into arbitrary fragments.

Split the current coupled Python/Mini App job into `python-tests` and
`miniapp-smoke`. Keep all existing security, lint, type, architecture, dependency,
coverage and size checks. Preserve the protected `test` check name as a final
aggregate that requires all five CI jobs to succeed. Missing, skipped, cancelled
or failed jobs must reject success. Keep full tests unconditional and retain
concurrency cancellation, immutable action references and dependency locks.

Use the current ZeroClaw workflow as a reference for named independent jobs,
clear evidence and an explicit quality gate. Its Rust/platform matrix and
selective skips are not needed by this Python bridge. Record audit measurements,
source revision and the resulting CI operation in `.github/workflows/README.md`.

### Canonical story tracker views

SQLite remains the canonical store introduced by migration 25. Add a bounded
read-only projection consumed by Telegram `/trackers` and an authenticated
`GET /api/v1/trackers` Mini App route. The Mini App exposes a Story trackers page
under Manage. `/status` retains its existing session/configuration purpose.

Present relationships, visible agendas, inventory, skills, conditions, factions,
linked quests and recorded checks with native narrative authority and current
source boundaries. Reuse NPC visibility rules and omit hidden planning/intel
fields. Display saved-source freshness and an understandable empty/catching-up
state. No viewing action triggers extraction, a model request, a d20 roll, or a
story turn. The new GET must not create a session. Bound Telegram output and
explicitly identify omitted rows; the Mini App provides the fuller bounded view.

### Image styles

Add Realism and Anime selectors to the existing `/imagine` panel, with a visible
selected state. Realism is the default. Store the validated value using existing
session metadata; reset and alternate-ending copies follow existing image
preferences. Keep image provider/model/size selection and image-only delivery.

Pass the selected style to current-scene prompt construction and apply a final
style directive to both scene and custom image requests. For image references,
preserve subject identity while applying the selected visual medium. Include
style and reference overhead in the provider's prompt budget and the displayed
custom-input limit. Invalid/stale callbacks cannot alter another session.

## Validation and review

Test the gate's CLI outcomes against success, failure, skipped, cancelled,
missing and malformed inputs. Re-run all affected boundary tests after helper
consolidation without dropping assertions. Test tracker scope, privacy, freshness,
no writes/model calls, empty/closed sessions, Telegram length and Mini App rendering.
Test both styles across scene/custom and text/reference routes and prompt boundaries.

Run complete Linux CI, full coverage and security floors, Mini App smoke, all-source
Ruff/formatting, type/architecture/reference checks and the module-size ratchet.
Review the final combined diff independently before merge. Native Windows lacks
the application's Unix fcntl dependency, so its unsupported full-suite failures
cannot substitute for the required Linux result.
