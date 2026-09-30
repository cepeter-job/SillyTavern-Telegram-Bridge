# 🌉 SillyTavern Telegram Bridge

[![CI](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml/badge.svg)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/cepeter/SillyTavern-Telegram-Bridge?display_name=tag)](https://github.com/cepeter/SillyTavern-Telegram-Bridge/releases/latest)
[![License: GPLv3](https://img.shields.io/badge/license-GPLv3-blue.svg)](LICENSE)

Use your SillyTavern characters from Telegram while SillyTavern remains the owner
of character cards, Personas, World Info, System Prompts, and other native data.
The bridge adds Telegram sessions, memory, media handling, model/provider routing,
and an optional private management Mini App.

## Quick start

The maintained installer targets Linux with user systemd and runs as the intended
non-root user. For the normal first install, download the small installer and run
it in a terminal:

```bash
curl --proto '=https' --tlsv1.2 --fail --location \
  https://raw.githubusercontent.com/cepeter/SillyTavern-Telegram-Bridge/main/install.sh \
  -o /tmp/sillytavern-telegram-install.sh
chmod 700 /tmp/sillytavern-telegram-install.sh
/tmp/sillytavern-telegram-install.sh
```

The no-argument installer shows:

```text
1) Standard install                 [recommended]
2) Install + Tailscale Mini App
3) Prepare only, do not start
4) Repair/reinstall dependencies
5) Advanced options
0) Exit
```

**Standard install** auto-detects the Linux family/package manager, installs only
missing bridge prerequisites, creates the standard pinned maintainer trust file,
discovers and verifies the newest signed release, and leaves a real Git checkout
on local branch `main` for the built-in signed `/update` flow. Supported base
package managers are `apt-get`, `dnf`/`yum`, `pacman`, and `zypper`.

If SillyTavern is already installed, the wizard detects its application root,
reads its configured `dataRoot`, finds native users, and points the bridge at
their character, World Info, System Prompt, Persona settings, and avatar paths.
It reports Node/npm dependency state but never runs `npm install` or otherwise
mutates the existing SillyTavern installation.

On a fresh bridge configuration, the wizard asks only for the required values:
Telegram bot token, numeric allowed user ID(s), default `provider::model`, and,
for the built-in OpenAI-compatible setup, endpoint and API key. Secrets are
entered without echo. The provider hostname is derived automatically from the
endpoint. Existing private `.env` values and custom service units are preserved.
Choose **Configure later** to prepare the installation without starting the
service.

Option **Install + Tailscale Mini App** uses the same setup plus Tailscale Funnel;
Tailscale itself must already be installed and signed in. See
[Installation](docs/installation.md) for details and verification commands.

### Trust model

The normal easy path is **trust on first use (TOFU)**: the pinned release-signing
public key embedded in the downloaded installer is fingerprint-checked locally,
then used to verify the signed release tag before repository code is checked out.
If you require an independent first-key trust boundary, provision the
allowed-signers file through a separate trusted maintainer channel and use the
[advanced independently trusted bootstrap](docs/installation.md#independently-trusted-bootstrap)
instead.

## What you can do

- Chat with native SillyTavern characters from Telegram.
- Keep multiple sessions with separate model, Persona, World Info, notes, memory,
  summaries, variants, and generation settings.
- Use text, images, documents, voice transcription, optional TTS, expressions,
  Data Bank retrieval, Hindsight memory, and Live Sync.
- Run forum-topic group scenes, Director goals, structured scene state, and Light
  Novel choice mode.
- Enable session-scoped Grounded User mode to keep user abilities, success and
  NPC/world reactions tied to established story facts without removing explicit
  Persona advantages.
- Manage Characters, Optimizer proposals, models, sessions, Personas, Worlds,
  memory, Data Bank, status, and verified updates from the optional Mini App.
- Use a private provider catalog for OpenAI-compatible, native OpenAI Codex OAuth,
  Anthropic Messages, and OpenCode Muse routes without exposing provider credentials
  to Telegram clients.

For behavior and workflows, see the [User guide](docs/user-guide.md).

## Basic bot use

1. Start the bot and choose/configure a character and session.
2. Use `/start` to choose the opening greeting for a new or reset session.
3. Send normal messages to continue the conversation.
4. Use `/character`, `/session`, `/providers`, `/settings`, `/persona`, `/world`,
   `/memory`, and `/databank` to manage the active session.
5. Use `/status` for the current session/runtime summary.

Telegram `/help` is the **canonical command reference**. Use a command name for
focused detail, for example `/help scene refresh`. The repository documentation
explains concepts and workflows; `/help` reflects the executable command catalog.

## Mini App design gallery

The shipped Mini App follows this story-first layout. Names, portraits, status values,
and available controls are populated from the authenticated bridge at runtime.

<table>
  <tr>
    <th>Home</th>
    <th>Characters</th>
    <th>Manage</th>
  </tr>
  <tr>
    <td><img src="docs/assets/miniapp-concept/home.webp" alt="Mini App Home concept" width="280"></td>
    <td><img src="docs/assets/miniapp-concept/characters.webp" alt="Mini App Characters concept" width="280"></td>
    <td><img src="docs/assets/miniapp-concept/manage.webp" alt="Mini App Manage concept" width="280"></td>
  </tr>
</table>

## Documentation

| Guide | Use it for |
|---|---|
| [Installation](docs/installation.md) | Fresh install, manual install, systemd, Tailscale Funnel, upgrades |
| [Configuration](docs/configuration.md) | `.env`, paths, providers, network policy, memory/RAG/voice settings |
| [User guide](docs/user-guide.md) | Bot workflows, sessions, generation, native assets, groups, media |
| [Operations](docs/operations.md) | Reliability, privacy, signed updates, downloads, database compatibility, troubleshooting |
| [Mini App](docs/miniapp.md) | Mini App security model, pages, Funnel deployment, installer behavior |
| [Token usage](docs/token-usage.md) | Provider-reported counts, coverage, privacy, retention and migration notes |

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
