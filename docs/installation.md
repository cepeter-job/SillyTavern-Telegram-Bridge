# Installation

## Before you start

The maintained installation path targets Linux with user systemd and runs as the
intended non-root user.

The trust/bootstrap steps happen **before** `install.sh` can provision system
packages, so `git` and `ssh-keygen` must already exist. On Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y git openssh-client ca-certificates
```

Have these application values ready:

- Telegram bot token from BotFather.
- Numeric Telegram user ID to allow.
- Story provider/model and its credential.
- A SillyTavern data location, or use the starter data created by the installer.

The installer can provision user-local Python 3.11+, locked Python dependencies,
a starter character/avatar, the private environment file, and a hardened user
systemd service. Tailscale is optional and is needed only for the public Mini App.

## Install with the user-scope script

A fresh install has two trust-preserving bootstrap steps: provision the external
maintainer key once, then install the newest signed `v*` release without
executing repository code before its tag verifies.

### 1. One-time trust setup

Create the standard external allowed-signers file:

```bash
mkdir -p "$HOME/.config/sillytavern-telegram"
chmod 700 "$HOME/.config/sillytavern-telegram"
cat > "$HOME/.config/sillytavern-telegram/trusted-maintainers" <<'EOF'
cepeter namespaces="git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGRaxgobK+D+zdXdUzLb1xTQ2EPs9iYkeQGOOlepl+35 cepeter-release-signing
EOF
chmod 600 "$HOME/.config/sillytavern-telegram/trusted-maintainers"
awk '{print $3, $4, $5}' "$HOME/.config/sillytavern-telegram/trusted-maintainers" | ssh-keygen -lf -
```

Independently compare the printed fingerprint through a separate trusted
maintainer channel before proceeding:

```text
SHA256:nCiZP+h1YWYCFjh37W8tXjR7oWGpZPF6bP4lbTOlAiI
```

The repository documentation is not the independent verification channel.
Rotation and revocation procedures are in
[Operations](operations.md#automatic-signed-update).

### 2. Install latest signed release

```bash
set -euo pipefail
repo="https://github.com/cepeter/SillyTavern-Telegram-Bridge.git"
dest="$HOME/sillytavern-telegram-bridge"
signers="$HOME/.config/sillytavern-telegram/trusted-maintainers"

git clone --no-checkout "$repo" "$dest"
cd "$dest"
tag="$(git tag --list 'v*' --sort=-version:refname | sed -n '1p')"
test -n "$tag" || { echo "No release tag found" >&2; exit 1; }
git -c gpg.format=ssh \
  -c "gpg.ssh.allowedSignersFile=$signers" \
  -c gpg.minTrustLevel=fully verify-tag "$tag"
git checkout -B main "$tag^{commit}"
./install.sh --system-deps --no-start
```

This leaves a real Git checkout on local branch `main` whose initial contents
are exactly the verified release commit. The external signer file remains
outside the repository and is reused by the built-in signed `/update` flow.

### 3. Configure the generated private environment

The first installer run creates:

```text
~/.local/share/sillytavern-telegram/.env
```

Edit that generated file in place. **Do not replace it with `.env.example`.**
The generated file already contains the installer-selected starter values and
points `SILLYTAVERN_UPDATE_ALLOWED_SIGNERS` at the standard external trust file.
At minimum, fill:

```dotenv
SILLYTAVERN_TELEGRAM_BOT_TOKEN=your-bot-token
SILLYTAVERN_TELEGRAM_ALLOWED_USERS=your-numeric-user-id
SILLYTAVERN_MODEL=default::your-model-id
SILLYTAVERN_PROVIDER_ENDPOINT=https://your-provider.example/v1
SILLYTAVERN_PROVIDER_ALLOWED_HOSTS=your-provider.example
LLM_API_KEY=your-provider-key
```

For additional providers, Codex OAuth, network allowlists, native paths, memory,
RAG, voice, and other settings, see [Configuration](configuration.md).

### 4. Start the bridge

For a Telegram-only deployment, rerun the installer without Funnel:

```bash
cd ~/sillytavern-telegram-bridge
./install.sh --linger
```

This validates the private configuration, keeps the installer-managed service
unit, enables/restarts the user service, and enables lingering so it can continue
after logout. It does **not** publish the Mini App.

For the optional public Mini App, first install and sign in to Tailscale. This
project supports the current Serve/Funnel CLI and therefore requires Tailscale
1.52+. On Linux, an administrator can allow the bridge user to manage the local
`tailscaled` daemon with:

```bash
sudo tailscale set --operator="$USER"
```

That operator setting controls the **local daemon only**. Funnel separately
requires the tailnet prerequisites and authorization, including MagicDNS, HTTPS
certificates, and permission to use Funnel. Then run:

```bash
./install.sh --with-tailscale-funnel --linger
```

Funnel is public internet exposure. Telegram `initData` validation and the
allowed-user list remain the application's authorization boundary. The installer
uses the supported Funnel HTTPS ports 443, 8443, then 10000 without resetting
unrelated Serve/Funnel routes. See [Mini App](miniapp.md) for deployment,
authorization, and troubleshooting details.

### 5. Verify the service

```bash
systemctl --user status sillytavern-telegram.service
journalctl --user -u sillytavern-telegram.service -n 80 --no-pager
./.venv/bin/python sillytavern_telegram_bridge.py --check
```

The installer preserves existing private `.env` values, native files, custom
provider YAML, and custom service units. Use `--replace-service` only when you
explicitly want the installer to back up and replace a custom unit.

## Updates

Use the bridge's signed `/update` flow for normal upgrades. It requires a clean
Git checkout on branch `main`, verifies the signed release tag against the
external trust file, and refuses automatic installation when
`requirements.lock` changed.

When dependency changes require a reviewed manual update, use the
[manual signed update procedure](operations.md#manual-update). Do **not** replace
release verification with `git pull origin main`; `main` can contain commits
that are not a published signed release.

## Advanced / development installation

These paths are intentionally outside the primary user installation flow.

### Bootstrap from an independently obtained installer

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
