# Installation

[Back to README](../README.md) · [First conversation](user-guide.md#your-first-conversation) ·
[Configuration](configuration.md) · [Operations](operations.md)

The supported installer runs the bridge as your normal Linux user and keeps it
running through a user systemd service. Run the installer as that user, not as
root. It may ask for administrator access to install missing system packages.

## Before you start

Have these ready:

| Item | What to enter |
|---|---|
| Telegram bot token | The token for your bot from [BotFather](https://t.me/BotFather). |
| Allowed users | Your numeric Telegram user ID; use commas for multiple IDs, not usernames. |
| Story model | A route such as `provider-one::model-a`. The first part identifies the provider; the second is its exact model ID. |
| Provider connection | Its API endpoint and credential, when the chosen transport needs one. |
| Existing SillyTavern data | The application location and native user to use, if you already have an installation. |

You do not need Hindsight, an embedding server or the Mini App to begin text
chat. If there is no SillyTavern installation, the installer prepares isolated
starter data and a PNG card. It does not install the SillyTavern web frontend.

Supported system package managers are `apt-get` (Debian/Ubuntu), `dnf` or `yum`
(Fedora/RHEL family), `pacman` (Arch family), and `zypper` (SUSE family).

## Guided install

Download the installer and run it in a terminal:

```bash
curl --proto '=https' --tlsv1.2 --fail --location \
  https://raw.githubusercontent.com/cepeter/SillyTavern-Telegram-Bridge/main/install.sh \
  -o /tmp/sillytavern-telegram-install.sh
chmod 700 /tmp/sillytavern-telegram-install.sh
/tmp/sillytavern-telegram-install.sh
```

With no arguments, it offers:

| Choice | Use it when… |
|---|---|
| **Standard install** | You want the normal bot setup. Start here. |
| **Install + Tailscale Mini App** | You also want the management interface and already have Tailscale configured. |
| **Prepare only, do not start** | You want files and dependencies ready without starting the service. |
| **Repair/reinstall dependencies** | You need to repair the bridge's Python environment. |
| **Advanced options** | You need custom paths or installation controls. |

### Standard install

The wizard installs missing prerequisites, prepares Python 3.11+ with locked
dependencies, verifies the newest signed release and creates the user service.
It keeps a Git checkout on local branch `main` so signed `/update` can work later.
The standard trust file lives outside the checkout; see [Trust model](#trust-model).

It asks only for missing required settings and hides secret input. Existing
private `.env` values and custom service units are preserved. Choose
**Configure later** to leave incomplete configuration in place with the service
stopped; rerun the installer when you have the missing values.

Keep the generated file at:

```text
~/.local/share/sillytavern-telegram/.env
```

Edit this file for later changes. Copying `.env.example` over it would replace
your private settings.

### Finding your SillyTavern data

An existing `SILLYTAVERN_DIR` setting takes priority. Otherwise, the wizard checks
common locations and a bounded set of folders in your home directory. It reads
SillyTavern's `config.yaml` and `dataRoot`, then asks which installation/native
user to use if there is more than one.

The selected user's character, World Info, System Prompt, Persona settings and
avatar paths are recorded explicitly. An existing PNG card can become the initial
default. The wizard reports Node/npm dependency status for SillyTavern, but it
leaves SillyTavern's packages and Node installation alone.

### Provider and context setup

For a new OpenAI-compatible route, enter the model, endpoint and API key. The
wizard adds the endpoint hostname to `SILLYTAVERN_PROVIDER_ALLOWED_HOSTS` for you.
An existing provider catalog is preserved.

The installer does not ask you to guess a context-window size. A generated
catalog pins the chosen model list with `discover_models: false` and enables
`discover_model_metadata: true` to learn published context sizes for those models.
If metadata is unavailable, the configured fallback is used, 32K by default.
For a model with a smaller limit or missing metadata, set its explicit value in
[context configuration](configuration.md#context-planning-and-diagnostics).

### Install + Tailscale Mini App

This uses the same setup and adds a public HTTPS endpoint through Tailscale
Funnel. Tailscale 1.52+ must already be installed and signed in. MagicDNS, HTTPS
and Funnel authorization also need to be available for the device.

If the bridge user needs permission to manage the local daemon, an administrator
can run:

```bash
sudo tailscale set --operator="$USER"
```

That grants local operator access, not Funnel authorization. The Mini App still
requires a signed Telegram launch and an allowed Telegram user ID. Follow the
[Mini App setup guide](miniapp.md#setup-with-tailscale-funnel) for listener handling
and troubleshooting.

## Verify the service

From the installed checkout:

```bash
cd ~/sillytavern-telegram-bridge
./.venv/bin/python sillytavern_telegram_bridge.py --check
systemctl --user status sillytavern-telegram.service
journalctl --user -u sillytavern-telegram.service -n 80 --no-pager
```

The configuration check should finish successfully and systemd should report
`active (running)`. Then open the bot and follow the
[first conversation walkthrough](user-guide.md#your-first-conversation).
A running process alone does not verify your model account: send a short message
after `/start` to check the complete path.

The installer enables the user service and can enable lingering so it continues
after you log out. See [service controls](operations.md#service-controls) for
restart, stop and lingering checks.

## Updates

Use `/update` for normal releases. It verifies the signed tag and requires a clean
checkout on `main`. If runtime dependencies changed, follow the
[manual signed update procedure](operations.md#manual-update).

Avoid updating an installed release with `git pull origin main`: `main` may
contain changes that have not been published as a signed release. Keep your
database and native-data backups before upgrades.

## Trust model

The guided download uses **trust on first use (TOFU)**. The installer contains a
pinned public release-signing key, checks its fingerprint, writes the external
allowed-signers file and verifies the release tag before checking out its code.
The expected fingerprint is:

```text
SHA256:Au9pahLKr9Wj1ayrHyXAZEO48y/xuVY88dk6zATqYqU
```

This trusts the installer you downloaded for the first key. If you need independent
authentication of that key, obtain the installer and signer through separately
trusted channels and use the [bootstrap below](#independently-trusted-bootstrap).
The [update runbook](operations.md#automatic-signed-update) explains the trust file
and key rotation.

For an existing installation that trusts the pre-0.3 signer, complete the
[public-key rotation step](operations.md#upgrading-to-the-03-release-signer) before
updating. The installer will not replace your existing trust file automatically.

## Advanced / development installation

### Independently trusted bootstrap

With an independently authenticated installer and public allowed-signers file,
choose the exact release tag:

```bash
./install.sh --release vX.Y.Z \
  --allowed-signers "$HOME/.config/sillytavern-telegram/trusted-maintainers" \
  --system-deps --no-start
```

Replace `vX.Y.Z` with the release you reviewed.

### Unsigned development main

For development without signed-release bootstrap:

```bash
./install.sh --unsafe-main --system-deps --no-start
```

Use an isolated checkout and test data. Contributor setup is in
[Contributing](../CONTRIBUTING.md).

### Manual/offline release ZIP

Releases include `SillyTavern-Telegram-Bridge-vX.Y.Z.zip` and its `.sha256` file.
Verify the checksum and authenticate the corresponding signed release/tag using
your trusted maintainer key. A checksum alone detects file changes; it does not
authenticate the publisher.

Extract into a fresh directory. Overlaying an archive can leave obsolete code
behind. ZIP installations do not meet the Git-checkout requirement for automatic
`/update`; follow the manual release procedure for future upgrades.

For custom environments, use [Configuration](configuration.md) and
[the systemd example](../systemd/sillytavern-telegram.service.example) as references.
Keep an installer-managed deployment's existing private files and service settings.

## Generated locations

| Purpose | Default path |
|---|---|
| Verified Git checkout | `~/sillytavern-telegram-bridge` |
| Private environment | `~/.local/share/sillytavern-telegram/.env` |
| Bridge state/data | `~/.local/share/sillytavern-telegram` |
| User systemd unit | `~/.config/systemd/user/sillytavern-telegram.service` |
| Starter SillyTavern user data | `~/.local/share/SillyTavern/data/default-user` |
| External update trust file | `~/.config/sillytavern-telegram/trusted-maintainers` |

## Troubleshooting

If the bot does not answer, check the user service and its recent log with the
commands above. Verify the bot token and numeric allowed user ID, then run
`--check` again. Restart after changing `.env`.

For provider failures, memory growth, backups and refused updates, use
[Operations](operations.md#troubleshooting). For a Mini App that will not open,
use [Mini App troubleshooting](miniapp.md#troubleshooting).
