# Developer handbook

This handbook explains how to develop SillyTavern Telegram Bridge: prepare a local
checkout, find the code that owns a behavior, make a tested change, and take it
through review. Start here if you are contributing code, tests, documentation or
maintenance work.

The bridge is a Python application that reads native SillyTavern content and
provides Telegram chat plus an optional Mini App. You can run its automated tests
without a Telegram bot, a provider account or a running SillyTavern server. A real
conversation is a separate integration check.

## Your first contribution

1. Follow [Local setup](setup.md) to create a Python 3.11 development environment
   from the checked-in dependency locks and run a small offline check.
2. Read [Architecture](architecture.md) to trace one request and identify the
   application, persistence and transport owners relevant to your change.
3. Follow [Making a change](workflow.md), starting with the issue or reproducible
   behavior and keeping the change on a branch.
4. Use [Testing and CI](testing.md) to select focused regressions and understand
   the full checks that must report on your PR.
5. Open the PR with the behavior, evidence and remaining limitations recorded.
   [Maintenance and releases](maintenance.md) explains the later dependency,
   signing and operator handoffs.

[CONTRIBUTING.md](../../CONTRIBUTING.md) is the contribution policy, including
architecture constraints and review requirements. This handbook explains how to
apply it. Keep both aligned when a workflow changes.

## Find the right guide

| Task | Starting point |
|---|---|
| Set up a checkout and run the first tests | [Local setup](setup.md) |
| Run an isolated development bot | [Optional connected development](setup.md#optional-connected-development) |
| Understand startup, request handling, storage and background work | [Architecture](architecture.md) |
| Add a command, callback, API action or persisted setting | [Making a change](workflow.md#choose-the-owning-layer) |
| Reproduce a failure and prepare a reviewable PR | [Making a change](workflow.md) |
| Choose tests or investigate a failed CI job | [Testing and CI](testing.md) |
| Change dependencies or prepare a release handoff | [Maintenance and releases](maintenance.md) |
| Report a security vulnerability privately | [Security policy](../../SECURITY.md) |

## Read deeper when the change needs it

The topic guides own their detailed behavior and procedures. Follow their source
links and inspect the implementation before changing an invariant.

| Area | References |
|---|---|
| Settings and provider catalogs | [Configuration](../configuration.md), [environment example](../../.env.example), [provider example](../../config/providers.example.yaml) |
| User-visible commands and story behavior | [User guide](../user-guide.md), [command catalog](../../bridge/help_command_catalog.py), [command details](../../bridge/help_details.json) |
| Prompt ordering, native content and writing profiles | [Prompt assembly](../prompt-assembly.md), [native writing profiles](../native-writing-profiles.md) |
| Summaries, durable memory and retrieval | [Story memory runtime](../story-memory-runtime.md), [memory evaluation](../story-memory-evaluation.md) |
| Context budgets and measurement | [Context efficiency](../context-efficiency.md), [request observation](../request-observation.md), [prefix profiler](../prompt-prefix-profiler.md) |
| Context candidates and quality evidence | [Hybrid shadow evaluation](../hybrid-context-shadow.md), [native context experiment](../native-context-experiment.md), [writing-profile evaluation](../writing-profile-evaluation.md) |
| Mini App behavior, browser validation and recovery | [Mini App](../miniapp.md), [browser validation](../miniapp-browser-validation.md), [operation recovery](../miniapp-operation-recovery.md) |
| Diagnostics and token accounting | [Operation telemetry](../operation-telemetry.md), [token usage](../token-usage.md) |
| Installation, signed updates, backups and recovery | [Installation](../installation.md), [operations](../operations.md) |
| Workflow triggers, gates and their rationale | [Workflow registry](../../.github/workflows/README.md) |

## How to read project history

Use the code and tests on the branch you are changing as the source of truth for
current behavior. The supported contributor environment follows `main` and its
CI configuration; an experimental workflow or an open migration PR does not
change that baseline.

Files under `docs/superpowers/` record designs, implementation plans and reviews.
`docs/audits/` contains dated findings. `docs/evidence/` contains measurements and
review artifacts tied to particular sources and conditions. These records help
explain decisions, but a proposed feature is not automatically shipped and an old
successful experiment does not validate a new commit. Preserve historical
evidence; add a clearly identified correction or a new run when results change.

When changing developer behavior, update the relevant handbook page and its
canonical policy or runbook in the same PR. Keep the user-facing
[README](../../README.md) focused on installation and ordinary use.
