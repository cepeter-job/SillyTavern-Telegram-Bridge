# Making a change

[Developer handbook](README.md) · [Setup](setup.md) · [Testing and CI](testing.md)

A contribution is ready for review when another person can understand the problem,
follow the owning code path, reproduce the verification and assess the remaining
risks. This workflow applies to code, tests, documentation and maintenance changes.
[CONTRIBUTING.md](../../CONTRIBUTING.md) contains the governing rules.

## Define the outcome and establish the baseline

Read the relevant issue, recent changes and open PRs before starting. An existing
fix or migration may already cover part of the work. Describe the observed and
expected behavior in terms of a user action or an application contract. Record
how to reproduce a bug with synthetic inputs and identify what success means.
For a feature, agree on the user flow, state changes and failure behavior before
introducing new interfaces.

From an existing development clone, inspect local work before creating a branch:

```bash
git status --short
git fetch origin
git log -5 --oneline origin/main
git switch -c fix/descriptive-name origin/main
```

These commands assume `origin` is the canonical repository, as in [Setup](setup.md).
The branch name is an example. If the status shows existing work, preserve it in
its own branch/worktree before continuing; do not reset or discard it to obtain a
clean baseline. When your clone uses a fork as `origin`, use its canonical
`upstream/main` for the base instead. Record the starting commit with
`git rev-parse HEAD` so results have an identifiable source.

Run the existing focused tests for the affected behavior before editing. A failure
already present at the base should be investigated and reported separately from
your change. Ordinary development should not use a running installation as the
working directory or its database as test input.

## Find the behavior before editing it

Use repository search to find the visible action, error text or contract. For
example, to investigate a character optimizer interaction:

```bash
rg -n 'optimizer|Optimizer' bridge/character* bridge/miniapp_characters.py tests
rg -n 'character_optimizer' bridge tools tests
```

Read callers as well as the function that looks wrong. Establish the route from
Telegram or Mini App ingress through the application use case, provider/file I/O,
local commit and delivery. Use [Architecture](architecture.md) for the layer map.
Check asynchronous jobs, retries, session changes and stale forms, because the
visible failure may occur after the initiating handler has returned.

Avoid changing a similarly named helper without confirming it is the actual
owner. Historical umbrella modules and re-export APIs have been removed; current
tests should patch the dependency where it is actually defined or called.

## Choose the owning layer

The following recipes describe the files to inspect, not a requirement to change
every listed file for every contribution.

| Change | Implementation path | Behavior to preserve and verify |
|---|---|---|
| Telegram command or panel action | [command_routes.py](../../bridge/command_routes.py), the domain command/callback/input owner, and its panel renderer | Authorization, chat/topic/session scope, expiry, duplicate-click handling, queue identity and clear feedback |
| User-facing command help | [bot_commands.py](../../bridge/bot_commands.py), [help_command_catalog.py](../../bridge/help_command_catalog.py), [help_details.json](../../bridge/help_details.json), [user guide](../user-guide.md) | Actual command/button labels and supported input forms; Telegram `/help` remains the command reference |
| Mini App action or screen | Domain `miniapp_*.py` route and shared use case, [miniapp_http.py](../../bridge/miniapp_http.py), corresponding [browser module](../../bridge/miniapp_assets/) | Signed identity, input bounds, operation-ID deduplication, recoverable uncertainty, preserved drafts and useful loading/error states |
| Provider behavior | [provider_port.py](../../bridge/provider_port.py), [model_router.py](../../bridge/model_router.py), selected transport and request-budget owner | Explicit injection, host/credential rules, bounded responses, cancellation, usage accounting and fallback budgets |
| Persisted feature or schema change | Application write owner, feature repository/schema, [schema.py](../../bridge/schema.py) and [migrations.py](../../bridge/migrations.py) | Caller-owned transaction, atomic rollback, fresh initialization, upgrade from an older schema and unchanged ownership boundaries |
| New process setting | [settings.py](../../bridge/settings.py), its value parser/collaborators, [.env.example](../../.env.example), [configuration guide](../configuration.md) | Explicit immutable snapshot, validation/defaults, private paths and no import-time environment reads |
| New per-session preference | Existing session/settings use case and canonical metadata or domain repository | Session isolation, atomic updates and existing metadata ownership; a preference does not automatically require a new table column |
| Background task, memory or recovery | Feature service, durable queue/lease owner and [runtime lifecycle](../../bridge/runtime_lifecycle.py) | Bounded admission, original actor/session, reset/rewrite invalidation, idempotence, teardown and eventual recovery |
| Dependency, CI or installer change | [Maintenance](maintenance.md) and [workflow registry](../../.github/workflows/README.md) | Locks and provenance, stable required gates, source/runtime separation and recovery |

For a schema change, add an ordered migration through the established schema
owner; do not rewrite an already applied migration's identity. A test should
exercise both a new database and an upgrade from the relevant previous state,
including failure rollback when meaningful. Repository primitives perform SQL
under an active caller transaction. External provider/file preparation should
finish outside that transaction; native file replacement needs its own revision
and staging checks.

For prompt, memory or context changes, verify more than an output-string match.
Preserve source roles, readers, causal continuity, branch/reset boundaries,
negation, commitments and user control. Use the evaluation protocols linked from
the [handbook](README.md#read-deeper-when-the-change-needs-it). A token estimate,
mocked provider or automated structural check does not establish narrative
quality or authorize enabling an experimental history transformation.

## Implement and verify incrementally

For a behavior bug, add a focused regression that fails for the reproduced reason.
Make the smallest complete correction and rerun it. Include meaningful failure
paths: a stale actor/session, interrupted operation, external failure or rollback
may matter more than another ordinary success case. Keep unrelated refactors out
of the change.

For documentation-only work, verify statements and examples against source, run
the existing focused documentation checks and inspect rendered links/tables.
There is no need to create an artificial product regression for a prose edit.

Use [Testing and CI](testing.md) to widen verification to the affected domains,
then the normal full gates. Keep the exact commands, interpreter, source revision
and results. Distinguish a selected test pass, a complete suite pass, an audit
blocked by network access and a manual acceptance result. Never report an
unexecuted check as successful.

When a gate finds a problem, follow the ownership and evidence rather than
lowering the gate. Preserve coverage/security floors, the type surface, the
500-line ceiling for new Python files and shrinking exceptions. Static reference
reports help investigation; they are not permission to delete dynamic handlers.
An intentional architecture change must retain behavioral coverage while
updating the corresponding ownership guard.

Update the guide that owns changed behavior in the same PR. Configuration changes
need their example and configuration reference; new user actions need help and
user guidance; workflow changes need the registry. Put an appropriate pending
user-visible or operational note under `Unreleased` when warranted. Leave
historical evidence and release records intact.

## Prepare the commit

Inspect the diff before staging and stage only the files belonging to this change:

```bash
git diff --stat
git diff
git diff --check
git status --short
```

Use `git add` with the actual intended file paths, then inspect `git diff --cached`
before committing. Keep private `.env` files, credentials, logs, databases, native
user content, generated reports and signing material out of the commit. Reviewed
public examples have a protected hash manifest; an intentional example change
needs maintainer review and its matching manifest update.

Use a clear subject such as `fix(miniapp): preserve optimizer recovery after a lost response`
or `docs: explain contributor development workflow`. PR titles follow
`type(scope): subject`, with an optional scope; the
[title checker](../../tools/check_pr_title.py) lists accepted types and the
100-character limit. Its CI workflow is advisory, but following the convention
keeps the eventual history understandable.

## Open the pull request

If you have repository write access, push the feature branch to `origin`. If you
are contributing through a fork, create your fork on GitHub and add it as a push
remote in the canonical clone. Replace `YOUR-USER` with your GitHub username:

```bash
git remote add fork https://github.com/YOUR-USER/SillyTavern-Telegram-Bridge.git
git push -u fork HEAD
```

Open a PR targeting `cepeter-job/SillyTavern-Telegram-Bridge:main` from the branch
you pushed. Use a draft while required implementation or evidence is unfinished.
Describe:

- **Problem and outcome:** what is wrong or missing, who it affects and the
  expected behavior after the change.
- **Changes and decisions:** the owning components, relevant tradeoffs and why
  this approach fits the existing architecture.
- **Verification:** exact executed commands and results, relevant CI links,
  source revision and any manual acceptance evidence.
- **Operational impact and limitations:** dependency/schema changes, recovery
  considerations, untested paths and follow-up work.

Link the related issue and use a closing reference only when the PR actually
completes it. A partial improvement should state what remains. Evidence for a
different commit or model configuration must be identified as such.

## Review, refresh and handoff

Review your own diff first, then obtain independent review as applicable. Identify
the author of each review; self-review is not independent review. Resolve feedback
with source evidence and tests, and explain a disagreement rather than silently
ignoring it. New commits change what the reviewer and CI must assess.

If `main` advances, refresh from the canonical base using the project's normal
branch workflow. Resolve conflicts by preserving the reviewed intent on both
sides, inspect the complete resulting diff and rerun affected checks. If rebasing
a published branch would rewrite shared history, coordinate with its contributors
first. Do not bypass strict up-to-date protection or merge against stale check
results.

Before merge, verify the exact reviewed head and required checks listed in
[CONTRIBUTING.md](../../CONTRIBUTING.md#required-verification), including the
separate CodeQL security-result check. The `test` aggregate depends on its actual
quality jobs; a skipped, cancelled or missing dependency is not a pass. Advisory
title/size results do not substitute for those requirements. See
[Testing and CI](testing.md#read-a-ci-failure) for failure triage.

A merged PR changes the source repository. Record the resulting `main` commit
and hand off any dependency, schema or runtime acceptance requirements through
[Maintenance and releases](maintenance.md). Release signing, publication and
deployment have their own operator procedures and verification.
