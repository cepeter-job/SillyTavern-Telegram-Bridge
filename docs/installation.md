# Installation

## Before you start

The maintained installer targets Linux with user systemd and must run as the
intended non-root user. The guided path detects the distribution and installs
only missing bridge prerequisites. Supported package managers are:

- Debian/Ubuntu: `apt-get`
- Fedora/RHEL-family: `dnf` or `yum`
- Arch-family: `pacman`
- openSUSE/SUSE-family: `zypper`

Have these application values ready if this is a new bridge configuration:

- Telegram bot token from BotFather.
- Numeric Telegram user ID(s) allowed to use the bot.
- Default Story model in `provider::model` form.
- For the built-in OpenAI-compatible setup: provider endpoint and API key.

An existing private `.env` is preserved. The wizard asks only for missing
required values and does not echo secret prompts.

## Guided install

Download the installer and run it in a terminal:

```bash
curl --proto '=https' --tlsv1.2 --fail --location \
  https://raw.githubusercontent.com/cepeter/SillyTavern-Telegram-Bridge/main/install.sh \
  -o /tmp/sillytavern-telegram-install.sh
chmod 700 /tmp/sillytavern-telegram-install.sh
/tmp/sillytavern-telegram-install.sh
```

With no arguments, the installer shows:

```text
1) Standard install                 [recommended]
2) Install + Tailscale Mini App
3) Prepare only, do not start
4) Repair/reinstall dependencies
5) Advanced options
0) Exit
```

### Standard install

The recommended option performs the normal user-scope setup:

1. Detect the Linux family and package manager.
2. Detect missing bridge prerequisites and install only those packages.
3. Create the standard external trust file at
   `~/.config/sillytavern-telegram/trusted-maintainers` when absent.
4. Verify the pinned maintainer key fingerprint.
5. Clone without checkout, discover the newest `v*` release tag, verify the
   signed tag, then create local branch `main` at that exact release commit.
6. Provision user-local Python/uv and hash-locked bridge dependencies.
7. Detect an existing SillyTavern installation and native user data.
8. Collect any missing minimum bridge configuration.
9. Prepare and start the user systemd service and enable lingering.

The resulting source tree remains a real Git checkout on branch `main`, so the
built-in signed `/update` flow continues to use the same external trust anchor.

### SillyTavern autodetection

The installer preserves an already configured `SILLYTAVERN_DIR` first. When
none is configured, it checks common user-scope locations and a bounded set of
home-directory application/repository folders.

A candidate must contain SillyTavern markers such as `server.js`,
`package.json`, and `config.yaml`. The installer reads `config.yaml`
`dataRoot` instead of assuming `./data`, then discovers native users beneath
that data root. If multiple valid installations or users exist, the wizard asks
which one to use.

For the selected user, the bridge records explicit native paths for character
cards, World Info, System Prompts, Persona settings, and Persona avatars. If
character PNGs already exist, one is selected as the initial default instead of
forcing the starter card.

The menu also reports whether Node/npm and `node_modules` are present for the
detected SillyTavern installation. The bridge installer does **not** run
`npm install`, upgrade Node, or otherwise manage SillyTavern's own dependencies.

If no installation is detected, the bridge keeps its isolated starter data
under the normal user-local SillyTavern fallback path.

### Minimal configuration prompts

For a fresh bridge configuration, the wizard asks only for:

```text
Telegram bot token
Allowed Telegram user ID(s)
Default model (provider::model)
OpenAI-compatible API endpoint
Provider API key
```

The endpoint hostname is written automatically to
`SILLYTAVERN_PROVIDER_ALLOWED_HOSTS`; it is not requested separately.

If a provider catalog/model is already configured, those values are preserved.
If all required Telegram/model values are already valid, the installer does not
re-prompt for existing secrets.

Choose **Configure later** to write the installation files but leave required
configuration incomplete and keep the service stopped. Rerun `./install.sh`
later to finish setup.

The generated private environment remains:

```text
~/.local/share/sillytavern-telegram/.env
```

Do not replace it with `.env.example`; existing values, custom provider
configuration, and custom service units are preserved.

### Install + Tailscale Mini App

This option uses the same guided setup and additionally prepares Tailscale
Funnel. Tailscale itself must already be installed and signed in. The project
supports the current Serve/Funnel CLI and requires Tailscale 1.52+.

An administrator can allow the bridge user to manage the local daemon with:

```bash
sudo tailscale set --operator="$USER"
```

Funnel authorization still depends on the tailnet's MagicDNS, HTTPS, and Funnel
policy. Public Mini App requests remain protected by Telegram `initData`
validation and `SILLYTAVERN_TELEGRAM_ALLOWED_USERS`.

### Prepare and dependency repair choices

**Prepare only** provisions the verified checkout, environment, dependencies,
and service definition without starting the bridge.

**Repair/reinstall dependencies** re-checks missing system prerequisites and the
bridge Python environment. The installer never uses that option to modify an
existing SillyTavern Node/npm installation.

## Trust model

The easy guided path uses trust on first use (TOFU): the downloaded installer
contains the pinned release-signing public key, verifies its expected fingerprint
locally, creates the external allowed-signers file, and then verifies the signed
release tag before checking out repository code.

The expected current fingerprint is:

```text
SHA256:nCiZP+h1YWYCFjh37W8tXjR7oWGpZPF6bP4lbTOlAiI
```

If your deployment requires an **independent first-key trust boundary**, obtain
and verify the maintainer signer through a separate trusted channel and use the
[advanced independently trusted bootstrap](#independently-trusted-bootstrap)
instead of relying on TOFU.

## Verify the service

```bash
systemctl --user status sillytavern-telegram.service
journalctl --user -u sillytavern-telegram.service -n 80 --no-pager
cd ~/sillytavern-telegram-bridge
./.venv/bin/python sillytavern_telegram_bridge.py --check
```

## Updates

Use the bridge's signed `/update` flow for normal upgrades. It requires a clean
Git checkout on branch `main`, verifies the target signed release tag against
the external trust file, and refuses automatic installation when
`requirements.lock` changed.

When dependency changes require a reviewed manual update, use the
[manual signed update procedure](operations.md#manual-update). Do **not** replace
release verification with `git pull origin main`; `main` can contain commits
that are not a published signed release.

## Advanced / development installation

These paths are intentionally outside the primary user installation flow.

### Independently trusted bootstrap

Keep `--release TAG` for cases where a separately authenticated copy of
`install.sh` must clone the repository itself:

```bash
./install.sh --release vX.Y.Z \
  --allowed-signers "$HOME/.config/sillytavern-telegram/trusted-maintainers" \
  --system-deps --no-start
```

### Unsigned development `main`

Use this only for development when signature-pinned bootstrap is deliberately
not required:

```bash
./install.sh --unsafe-main --system-deps --no-start
```

### Manual/offline release ZIP

GitHub releases include `SillyTavern-Telegram-Bridge-vX.Y.Z.zip` and a matching
`.sha256`. Verify the checksum before extraction and never overlay an archive
onto an existing source tree.

The checksum detects corruption or accidental modification; it does **not**
independently authenticate the publisher. Establish release authenticity from
the corresponding signed release/tag and independently trusted maintainer key
before trusting the archive. ZIP installations also do not satisfy the
Git-checkout-on-`main` requirement for automatic `/update`.

For customized manual environments, use [Configuration](configuration.md) and
the repository's systemd example as references. Do not run manual copy steps on
top of an installer-managed deployment unless you intentionally want to replace
installer-managed state.

## Generated locations

| Purpose | Default path |
|---|---|
| Verified Git checkout | `~/sillytavern-telegram-bridge` |
| Private environment | `~/.local/share/sillytavern-telegram/.env` |
| Installer state/live data | `~/.local/share/sillytavern-telegram` |
| User systemd unit | `~/.config/systemd/user/sillytavern-telegram.service` |
| Default SillyTavern user data | `~/.local/share/SillyTavern/data/default-user` |
| External update trust file | `~/.config/sillytavern-telegram/trusted-maintainers` |

## Troubleshooting

Run the built-in configuration check and inspect the user service:

```bash
cd ~/sillytavern-telegram-bridge
./.venv/bin/python sillytavern_telegram_bridge.py --check
systemctl --user status sillytavern-telegram.service
journalctl --user -u sillytavern-telegram.service -n 100 --no-pager
```

For signed-update failures, database recovery, network policy, and operational
diagnostics, see [Operations](operations.md). For Mini App/Funnel problems, see
[Mini App](miniapp.md).
