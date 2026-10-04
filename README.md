# SillyTavern Telegram Bridge

[![CI](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/cepeter/SillyTavern-Telegram-Bridge?display_name=tag)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/releases/latest)
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

Chat with your SillyTavern characters from Telegram. Choose a character, pick an
opening message, and continue the story from your phone or desktop.

The bridge reads native SillyTavern character cards, Personas, World Info and
System Prompts. It keeps Telegram conversations in separate sessions and connects
directly to your configured model provider. An optional Mini App gives you a
larger interface for managing characters, sessions and settings inside Telegram.

**New here?** Follow the quick start below, then the
[first conversation walkthrough](docs/user-guide.md#your-first-conversation).

## What you need

- A Linux machine with user systemd for the supported installer.
- A Telegram bot token from [BotFather](https://t.me/BotFather) and your numeric
  Telegram user ID.
- Access to a supported text model provider. The guided setup asks for its API
  endpoint, model ID and credential when needed.
- Your SillyTavern data, if you already have it. The installer can also prepare a
  starter character for an initial setup.

The installer prepares Python 3.11+ and the bridge dependencies. Hindsight,
embeddings, image generation, voice, Live Sync and the Mini App are optional;
you can start with ordinary text chat.

## Quick start

Run these commands as the Linux user who will run the bot:

```bash
curl --proto '=https' --tlsv1.2 --fail --location \
  https://raw.githubusercontent.com/cepeter/SillyTavern-Telegram-Bridge/main/install.sh \
  -o /tmp/sillytavern-telegram-install.sh
chmod 700 /tmp/sillytavern-telegram-install.sh
/tmp/sillytavern-telegram-install.sh
```

Choose **Standard install**. The wizard finds existing SillyTavern data, asks for
missing configuration, installs a verified signed release and starts the user
service. Keep your bot token, allowed user ID and provider details ready. Existing
private configuration is preserved.

Choose **Install + Tailscale Mini App** if you also want the management interface.
That option requires Tailscale 1.52+ already installed and signed in. You can add
the Mini App later. Choose **Configure later** to prepare the installation and
finish configuration before starting the bot.

The normal download uses **trust on first use (TOFU)**: its embedded maintainer
key verifies the release. For the full walkthrough, supported Linux package
managers and an independently trusted setup, see
[Installation](docs/installation.md).

## What you can do

- **Keep separate stories.** Each session has its own history, character setup,
  models, settings and continuity state.
- **Use your native characters.** Select cards, Personas, lorebooks and System
  Prompts; review proposed card edits before applying them.
- **Choose who the story follows.** Stay player-centric, share the stage with an
  ensemble, or follow a world-driven story where other characters carry the plot.
  Director Room lets you inspect and steer the plan without giving away your
  character's decisions.
- **Finish a story without losing it.** Closed Story adds a finale and separate
  epilogue. Alternate Ending creates a new session before the finale; the original
  ending stays unchanged.
- **Shape the conversation.** Regenerate replies, keep alternate responses,
  edit your last turn, or use Light Novel choices while a story is open.
- **Keep track of a long story.** Use summaries, optional memory, NPC state and
  searchable documents. The bridge budgets context before sending it to a model.
- **Add media when you need it.** Generate an image of the current scene, use a
  character portrait as a reference, or enable voice input and spoken dialogue.
- **Recover interrupted replies.** When an answer is already saved, delivery
  recovery can resend it without asking the model to generate it again.

Normal text chat is a good starting point. Optional helpers can add model calls,
latency or local resource use; the [user guide](docs/user-guide.md#model-calls-and-token-use)
explains the tradeoffs.

## Basic bot use

1. Open your bot's private chat and send `/character`. Follow the setup panel to
   choose a character, Narrative Style, mode and session. Optional Persona, World and System Prompt
   choices can be skipped.
2. Use `/providers` to check the **Story** model. **Utility** is the model used
   for summaries and other helpers; it can inherit Story. **Director** plans the
   narrative and can inherit Utility.
3. Send `/start` and choose the character's Default or Alternate greeting.
4. Send normal messages to continue the story.
5. Use `/session` to switch stories, `/settings` to adjust generation, and
   `/narrative` to change viewpoint and story focus, `/director` to inspect plans
   and endings, and `/status` to see narrative continuity and the context budget.

Telegram `/help` is the **canonical command reference**. For a particular action,
ask for focused help, such as `/help scene refresh`.

If a new session says `Please use /start command.`, choose its greeting before
sending dialogue. For an interrupted response, see
[retry and recovery](docs/user-guide.md#retry-regenerate-or-continue).

## Documentation

| I want to… | Read |
|---|---|
| Install the bot or add the Mini App | [Installation](docs/installation.md) |
| Start a story and learn the everyday controls | [User guide](docs/user-guide.md) |
| Configure paths, providers or optional services | [Configuration](docs/configuration.md) |
| Restart, update, back up or troubleshoot the bridge | [Operations](docs/operations.md) |
| Use the Telegram management interface | [Mini App](docs/miniapp.md) |
| Understand reported model usage and missing counts | [Token usage](docs/token-usage.md) |

For project maintenance, see [Contributing](CONTRIBUTING.md), the
[Changelog](CHANGELOG.md), [Security policy](SECURITY.md) and
[Third-party notices](THIRD_PARTY_NOTICES.md).

## Your data

Keep the bot private with the numeric Telegram user allowlist. Character,
Persona and World edits affect shared native SillyTavern files; use separate
bridge instances for people who should not manage each other's data.

Your selected provider receives the prompt and any context or media needed for
the request. Keep `.env`, credentials, databases, logs and private content out of
Git. The [operations guide](docs/operations.md) covers backups and recovery, and
the [security policy](SECURITY.md) explains the trust boundaries.

## Optional provider referral

The maintainer's [NanoGPT invitation link](https://nano-gpt.com/r/UqUJAyNQ) is
available if you choose that provider. The bridge also supports other providers;
see [Configuration](docs/configuration.md#provider-catalog).

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
