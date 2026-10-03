# Documentation and product review — 2026-10-03

## Scope

Reviewed the README, active user/operator guides, contributor and security
references, and the implementation behind the described workflows at
[`66a80f3`](https://github.com/cepeter/SillyTavern-Telegram-Bridge/commit/66a80f35d0b7ff53ca15f4a292573aa0317d9329).
The resulting PR changes documentation only. Product ideas below are proposals,
not features introduced by this PR.

The review checks discoverability, plain language, consistency with code,
maintenance burden and the choices a new user has to make. It is not a new
security audit, a production RAM benchmark or a study of feature adoption.
The earlier [resource audit](../../AUDIT.md) remains a separate dated record.

## Documentation findings and decisions

| Finding | Decision in this PR |
|---|---|
| The README began with a provider promotion and introduced many internal concepts before the first chat. | Lead with the product, requirements, installation and first conversation. Keep the referral in a clearly optional section near the end. |
| The user guide repeated feature descriptions and long command lists before explaining setup. | Organize around tasks, add a short glossary and daily controls, and leave the exhaustive command reference in `/help`. |
| Forced conversational asides and absolute claims obscured precise behavior. | Use direct instructions and explain recovery limits without promises such as “No sync conflicts” or “No silent overwrites. Ever.” |
| Narration/dialogue output and greeting formatting were hard to discover. | Add a rendered example and distinguish automatic reply formatting from user input and TTS quote rules. |
| The guide said “Grounded User” where the Telegram button says “I am not MC.” | Name the actual control and explain the underlying feature. |
| The context explanation omitted the safety margin from its formula and 900K example. | Document both deductions: 28,016 estimated input tokens for the default profile and 887,712 for a recognized 900K alias with the default reserve. |
| A 32K fallback was described as safe for unknown models. | Explain that it is a planning fallback; an unknown model may need an explicit smaller limit. |
| Adding Funnel to a running installation used the dependency-installing command. | Show `--no-deps` for adding the Mini App to an existing compatible environment; retain the stopped-service procedure for dependency changes. |
| Retry, regeneration, choice recovery and image retry were easy to confuse. | Add a situation/action table and explain when another model request may occur. |
| Optional helpers had scattered cost explanations. | Add a model-call table and link it to actual usage coverage. |
| Backup instructions focused on SQLite without clearly defining a complete backup. | List native content, private configuration and optional Hindsight data separately. Use a quoted restore filename and stop on command failure. |
| Mini App user guidance included a development-only DOM harness. | Move the test commands to Contributing. Keep Mini App setup, usage and troubleshooting together. |
| Static migration totals and completed/speculative plans increased documentation upkeep. | Link to the migration source and remove three planning-only files from the current docs. |

### Removed from the current documentation

- The completed provider context-metadata discovery plan and design spec. Their
  shipped behavior is described in Configuration and the user guide.
- The unimplemented Humanizer weekly-reference-refresh specification. No refresh
  service, timer or runtime feature is removed; none is installed by the bridge.

These files remain recoverable in the inspected commit's Git history. Public
configuration examples, `config/system_prompts.example/`, licenses, asset
provenance, security guidance, migrations and behavioral regression tests remain.
The existing dated audits retain their original scope and evidence.

## Product direction

Keep the main promise narrow: **a dependable private Telegram roleplay bot that
uses native SillyTavern cards and preserves a user's story**. Improve the controls
around existing capabilities before adding more background model work.

There is no adoption evidence in this review that makes a whole advanced mode
“useless.” Complexity alone is insufficient reason to remove sessions, memory,
provider transports or recovery. The strongest reduction candidates are optional
extra prose rewriting and automatic subjective grading.

## Features to keep

| Feature | Why it belongs in the core | Source evidence |
|---|---|---|
| Greeting choices and narration/dialogue formatting | They make card-based roleplay recognizable and let the user choose the opening. | [Greetings](../../bridge/greetings.py), [formatting](../../bridge/roleplay_format.py) |
| Separate sessions and durable saved-output recovery | They protect story continuity and avoid needless regeneration after delivery failures. | [Recovery regressions](../../tests/test_delivery_review_regressions.py) |
| Context discovery, explicit limits and compaction | They bound prompt growth and explain oversized requests before generation. | [Context planner](../../bridge/context_compaction.py), [metadata tests](../../tests/test_context_window_hardening.py) |
| Current Scene images and optional character reference | They serve an existing roleplay workflow while leaving the transcript unchanged. | [Image generation](../../bridge/image_generation.py), [image tests](../../tests/test_image_generation.py) |
| Reviewed optimizer proposals and backups | Users can improve cards while reviewing changes before native files are replaced. | [Optimizer](../../bridge/character_optimizer.py), [resilience tests](../../tests/test_character_optimizer_resilience.py) |
| Backup/restore, private diagnostics and signed updates | These provide recovery and maintainability for a continuously running bot. | [Database backup](../../bridge/database_backup.py), [diagnostics](../../bridge/memory_diagnostics.py) |

These features already exist. “Add context discovery,” “add opening choices” or
“add usage tracking” would misstate the current implementation.

## Prioritized improvements and additions

| Priority | Proposal | Value and tradeoff |
|---|---|---|
| P1 | **Expose usage in Telegram.** Add a `/usage` panel backed by the existing ledger. | Users could inspect calls/tokens without deploying the optional Mini App. Preserve missing-count coverage, private-chat scope and actual model/purpose attribution. |
| P1 | **Show context details in model selection.** | `/status` already shows window, usable input, last estimate and compaction; `/prompt` adds detail. Reuse that information beside model choices so a user can compare limits without leaving the panel. |
| P1 | **Offer a measured low-memory concurrency profile.** | Current generation/utility/memory pools are lazy and bounded, with 3/2/1 workers. Lower configurable concurrency could reduce simultaneous work on a small VPS, at the cost of more waiting. Benchmark before claiming a RAM saving. |
| P1 | **Show running-version identity in Telegram status.** | Mini App System already distinguishes running code from installed files; `/status` currently shows session/context/settings. Reuse the captured runtime identity so bot-only users can verify an update without guessing from a checkout. |
| P1 | **Improve setup guidance in existing panels.** | Explain Story versus Utility, show the next step after Apply, and make `/start` requirements clearer. Reuse the current setup flow rather than adding a second wizard. |
| P2 | **Add optional usage warnings.** | Warn about provider-reported tokens or call totals. A hard limit needs a separate policy for missing counts, concurrent requests and in-flight work. Do not invent subscription balances or dollar costs. |
| P2 | **Show backup age and clarify complete backup coverage.** | A last-successful-backup indicator would make an existing maintenance tool easier to use. Any scheduled backup should be an explicit operator choice, with retention and restore verification. |
| P2 | **Make expensive helper actions visible before use.** | Label scene-image prompt preparation, choice strategies, Humanizer and card ranking by their extra calls. Avoid automatic second paid image requests after failure. |

Implementation evidence: the [usage repository](../../bridge/token_usage_repository.py)
already aggregates counters, days, models and purposes; [prompt panels](../../bridge/prompt_panels.py)
and [session status](../../bridge/status_panels.py) already expose the budget.
[Runtime health](../../bridge/runtime_health.py) and [Mini App System](../../bridge/miniapp_system.py)
already provide running/installed identity; [background executors](../../bridge/background.py)
already bound work. The [usage guide](../token-usage.md) lists important exclusions,
including Director planning and embeddings, and explains why counts are not invoices.

## Features to simplify or consider removing

| Candidate | Recommendation | What would be lost or needs checking |
|---|---|---|
| **Humanizer** | Keep it off by default, as it is now. Consider removal if actual users do not prefer the extra rewrite. Improve the original Story prompt first. | An optional prose style. The extra call adds latency/tokens, and structural guards cannot guarantee unchanged meaning. See [implementation](../../bridge/humanize.py). |
| **Automatic S–D character ranking** | Prefer on-demand grading or make automatic grading configurable. Keep the optimizer's reviewed edit workflow. | A convenient but subjective browsing cue. Validate demand before removing badges/assets; do not present a model's grade as objective quality. See [ranking](../../bridge/character_quality.py). |
| **Weekly Humanizer reference automation** | Leave it out of the runtime and current roadmap unless a specific maintenance need emerges. | No current runtime capability is lost; the removed file was a future specification. A weekly fetch of a pinned reference would not improve replies automatically. |
| **Overlapping command/panel controls** | Keep a small daily command set prominent and put specialist actions in advanced help. Make typed argument behavior consistent in a separate change. | The Mini App is optional, so removing bot commands solely because a web page duplicates them would remove access for bot-only users. |
| **Light Novel A/B/C and group modes** | Keep the modes but lead with Normal and explain the distinct call costs. | They support different story workflows. There is no usage evidence here for deleting a strategy, Director mode or manual turn ownership. |
| **Memory, NPC Bank and Data Bank controls** | Explain each by the problem it solves and simplify defaults/refresh controls. | These stores have different knowledge, ownership and deletion rules; merging them blindly risks losing those boundaries. |
| **Always-installed optional integrations** | Explore separate installation profiles for speech/Hindsight only if package footprint is a real user problem. | This requires lockfile, installer and import-path work. Installed packages alone do not prove idle RAM consumption; lazy-loading behavior needs measurement. |

## Suggested follow-up PRs

1. **Telegram usage and runtime visibility:** reuse the current ledger and running
   identity, keep existing context diagnostics, preserve unknown counts and avoid
   new polling or paid probes. Context display in model selection can be a small
   follow-up presentation change.
2. **Low-memory operating profile:** validated configurable pool/admission limits,
   queue/recovery regressions and representative peak-memory/latency measurements.
3. **Optional card grading and clearer advanced controls:** retain reviewed optimizer
   proposals, keep Humanizer off and label additional model work.
4. **Backup status and operator guidance:** show the latest successful snapshot and
   document an explicitly enabled schedule only if requested.

Each proposal needs its own design and implementation review. This documentation
PR enables none of them and makes no live deployment changes.

## Validation

The documentation PR runs the existing installation/governance checks, verifies
relative file and heading links, checks shell-block syntax and whitespace, and
receives an independent review against current source. Exact results and the
GitHub CI status are recorded in its PR description/checks. No runtime tests or
production settings are changed to make the documentation pass.
