# SillyTavern Telegram Bridge

[![CI](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/cepeter-job/SillyTavern-Telegram-Bridge?display_name=tag)](https://github.com/cepeter-job/SillyTavern-Telegram-Bridge/releases/latest)
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

Chat with your SillyTavern characters from Telegram. Choose a character, pick an
opening message, and continue the story from your phone or desktop.

The bridge runs on a Linux computer or VPS and connects Telegram to your model
provider. Keep that computer running; your phone is the chat interface, not the
server. You can use existing SillyTavern cards and settings, or start with the
installer's sample character. You do not need to install the SillyTavern web
frontend just to try the bridge.

Each story has its own session. The optional Mini App adds screens for managing
characters, sessions and settings inside Telegram; it is not needed for text chat.

**New here?** Follow the quick start below, then the
[first conversation walkthrough](docs/user-guide.md#your-first-conversation).

## What you need

- A Linux computer or VPS with a normal, non-root account and user systemd
  services. Systemd keeps the bot running after setup.
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

Open a terminal on the Linux machine that will run the bot. When using a VPS,
run this in your SSH or PuTTY terminal, **not in Telegram**. Sign in as the
normal Linux user who will own the installation; do not run the installer with
`sudo` or as root.

```bash
curl --proto '=https' --tlsv1.2 --fail --location \
  https://raw.githubusercontent.com/cepeter-job/SillyTavern-Telegram-Bridge/main/install.sh \
  -o /tmp/sillytavern-telegram-install.sh &&
chmod 700 /tmp/sillytavern-telegram-install.sh &&
/tmp/sillytavern-telegram-install.sh
```

Choose **Standard install** for your first setup. The wizard asks for missing bot
and provider settings, looks for existing SillyTavern data, and prepares the
service. Keep your bot token, numeric Telegram user ID, provider API address and
model ID ready. Existing private configuration is preserved. A first install
verifies a signed release before using its code.

When setup finishes, check that the service is running and follow
[Basic bot use](#basic-bot-use) below. If `curl` is missing, setup cannot start,
or the service does not run, use the [installation walkthrough](docs/installation.md)
before trying the command again.

**Optional:** choose **Install + Tailscale Mini App** only when you also need the
management interface and have Tailscale 1.52+ installed and signed in. Text chat
works without it, and you can add it later. During configuration, **Configure
later** lets you stop safely and return when you have the missing details.

The download uses **trust on first use (TOFU)**: you initially trust the public
signing key supplied by this installer. Later updates verify release signatures
against your saved trust file. See the [trust explanation](docs/installation.md#trust-model)
for independent key verification. Rerunning the installer is not a substitute
for the [signed update procedure](docs/operations.md#automatic-signed-update).

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

In Telegram, open your bot's **private chat**:

1. Send `/character` and choose a card. For a first test, choose **Player-centric**
   and **Normal**. Skip optional Persona, World Info and System Prompt choices,
   then choose or create a session and tap **Apply**.
2. Open `/providers` and check the **Story** model, which writes the replies.
   You do not need three different models: Utility can use Story, and Director
   can use Utility. The [user guide](docs/user-guide.md#choosing-models) explains
   when separate models are useful.
3. Send `/start`, preview the Default or Alternate greetings, and choose one.
   You should see the character's opening message.
4. Send normal messages to continue the story. For example:
   `*I look around the room.* "Where should we go next?"`

Use `/session` to return to an earlier story and `/status` to check which story
is active. `/usage` shows the active session’s last 7 days of provider-reported
tokens as plain text. `/settings` changes generation settings; `/narrative` changes viewpoint
and focus. Explore `/director` for planning and endings after the first exchange
works.

Telegram `/help` is the **canonical command reference**. For one action, try
`/help scene refresh` rather than reading every command at once.

`Please use /start command.` means that this session has not sent its greeting
yet. It does not mean you need to reinstall the bot. If a reply fails, return to
that session and use the [retry and recovery guide](docs/user-guide.md#retry-regenerate-or-continue).

## Documentation

| I want to… | Read |
|---|---|
| Install the bot or add the Mini App | [Installation](docs/installation.md) |
| Start a story and learn the everyday controls | [User guide](docs/user-guide.md) |
| Configure paths, providers or optional services | [Configuration](docs/configuration.md) |
| Restart, update, back up or troubleshoot the bridge | [Operations](docs/operations.md) |
| Use the Telegram management interface | [Mini App](docs/miniapp.md) |
| Understand reported model usage and missing counts | [Token usage](docs/token-usage.md) |

The six guides above describe how to use and maintain the bot. Dated files under
`docs/audits/` and `docs/superpowers/` are development records, not alternative
installation instructions.

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
