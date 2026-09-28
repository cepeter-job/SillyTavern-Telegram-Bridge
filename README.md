# 🌉 SillyTavern Telegram Bridge

[![CI](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/cepeter/SillyTavern-Telegram-Bridge?display_name=tag)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/releases/latest)
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

Use your SillyTavern characters from Telegram while SillyTavern remains the owner
of character cards, Personas, World Info, System Prompts, and other native data.
The bridge adds Telegram sessions, memory, media handling, model/provider routing,
and an optional private management Mini App.

## Quick start

Linux installation is user-scoped. For a first install, verify a signed release **before executing repository code**. Provision the maintainer public key independently as described in [Operations](docs/operations.md), choose a release tag such as `vX.Y.Z`, then:

```bash
git clone --no-checkout https://github.com/cepeter/SillyTavern-Telegram-Bridge.git ~/sillytavern-telegram-bridge
cd ~/sillytavern-telegram-bridge
git -c gpg.format=ssh \
  -c gpg.ssh.allowedSignersFile="$HOME/.config/sillytavern-telegram/trusted-maintainers" \
  -c gpg.minTrustLevel=fully verify-tag vX.Y.Z
commit=$(git rev-parse 'vX.Y.Z^{commit}')
git checkout -B main "$commit"
./install.sh --system-deps --no-start
```

Cloning unsigned `main` is a development-only choice. When an independently obtained bootstrap copy of `install.sh` must clone the repository itself, use `--release vX.Y.Z --allowed-signers PATH`; `--unsafe-main` is the explicit opt-in for an unsigned development clone.

Fill the generated private file:

```text
~/.local/share/sillytavern-telegram/.env
```

At minimum, configure your Telegram bot token/user ID, default model/provider,
and provider credential. The maintained `.env.example` is the source of starter
configuration. For the Mini App, install/sign in to Tailscale and then run:

```bash
./install.sh --with-tailscale-funnel --linger
```

The default Mini App listener is loopback-only; Tailscale Funnel provides public
HTTPS while Telegram `initData` and the allowed-user list remain the application
authorization boundary. See [Installation](docs/installation.md) for the full
fresh-install, upgrade, systemd, and Tailscale procedure.

## What you can do

- Chat with native SillyTavern characters from Telegram.
- Keep multiple sessions with separate model, Persona, World Info, notes, memory,
  summaries, variants, and generation settings.
- Use text, images, documents, voice transcription, optional TTS, expressions,
  Data Bank retrieval, Hindsight memory, and Live Sync.
- Run forum-topic group scenes, Director goals, structured scene state, and Light
  Novel choice mode.
- Manage Characters, Optimizer proposals, models, sessions, Personas, Worlds,
  memory, Data Bank, status, and verified updates from the optional Mini App.
- Use a private provider catalog for OpenAI-compatible, native OpenAI Codex OAuth,
  Anthropic Messages, and OpenCode Muse routes without exposing provider credentials
  to Telegram clients.

For behavior and workflows, see the [User guide](docs/user-guide.md).

## Basic bot use

1. Start the bot and choose a character/session.
2. Send normal messages to continue the conversation.
3. Use `/character`, `/session`, `/model`, `/settings`, `/persona`, `/world`,
   `/memory`, and `/databank` to manage the active session.
4. Use `/start` to open or restart the selected character's opening flow.
5. Use `/status` for the current session/runtime summary.

Telegram `/help` is the **canonical command reference**. Use a command name for
focused detail, for example `/help scene refresh`. The repository documentation
explains concepts and workflows; `/help` reflects the executable command catalog.

## Documentation

| Guide | Use it for |
|---|---|
| [Installation](docs/installation.md) | Fresh install, manual install, systemd, Tailscale Funnel, upgrades |
| [Configuration](docs/configuration.md) | `.env`, paths, providers, network policy, memory/RAG/voice settings |
| [User guide](docs/user-guide.md) | Bot workflows, sessions, generation, native assets, groups, media |
| [Operations](docs/operations.md) | Reliability, privacy, signed updates, downloads, database compatibility, troubleshooting |
| [Mini App](docs/miniapp.md) | Mini App security model, pages, Funnel deployment, installer behavior |
| [Token usage](docs/token-usage.md) | Provider-reported counts, coverage, privacy, retention and upgrade notes |
| [Humanizer reference refresh](docs/humanizer-weekly-sync.md) | Maintainer procedure for the optional Humanizer reference process |

Project-level references:

- [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)
- [License](LICENSE)

## Requirements

- Linux with user systemd for the maintained installer path.
- Python 3.11+ (the installer can provision user-local Python through pinned `uv`).
- A Telegram bot token and numeric Telegram user ID.
- SillyTavern data with at least one PNG character card, or the installer's starter
  card for initial setup.
- A configured Story model/provider route.
- Tailscale 1.52+ only when using the optional Mini App Funnel deployment.

Hindsight, embeddings, image generation, STT, TTS, Live Sync, and a separate
Utility model are optional.

## Safety and data ownership

Keep `.env`, provider credentials, databases, logs, cards, Personas, and private
prompts out of Git. External provider destinations are allowlisted; private/LAN
hosts require an additional opt-in. Uploaded documents and recalled context are
bounded and treated as untrusted input.

The bridge does not replace SillyTavern's native data model. Character, Persona,
and World edits use validation, reference checks, backups, and revision guards.
For deployment/update trust boundaries and recovery procedures, read
[Operations](docs/operations.md) and [Security](SECURITY.md).

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
