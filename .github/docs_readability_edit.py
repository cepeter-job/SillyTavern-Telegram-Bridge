"""One-off, source-pinned documentation edit; removed before PR review."""
from pathlib import Path
import hashlib
import re
import runpy
import subprocess

BASE = "39bac3de0369a7580e7d854c99622d9ac83f30de"
EXPECTED = {
    "README.md": "dfbae90bb8fc52946e78b2c3e2c766b41a62cfd7",
    "docs/installation.md": "6cc926c7fafc442f582ab0b519f6d59332914481",
    "docs/user-guide.md": "16cff4eacd6a50c45f5c2eb31e09e99f37321509",
    "docs/configuration.md": "be8c9d7010fa479cb5df026b8311eceafbaea79e",
    "docs/operations.md": "e4f3e32d6820c4e618ac0398dc161ddeb2c9777f",
    "docs/miniapp.md": "eec5c71d2aaa8265b0f7ddb94a4f80bee6dd9061",
    "docs/token-usage.md": "492522ac1164fdb9da5047c3d3b64405d2fd17b3",
    "CONTRIBUTING.md": "552faa82d71de9143c6b83d8052f5a3a7cc362a4",
}
docs = {}
for name, expected in EXPECTED.items():
    raw = Path(name).read_bytes()
    actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if actual != expected:
        raise SystemExit(f"Source changed; refusing to overwrite {name}")
    docs[name] = raw.decode("utf-8")
original = docs.copy()


def edit(name, old, new):
    if docs[name].count(old) != 1:
        raise SystemExit(f"Expected one exact passage in {name}: {old[:100]!r}")
    docs[name] = docs[name].replace(old, new, 1)


def section(name, start, end, replacement):
    text = docs[name]
    if text.count(start) != 1 or text.count(end) != 1:
        raise SystemExit(f"Section boundary changed in {name}: {start}")
    first = text.index(start)
    last = text.index(end, first + len(start))
    docs[name] = text[:first] + replacement.strip() + "\n\n" + text[last:]


# README: one clear first-run path; retain established headings and links.
edit("README.md", """The bridge reads native SillyTavern character cards, Personas, World Info and
System Prompts. It keeps Telegram conversations in separate sessions and connects
directly to your configured model provider. An optional Mini App gives you a
larger interface for managing characters, sessions and settings inside Telegram.
""", """The bridge runs on a Linux computer or VPS and connects Telegram to your model
provider. Keep that computer running; your phone is the chat interface, not the
server. You can use existing SillyTavern cards and settings, or start with the
installer's sample character. You do not need to install the SillyTavern web
frontend just to try the bridge.

Each story has its own session. The optional Mini App adds screens for managing
characters, sessions and settings inside Telegram; it is not needed for text chat.
""")
edit("README.md", "- A Linux machine with user systemd for the supported installer.", "- A Linux computer or VPS with a normal, non-root account and user systemd\n  services. Systemd keeps the bot running after setup.")
section("README.md", "## Quick start\n", "## What you can do\n", r'''## Quick start

Open a terminal on the Linux machine that will run the bot. When using a VPS,
run this in your SSH or PuTTY terminal, **not in Telegram**. Sign in as the
normal Linux user who will own the installation; do not run the installer with
`sudo` or as root.

```bash
curl --proto '=https' --tlsv1.2 --fail --location \
  https://raw.githubusercontent.com/cepeter/SillyTavern-Telegram-Bridge/main/install.sh \
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
''')
section("README.md", "## Basic bot use\n", "## Documentation\n", '''## Basic bot use

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
is active. `/settings` changes generation settings; `/narrative` changes viewpoint
and focus. Explore `/director` for planning and endings after the first exchange
works.

Telegram `/help` is the **canonical command reference**. For one action, try
`/help scene refresh` rather than reading every command at once.

`Please use /start command.` means that this session has not sent its greeting
yet. It does not mean you need to reinstall the bot. If a reply fails, return to
that session and use the [retry and recovery guide](docs/user-guide.md#retry-regenerate-or-continue).
''')
edit("README.md", "For project maintenance, see [Contributing](CONTRIBUTING.md), the", "The six guides above describe how to use and maintain the bot. Dated files under\n`docs/audits/` and `docs/superpowers/` are development records, not alternative\ninstallation instructions.\n\nFor project maintenance, see [Contributing](CONTRIBUTING.md), the")

# Installation: explain input, prerequisites, and expected outcomes.
edit("docs/installation.md", """The supported installer runs the bridge as your normal Linux user and keeps it
running through a user systemd service. Run the installer as that user, not as
root. It may ask for administrator access to install missing system packages.
""", """This guide takes you from an empty Linux account to your first Telegram reply.
Run the terminal commands on the computer or VPS that will host the bot. Your
phone or desktop Telegram app is where you will chat after installation.

Use a normal Linux account, not root. The installer creates a user systemd
service: a background service owned by that account. It may ask for administrator
access to install missing system packages, but do not put `sudo` before the
installer itself. Keep the host online while using the bot.

Already installed? Use [Updates](#updates) rather than starting over. Your
existing stories and private settings do not need to be reset.
""")
edit("docs/installation.md", """| Allowed users | Your numeric Telegram user ID; use commas for multiple IDs, not usernames. |
| Story model | A route such as `provider-one::model-a`. The first part identifies the provider; the second is its exact model ID. |
| Provider connection | Its API endpoint and credential, when the chosen transport needs one. |""", """| Allowed users | Your own numeric Telegram account ID, not an `@username`, bot token or group ID. Use commas for multiple users. |
| Story model | The exact model ID from your provider. A route such as `provider-one::model-a` combines a provider label with that model ID. |
| Provider connection | The provider's API base address and API key when required. Use its API address, not the website's dashboard URL. |""")
edit("docs/installation.md", """You do not need Hindsight, an embedding server or the Mini App to begin text
chat.""", """A bot token identifies **your bot**; an API key pays for or authorizes **model
requests**; the numeric user ID identifies **you**. They are different values.
Keep both credentials private. An ID-lookup tool does not need your bot token or
provider key. Values such as `provider-one`, `model-a` and `123456789` in these
guides are examples to replace, not a working account configuration.

You do not need Hindsight, an embedding server or the Mini App to begin text
chat.""")
edit("docs/installation.md", "Download the installer and run it in a terminal:\n", """Download the installer and run it in an interactive terminal. On a VPS, use your
SSH or PuTTY session. Keep the terminal open for the setup questions.

If the download reports `curl: command not found`, install curl and the HTTPS
certificate package first. For Debian or Ubuntu, run:

```bash
sudo apt-get update
sudo apt-get install -y curl ca-certificates
```

On another supported distribution, use its package manager for the equivalent
packages. Then download the installer. The `&&` separators below stop the next
step from running if the download or permission change fails.
""")
edit("docs/installation.md", """  -o /tmp/sillytavern-telegram-install.sh
chmod 700 /tmp/sillytavern-telegram-install.sh
/tmp/sillytavern-telegram-install.sh""", """  -o /tmp/sillytavern-telegram-install.sh &&
chmod 700 /tmp/sillytavern-telegram-install.sh &&
/tmp/sillytavern-telegram-install.sh""")
edit("docs/installation.md", """The standard trust file lives outside the checkout; see [Trust model](#trust-model).
""", """The standard trust file lives outside the checkout; see [Trust model](#trust-model).

Follow the prompts in order: select the data location when asked, enter the
missing bot settings, then enter the model connection details. Secret input is
hidden, so nothing may appear while you type a token or key. That is expected.
Review the reported paths before continuing, especially when you have several
SillyTavern installations.
""")
edit("docs/installation.md", """Edit this file for later changes. Copying `.env.example` over it would replace
your private settings.
""", """Edit this file for later changes. Copying `.env.example` over it would replace
your private settings. See [changing configuration safely](configuration.md#change-a-setting-safely)
for the edit, check and restart steps.

To resume deferred setup, run `./install.sh` from `~/sillytavern-telegram-bridge`
in an interactive terminal. Rerunning it uses the existing checkout; it does
not automatically advance that checkout to a newer release.
""")
edit("docs/installation.md", """sudo tailscale set --operator=\"$USER\"""", """sudo tailscale set --operator=\"bridge-user\"""")
edit("docs/installation.md", """That grants local operator access, not Funnel authorization.""", """Replace `bridge-user` with the Linux username that runs the bridge, not the
administrator's username. That grants local operator access, not Funnel authorization.""")
edit("docs/installation.md", """The configuration check should finish successfully and systemd should report
`active (running)`.""", """The configuration check should finish without an error, and systemd should show
`active (running)`. If the status or log opens a scrollable view, press `q` to
return to the terminal. If the check fails, correct the named setting before
starting or restarting the service; do not reset your database to fix a typo.""")
edit("docs/installation.md", """restart, stop and lingering checks.
""", """restart, stop and lingering checks. Here, **lingering** means that Linux keeps
your user services running after your terminal or SSH session closes.
""")

# Workflow guide: define the terms where readers first encounter them.
edit("docs/user-guide.md", """Start with a normal conversation. Add memory, images, voice or group controls
when they solve a problem for your story.
""", """Start with [Your first conversation](#your-first-conversation). You can leave
optional features alone until you have exchanged a few messages with a character.
The remaining sections are references to return to as you need them, not a setup
checklist you must finish before chatting.

Commands beginning with `/` go in Telegram. Terminal commands in the installation
and operations guides run on the Linux host instead.
""")
edit("docs/user-guide.md", "- [Director Room](#director-room)\n", "- [Director Room](#director-room)\n- [Closed stories and alternate endings](#closed-story-and-the-epilogue)\n")
edit("docs/user-guide.md", """After [installing the bridge](installation.md), open your bot's private chat.
""", """After [installing the bridge](installation.md), open your bot's private chat.
For this first test, use **Player-centric**, **Normal** and the configured Story
model. You can explore other styles and helper models afterward.
""")
edit("docs/user-guide.md", """Setup is a draft until you tap Apply.""", """You should now see the character's opening message. Try a short reply such as:

```text
*I pause at the doorway.* "Were you expecting someone?"
```

If the character answers, the basic setup works. Use `/status` to check the
active session and `/session` when you need to return to it later. If no answer
arrives, check [Troubleshooting](operations.md#troubleshooting) before sending
several retries.

Setup is a draft until you tap Apply.""")
section("docs/user-guide.md", "### A few terms\n", "## Everyday controls\n", '''### A few terms

| Term | What it means here |
|---|---|
| **Character** | A card describing a character or cast for the model to play. |
| **Persona** | The identity and description you use for your own role in the story. |
| **World Info / lorebook** | A collection of setting or character facts that can be added to the prompt. |
| **System Prompt** | Instructions that guide the model's responses. |
| **Session** | One story, with its own message history and selected settings. |
| **Provider** | The service the bridge contacts to run a model. |
| **Route** | A provider and model together, written as `provider-id::model-id`. |
| **Story model** | The model that writes the roleplay. |
| **Utility model** | The model used for summaries, some choice strategies and other helpers. |
| **Director model** | The model that plans scene direction and endings, rather than writing the ordinary reply. |
| **Inherit** | Use the model selected for another role instead of choosing a separate one. Utility can inherit Story; Director can inherit Utility. |
| **Context window** | How much text a model can handle in one request, measured in tokens. It is not the size of your saved story. |
| **Native data** | SillyTavern's existing cards, lorebooks, prompts and Persona files. Edits to these files can affect other stories that use them. |
| **Thread / viewpoint** | A thread is a storyline being followed; a viewpoint is the character or perspective through which a scene is told. |

You do not have to memorize these terms. The relevant command or page is linked
from each workflow below.
''')
edit("docs/user-guide.md", """Most commands open a panel. Tap its buttons to make a choice. If it asks for
text, your next message completes that step; `/cancel` backs out. If a panel has
expired or belongs to a previous session, reopen the command.
""", """Most commands open a panel with buttons. Tap once and let the action finish.
When a panel asks for text, your next message is treated as that answer rather
than story dialogue; send `/cancel` to back out first.

An expired panel is usually just an old set of buttons. Reopen the command to
get controls for the current session. Switching sessions does not make an old
confirmation apply to the new story.
""")
edit("docs/user-guide.md", """A crash between Telegram accepting a message and the bridge recording the
acknowledgement can leave delivery uncertain. Recovery reduces duplicate delivery;
it cannot guarantee that every message is delivered exactly once.
""", """Occasionally Telegram may receive a message just before the bridge crashes,
leaving the bridge unsure whether it arrived. Recovery can then produce a
duplicate. Check the chat before retrying again; the bridge cannot promise
exactly-once delivery through every interruption.
""")
edit("docs/user-guide.md", """memory. It keeps the session and selected Character, mode, Persona, World,
System Prompt and Narrative Style, then returns it to an unstarted state. Send `/start` again.
""", """memory. It keeps the session and selected Character, mode, Persona, World,
System Prompt and Narrative Style, then waits for a new opening greeting. Send
`/start` again. Do not use Reset just because a reply or choice panel failed;
try the matching recovery action first.
""")
edit("docs/user-guide.md", """plans the next scene. An unset Director model inherits Utility, then Story. Both Story and Utility reasoning are configured from `/providers`. Director adds
its own independent reasoning control there and in Director Room.
""", """plans the next scene. You can start with one model for all three roles: leave
Utility and Director inherited rather than choosing a separate model for each.
An unset Director model inherits Utility, then Story.

Both Story and Utility reasoning are configured from `/providers`. Director has
its own reasoning control there and in Director Room. Reasoning is the model's
additional thinking work; higher budgets can increase waiting time and token use.
""")
edit("docs/user-guide.md", """The room shows the current scene, viewpoint, thread, accepted direction and recent
decisions.""", """The room shows the current scene, viewpoint, storyline (thread), accepted
direction and recent decisions. You can read the plan without turning it into
something that has happened in the story.""")
edit("docs/user-guide.md", """Once its actual resolution is committed and
reconciled, the Director prepares a brief""", """Once the resolution has been saved and
the bridge has updated its story record, the Director prepares a brief""")
edit("docs/user-guide.md", """External memory is isolated by the new session's tags and document IDs inside the
chat's Hindsight bank. Seeding failure does not point the branch at the original's
memories: the copied transcript and local continuity remain usable, and the UI
reports degraded external memory. A later ordinary memory retain uses the new
session's identity.
""", """The new session keeps its external memories separate from the original ending.
If Hindsight cannot receive the copied history, the app reports a memory warning.
Your new local story is still there: you can use its copied transcript and local
continuity without borrowing memories from the original finale. Later memory
updates use the new session's identity.
""")
edit("docs/user-guide.md", """`/status` shows your style, point of view, current scene/thread and whether the
narrative record is current or stale. That record describes committed story
facts, not proposed future events.""", """`/status` shows your style, point of view and current scene/thread. A **stale**
narrative record means the helper record has not yet caught up with the latest
saved messages or settings; it does not mean your messages were lost. The record
describes saved story facts, not proposed future events.""")
edit("docs/user-guide.md", """physical surroundings and continuity. Background reconciliation uses the
Utility model""", """physical surroundings and continuity. Updating the narrative record in the
background is called reconciliation. It uses the Utility model""")
edit("docs/user-guide.md", """If generation fails, **Retry Choices** repairs only the choices. It does not
regenerate the saved story. `/lightnovel` restores a missing panel.
""", """If the story arrived but the choices did not, tap **Retry Choices** once. It
requests choices for the saved story; it does not rewrite that story. Reopen
`/lightnovel` when the panel itself is missing. You can also type your own reply
instead of waiting for choices. A retry can still consume model tokens.
""")
edit("docs/user-guide.md", """S–D rank badges are model opinions, not objective quality scores. Ranking and
optimization send card text to the Utility provider and can use tokens. Applied
changes request a new rank; an unavailable rank leaves the card usable. The
[rank asset manifest](../assets/character-ranks/README.md) holds asset provenance.
""", """S–D rank badges are model opinions, not objective quality scores. Ranking and
optimization send card text to the Utility provider and can use tokens. Applied
changes request a new rank; an unavailable rank leaves the card usable.

New ranks are tied to the card's content, so copying unchanged bytes to another
data directory does not by itself remove the rank. Older records used a file's
location and filesystem details. The bridge can upgrade those records only when
the original recorded file still proves that the card is unchanged. If that
original is gone, rerank the current card rather than editing the database to
force a stale badge back into view. The
[rank asset manifest](../assets/character-ranks/README.md) records the badge artwork.
""")
# Keep the reading order consistent with the contents list.
style_start = docs["docs/user-guide.md"].index("## Narrative Style\n")
style_end = docs["docs/user-guide.md"].index("## Light Novel mode\n", style_start)
style = docs["docs/user-guide.md"][style_start:style_end]
docs["docs/user-guide.md"] = docs["docs/user-guide.md"][:style_start] + docs["docs/user-guide.md"][style_end:]
edit("docs/user-guide.md", "## Director Room\n", style + "## Director Room\n")

# Configuration: add a usable path through the reference, not a second catalog.
edit("docs/configuration.md", """For a guided installation, edit the private files the installer created. You
usually need only the bot settings and one provider to start. The reference
tables below cover optional services and advanced controls.
""", """Use this guide when you need to change a setting or connect a provider. A guided
installation already creates your private configuration; you do not need to
copy every example below or fill in every optional setting.

There are two files to understand. **`.env`** holds bot settings, paths and
credentials. The **provider YAML catalog** lists the services and models you can
choose. The catalog refers to a credential by its environment-variable name;
the actual key stays in `.env`.

For a first provider, read [Minimum settings](#minimum-required-configuration)
and the [provider example](#provider-catalog). For a working installation, start
with [Change a setting safely](#change-a-setting-safely), then look up just the
setting you need. The larger tables remain a full reference.
""")
edit("docs/configuration.md", "- [Minimum settings](#minimum-required-configuration)\n", "- [Change a setting safely](#change-a-setting-safely)\n- [Minimum settings](#minimum-required-configuration)\n")
edit("docs/configuration.md", "For a manual/custom installation only, create it from the maintained example without overwriting an existing private file:\n", """For a manual/custom installation only, create it from the maintained example
without overwriting an existing private file. Run the following block from the
bridge checkout; change the `cd` path if yours is different.
""")
edit("docs/configuration.md", """env=\"$HOME/.local/share/sillytavern-telegram/.env\"
""", """cd ~/sillytavern-telegram-bridge
env=\"$HOME/.local/share/sillytavern-telegram/.env\"
""")
edit("docs/configuration.md", "### Minimum required configuration\n", '''### Change a setting safely

1. Open the existing private `.env` or provider catalog in your text editor.
   Change only the setting you need. Keep a private copy before a larger edit.
2. Save the file. Put comments on their own lines, and do not define the same
   `.env` key twice. **Do not run `source .env`**: the bridge reads this file
   itself; it is not a shell script.
3. From the installed checkout, check the configuration:

   ```bash
   cd ~/sillytavern-telegram-bridge
   ./.venv/bin/python sillytavern_telegram_bridge.py --check
   ```

4. If the check succeeds, restart and inspect the service:

   ```bash
   systemctl --user restart sillytavern-telegram.service
   systemctl --user status sillytavern-telegram.service --no-pager
   ```

Correct a reported error before restarting. A successful configuration check
validates the local setup; it does not prove that your provider account has
credit or can run the selected model. Send a short Telegram reply to test that.

In the tables, `$SILLYTAVERN_BRIDGE_HOME` and similar expressions describe how a
default path is derived. For custom paths in `.env`, use a full filesystem path
rather than assuming that shell variables will be expanded there.

### Minimum required configuration
''')
edit("docs/configuration.md", "The bridge validates these values before polling Telegram:\n", """These four settings identify your bot, allowed users, starting character and
starting model. Replace every example value. They are only the bot-side part of
the setup: the matching provider entry, its credential and its allowed hostname
must also be configured as shown in [Provider catalog](#provider-catalog).
""")
edit("docs/configuration.md", """### Environment variable reference
""", """### Environment variable reference

Leave optional values at their defaults unless you need that feature. A default
URL for Hindsight, embeddings or voice does not install or start that service.
The tables describe available settings, not a list of required dependencies.
""")
edit("docs/configuration.md", """The bridge uses its own **private YAML provider catalog**; it does not import SillyTavern provider credentials/settings. For a manual/custom setup, start from the maintained example without overwriting an existing private catalog:
""", """The bridge uses its own **private YAML provider catalog**. It does not copy
provider credentials or connection settings from the SillyTavern web frontend.

For a manual/custom setup, start from the maintained example below. Run it from
the bridge checkout. It stops rather than replacing an existing private catalog.
""")
edit("docs/configuration.md", """catalog=\"$HOME/.local/share/sillytavern-telegram/sillytavern_telegram_providers.yaml\"
""", """cd ~/sillytavern-telegram-bridge
catalog=\"$HOME/.local/share/sillytavern-telegram/sillytavern_telegram_providers.yaml\"
""")
edit("docs/configuration.md", """Then place the referenced credential in your private environment file:
""", """This is a template, not a live endpoint. Replace `https://provider.example/v1`
with the API base address supplied by your provider, and `provider-one/model-a`
with its exact model ID. Keep indentation as shown and use spaces, not tabs.
`provider-one` is your local provider label; it is also the part before `::` in
`SILLYTAVERN_MODEL`.

Then add or update the matching credential and hostname in your existing private
`.env` file. Do not append a second copy of a key that is already present:
""")
edit("docs/configuration.md", """SILLYTAVERN_PROVIDER_ALLOWED_HOSTS=provider.example
```

### Native OpenAI Codex OAuth""", """SILLYTAVERN_PROVIDER_ALLOWED_HOSTS=provider.example
```

`api_key_env: PROVIDER_ONE_API_KEY` names the variable to read; it is not the key
itself. The hostname entry contains only the host, such as `provider.example`,
not `https://provider.example/v1`. When adding a provider, preserve any other
hosts you still use in the comma-separated allowlist. Finish with the
[check and restart steps](#change-a-setting-safely), then open `/providers` in
Telegram and try the configured Story model.

### Native OpenAI Codex OAuth""")
edit("docs/configuration.md", """python sillytavern_telegram_bridge.py --codex-login
python sillytavern_telegram_bridge.py --codex-status""", """cd ~/sillytavern-telegram-bridge
./.venv/bin/python sillytavern_telegram_bridge.py --codex-login
./.venv/bin/python sillytavern_telegram_bridge.py --codex-status""")
edit("docs/configuration.md", """The YAML catalog describes **where** to call; it does not grant network trust.
External endpoints must also be present in the corresponding environment
allowlist. An empty external-host list is fail-closed.
""", """The YAML catalog says **where** the provider is. The environment allowlist says
**which hosts the bridge may contact**. Both must agree. An empty external-host
list blocks external requests; it does not mean that every host is allowed.
""")
edit("docs/configuration.md", """## Provider runtime health and fallback

""", """## Provider runtime health and fallback

A cooldown is a temporary pause after errors. It helps avoid sending the same
failing request repeatedly. A fallback is a different model tried when the
selected provider cannot complete an eligible request. These are different from
refreshing the model list, which only updates the choices shown in the picker.

""")

# Operations: recovery first; preserve the detailed signing and backup safeguards.
edit("docs/operations.md", """Run these commands as the Linux user who owns the bridge. The default checkout
is `~/sillytavern-telegram-bridge`; use your chosen location if it differs.
""", """Use this guide to check the bot, update it or recover a saved story. Terminal
commands run on the Linux host as the user who installed the bridge, not inside
Telegram and not in a root shell. The default checkout is
`~/sillytavern-telegram-bridge`; adjust that path for a custom installation.

For a failed reply, try the matching [recovery action](user-guide.md#retry-regenerate-or-continue)
first. Reinstalling, resetting the story or restoring an older database is not
the first step for a provider timeout.
""")
edit("docs/operations.md", "- [Backup and restore](#database-migrations-backup-and-restore)\n", "- [Backup and restore](#database-migrations-backup-and-restore)\n- [Closed-story recovery](#closed-story-recovery)\n- [Alternate-ending recovery](#alternate-ending-recovery)\n")
edit("docs/operations.md", "Check configuration and recent service activity:\n", """Check the local configuration, service status and recent log in that order:
""")
edit("docs/operations.md", """systemctl --user status sillytavern-telegram.service
journalctl --user -u sillytavern-telegram.service -n 100 --no-pager""", """systemctl --user status sillytavern-telegram.service --no-pager
journalctl --user -u sillytavern-telegram.service -n 100 --no-pager""")
edit("docs/operations.md", "Restart after editing `.env`:\n", """`--check` validates local configuration. `active (running)` means the service
process is up; it does not prove that the provider can answer. The log is the
place to look for the actual error. If a command opens a scrollable view, press
`q` to return to the terminal.

After a configuration edit passes `--check`, restart to load it:
""")
edit("docs/operations.md", """If it reports `Linger=no`, an administrator can enable it with
`sudo loginctl enable-linger \"$USER\"`. The installer also offers lingering setup.
""", """`Linger=yes` means the user service may keep running after you log out.
If it reports `Linger=no`, run `sudo loginctl enable-linger \"$USER\"` from the
bridge user's terminal, or ask an administrator to enable it for that username.
Do not substitute the root account. The installer also offers lingering setup.
""")
edit("docs/operations.md", """### Memory OOM diagnostics

Memory diagnostics""", """### Memory OOM diagnostics

OOM means out of memory. RSS is the amount of physical RAM currently attributed
to the process. These diagnostics help explain memory growth; they do not add
RAM or impose a new memory limit.

Memory diagnostics""")
edit("docs/operations.md", """The repository retains only the latest GitHub release and tag. Earlier release
history remains in [CHANGELOG.md](../CHANGELOG.md) and Git history; keep local
backups needed for recovery.
""", """Choose a published release and read its notes before updating. A tag or commit
on `main` is not by itself a published release. Release archives contain code,
not your private settings or stories; keep your own backups for recovery.
[CHANGELOG.md](../CHANGELOG.md) records the release history.
""")
edit("docs/operations.md", """confirm installation. The updater requires:
""", """confirm installation. Let the update finish before requesting it again. Then
check the running version and send a short test message. You normally do not
need to run Git commands or edit the trust file for each release.

**Running** is the code loaded by the current process; **Installed** describes
the files on disk. If only Installed has changed, investigate the restart rather
than assuming the bot is already using the update.

The checks below explain why the updater may refuse a request. Do not bypass
them to force an update. The updater requires:
""")
section("docs/operations.md", "### Manual update\n", "### Database migrations, backup and restore\n", r'''### Manual update

Use this when `/update` reports changed runtime dependencies, or when you need
to install a specific reviewed release. This procedure is for an
installer-managed **Git checkout**. Back up the database, private configuration
and native SillyTavern data first; see [Backup and restore](#database-migrations-backup-and-restore).

Open the [releases page](https://github.com/cepeter/SillyTavern-Telegram-Bridge/releases),
read the notes, and copy the exact tag of the release you chose. The terminal
block below asks for that tag rather than guessing from the highest tag in Git.
Run it from an interactive Bash terminal as the bridge user. Check the two paths
before running it if you use a custom checkout or trust file.

```bash
set -euo pipefail
cd ~/sillytavern-telegram-bridge
read -r -p "Paste the reviewed release tag, including v: " tag
[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Expected a version tag such as v0.3.002" >&2; exit 1; }
git switch main
test -z "$(git status --porcelain)" || { echo "Source checkout has local changes; save and review them first" >&2; exit 1; }

signers="$HOME/.config/sillytavern-telegram/trusted-maintainers"
git fetch origin "refs/tags/$tag:refs/tags/$tag"
git -c gpg.format=ssh \
  -c "gpg.ssh.allowedSignersFile=$signers" \
  -c gpg.minTrustLevel=fully verify-tag "$tag"
git merge-base --is-ancestor HEAD "$tag^{commit}" || { echo "This is not a forward update; stop and review the checkout and backup" >&2; exit 1; }

./.venv/bin/python sillytavern_telegram_bridge.py --backup-database
systemctl --user stop sillytavern-telegram.service
git checkout -B main "$tag^{commit}"
./install.sh --no-start
./.venv/bin/python sillytavern_telegram_bridge.py --check
systemctl --user daemon-reload
systemctl --user start sillytavern-telegram.service
systemctl --user status sillytavern-telegram.service --no-pager
```

The signature and forward-update checks happen before the service stops or the
checkout moves. Existing tags are not pruned or force-replaced. A dirty checkout
or a failed signature needs investigation, not a `git reset --hard` workaround.
Do not substitute `git pull origin main`; it may include unpublished changes.

If a command fails after the stop, leave the service stopped while you inspect
the error. Once the configuration check succeeds, start the service and verify
its running revision and one Telegram exchange. A downgrade across database
schema changes needs the matching pre-upgrade snapshot; the block deliberately
refuses to guess that recovery path.

For a release ZIP, verify the checksum and signed-release authenticity, extract
to a fresh directory, install locked dependencies, run `--check`, and point your
service at the new directory. Do not overlay an older installation. An archive
install does not support automatic Git-based `/update`.
''')
edit("docs/operations.md", """### Database migrations, backup and restore

New releases""", """### Database migrations, backup and restore

A backup preserves your current data; a restore replaces it with an older saved
copy. Restoring is for recovery, not ordinary troubleshooting. Messages and
settings created after the chosen snapshot will not be present in that restored
copy, so check its date and keep the current database too.

New releases""")
section("docs/operations.md", "## Closed-story recovery\n", "## Alternate-ending recovery\n", '''## Closed-story recovery

Open Director Room to see whether the ending is waiting for generation, already
saved, or only missing from Telegram. After a provider error, use `/retry` or
**Recover saved ending**. Check the result before submitting another recovery.
A failed model call does not start an unlimited automatic retry loop.

The bridge resumes the unfinished step. It does not write a second epilogue just
because Telegram failed to deliver the first one. Read-only recovery does not
generate new expressions, speech or images, and a completed original stays closed.

For operators reading logs, `RESOLUTION_COMMITTED` means epilogue preparation can
resume. A committed epilogue needs its story record updated and closure completed,
not another Story call. `CLOSED` needs only missing delivery completed. Startup
and periodic scans schedule this work after services are ready; database
migrations never call a provider. A per-chat lock and expiring work lease prevent
workers from claiming the same recovery at once. Database guards reject stale
queued writes to a completed original.

Keep a database backup before an upgrade. Recovery tests cover transaction
rollback and simulated restarts, not physical host power-loss behavior.
''')
edit("docs/operations.md", """## Alternate-ending recovery

Alternate Ending has one durable request identity and a deterministic target
session. Creating the target, copying the checkpoint prefix and restoring local
state is one transaction. A failed local copy leaves no half-created session;
restart recovery resumes the same admitted request.

Optional Hindsight seeding runs afterward under a reclaimable lease, using only
the target's deterministic conversation document and strict session tag. A normal
seeding error completes the branch as degraded instead of retrying indefinitely.
A process interruption can resume after the lease expires without creating
another target. An applied request retains a small provenance receipt; its
transient operation payload is discarded. Source deletion after the local copy
does not delete the independent target.

Source message IDs, Telegram delivery state, queued jobs, response variants and
callback records are not reused by the target. Snapshot references are remapped
to its new transcript identities. Prefix copying streams rows and keeps only the
bounded set of references required by the checkpoint in memory.""", """## Alternate-ending recovery

Check the session list and the operation result before requesting another
Alternate Ending. Retrying the same operation returns the same new session;
making a later, separate request can create another branch. The original ending
stays closed.

A Hindsight warning means the copied history could not be added to external
memory. It does not mean the local story was lost. The new session can still use
its copied messages and local continuity, and it does not borrow the original
finale's memories. See [Try an alternate ending](user-guide.md#try-an-alternate-ending)
for the normal workflow.

For operators, the new session, checkpoint messages and local state are created
in one database transaction. A failed local copy leaves no half-created session.
Restart recovery uses the same durable request identity. Optional Hindsight
seeding follows under an expiring lease and uses only the new session's document
and tags. A normal seeding error completes with a warning rather than retrying
forever; interrupted work can resume after the lease expires.

The new session receives its own message IDs and work records. It does not reuse
source Telegram delivery state, jobs, response variants or callbacks. Once the
local copy is complete, deleting the source does not delete the new session.
Copying reads rows incrementally and retains only the checkpoint references it
needs in memory.""")

# Mini App: task navigation and plain explanations of recovery states.
edit("docs/miniapp.md", """to browse cards, edit settings or review usage.
""", """to browse cards, edit settings or review usage.

Already configured? Start with [Open the Mini App](#open-the-mini-app) and
[Find what you need](#find-what-you-need). To enable it for the first time, go to
[Setup with Tailscale Funnel](#setup-with-tailscale-funnel). A problem opening a
form or completing a task is covered in [Troubleshooting](#troubleshooting).
""")
edit("docs/miniapp.md", """A copied browser URL is not a separate login. If the session expires, reopen the
app from Telegram. The default signed-launch lifetime is one hour. Main Mini App
profile/deep links also need BotFather configuration.
""", """You should see Home with the current story and bridge status. A copied browser
URL is not a separate login: Telegram supplies the login information when you
launch the app. If it expires, close the app and reopen it from the bot chat.
The default launch lifetime is one hour. Opening through a main Mini App profile
or deep link also needs the corresponding BotFather configuration.

If there is no Bridge menu, check that Mini App setup finished and the service
restarted. Sending ordinary messages to the bot still works without this menu.
""")
edit("docs/miniapp.md", """The Optimizer uses Utility to prepare an original/proposed preview. You can add
a Manual suggestion, review changes, then **Apply** or **Discard**. Apply checks
the original card revision and consumes the proposal once. An existing-filename
upload also needs a replacement preview. Active/default/referenced cards cannot
be deleted, and restores check the reviewed revision.
""", """The Optimizer asks the Utility model to prepare a preview. Compare the original
and proposed fields, add a Manual suggestion when needed, then choose **Apply**
or **Discard**. Until you apply it, the installed card is unchanged. If the card
changed while you were reviewing, prepare a fresh preview instead of forcing the
old one through. Optimizing and ranking can consume model tokens.

Uploading a filename that already exists also requires a replacement review.
Cards used by active sessions, defaults or other references cannot be deleted;
a restore checks that the file still matches the version you reviewed.
""")
edit("docs/miniapp.md", """Long tasks have an operation ID and a queued/running/completed outcome under
**System → Operations**. Identical retries reuse an admitted operation. Check its
status before submitting another task. After a restart, interrupted work is
marked for review rather than silently replayed.
""", """For a task that takes time, open **System → Operations** before submitting it
again. Each task has an operation ID so you can find the same request:

| State | What to do |
|---|---|
| **Queued** | The request is waiting for a worker. Do not submit another copy. |
| **Running** | Work has started. Check this operation for the result. |
| **Completed** | Open its result; an optimizer preview can be reviewed from here. |
| **Interrupted** | The process restarted. Inspect the result before retrying because part of the action may already have happened. |

Identical retries reuse the accepted operation. Interrupted work is marked for
review rather than silently started over.
""")
edit("docs/miniapp.md", """An administrator can grant local daemon access with
`sudo tailscale set --operator=\"$USER\"`. This does not grant Funnel policy
authorization. Run the bridge installer as the bridge user.
""", """An administrator can grant local daemon access with
`sudo tailscale set --operator=\"bridge-user\"`. Replace `bridge-user` with the
Linux username running the bridge. This does not grant Funnel policy
authorization. Run the bridge installer as that user, not as the administrator.
""")
edit("docs/miniapp.md", """Funnel is public; authentication comes from Telegram, not membership in your
tailnet.""", """Funnel makes the HTTPS address reachable from the public internet. Telegram's
signed login and the allowed-user list still protect the app; being on your
private Tailscale network is not the app's login mechanism.""")
edit("docs/miniapp.md", """Choose **Manage → Director Room** to inspect hidden scene plans, current viewpoint,
threads and recent decisions.""", """Choose **Manage → Director Room** to read scene plans, the current viewpoint,
storylines and recent decisions.""")
edit("docs/miniapp.md", """The action runs through the existing actor-bound jobs
queue. Retrying the same operation retrieves its result instead of creating a
second branch;""", """The app tracks this as an operation belonging to you and the original session.
Retrying that operation retrieves its result instead of creating a second branch;""")
# Keep everyday Director controls above operator setup and design artwork.
start = docs["docs/miniapp.md"].index("## Director Room\n")
director = docs["docs/miniapp.md"][start:]
docs["docs/miniapp.md"] = docs["docs/miniapp.md"][:start]
edit("docs/miniapp.md", "## Setup with Tailscale Funnel\n", director.rstrip() + "\n\n## Setup with Tailscale Funnel\n")

# Usage: one concrete example before transport/accounting details.
edit("docs/token-usage.md", """Refresh after a request completes to see new activity.
""", """Refresh after a request completes to see new activity.

This page helps you understand which models and helper tasks are using tokens.
A token is a unit of text used by a model; it is not always a whole word. The
tracker shows what providers reported to the bridge, not a bill or your remaining
subscription allowance. Use your provider's account page for billing.
""")
edit("docs/token-usage.md", """## What is covered
""", """### A worked example

Suppose a provider reports **1,000 input tokens**, including **600 cached input
tokens**, and **200 output tokens**, including **50 reasoning tokens**. The total
is **1,200**, not 1,850. Cached input is already inside Input, and reasoning is
already inside Output. This example explains the counters; it does not imply a
price or that every provider reports both details.

If output usage is missing, a dash means **unknown**, not free. A partially
reported request can contribute its known input count while still lowering
Fully reported coverage. Do not compare a partially reported total with an
invoice as though every token had been counted.

### Why a short reply can use many tokens

Input can include the character card, instructions, lore and earlier messages,
not just the message you typed. Choices, summaries, image-prompt preparation and
Director work can add their own requests. Use the task breakdown and
[model-call guide](user-guide.md#model-calls-and-token-use) to see which feature
is responsible before changing settings.

## What is covered
""")
edit("docs/token-usage.md", """Tracking starts only after a bridge version containing the usage ledger is installed and restarted. Existing conversations do not contain historical provider usage, so there is no backfill.
""", """Tracking starts after a bridge version with usage tracking is installed and
restarted. Old messages do not contain the provider's original counters, so the
bridge cannot reconstruct historical usage from them.
""")
edit("docs/token-usage.md", """Canonical planning is recorded under `director`; committed-story reconciliation
uses `director_reconcile`.""", """Scene planning appears under `director`. Updating the helper record to match
saved story messages appears under `director_reconcile`.""")
edit("docs/token-usage.md", """The SQLite ledger stores session identity, model, task, completion time, status,
duration and optional numeric counters.""", """The local SQLite database stores a usage record with the session, model, task,
completion time, status, duration and any available numeric counters.""")

# Preserve the contributor reference; add a repeatable editorial standard.
edit("CONTRIBUTING.md", """Verify behavior against source and the executable command catalog.""", """For each procedure, tell the reader where to run it, what they need beforehand,
what to enter and what success looks like. Put recovery instructions next to
steps that can fail. Mark examples and placeholders explicitly. Prefer one
recommended starting path to several competing quick starts, and keep destructive
actions visibly separate from routine troubleshooting.

Use plain explanations before internal terms: for example, say \"saved reply\"
before discussing a committed delivery, and explain that a stale form needs a
fresh review. Keep real button labels, error messages, limits and safety warnings
exact. A more natural tone must not weaken an important restriction or imply a
feature is available when it is not.

Verify behavior against source and the executable command catalog.""")

# Check every intended file changed, headings still resolve, and examples parse.
for name, text in docs.items():
    if text == original[name]:
        raise SystemExit(f"No edit made to intended document: {name}")
    if len(re.findall(r"^```", text, re.M)) % 2:
        raise SystemExit(f"Unbalanced code fence: {name}")
    Path(name).write_text(text, encoding="utf-8")
    for code in re.findall(r"```bash\n(.*?)\n```", text, re.S):
        subprocess.run(["bash", "-n"], input=code, text=True, check=True)

# Preserve the whole configuration reference: no supported table row is removed.
old_rows = {line for line in original["docs/configuration.md"].splitlines() if line.startswith("| `")}
new_rows = set(docs["docs/configuration.md"].splitlines())
if not old_rows <= new_rows:
    raise SystemExit("A configuration reference row was lost")

checks = runpy.run_path("tests/test_installation_docs.py")
count = 0
for name, check in checks.items():
    if name.startswith("test_") and callable(check):
        check()
        count += 1
print(f"Existing installation-documentation checks: {count} passed")

# Additional documentation-only safety checks; no example commands are executed.
manual = docs["docs/operations.md"].split("### Manual update\n", 1)[1].split("### Database migrations", 1)[0]
assert 'read -r -p "Paste the reviewed release tag, including v: " tag' in manual
assert '--prune-tags' not in manual
assert manual.index('verify-tag "$tag"') < manual.index('systemctl --user stop')
assert 'git merge-base --is-ancestor HEAD "$tag^{commit}"' in manual
assert '--operator=\"$USER\"' not in docs["docs/installation.md"]
assert '--operator=\"$USER\"' not in docs["docs/miniapp.md"]
assert len(docs["README.md"].splitlines()) <= 260
print("Reviewed-tag, reference-preservation, operator-identity and Bash syntax checks passed")
subprocess.run(["git", "diff", "--check"], check=True)
subprocess.run(["git", "diff", "--stat"], check=True)
print("Changed documents: " + ", ".join(sorted(docs)))
