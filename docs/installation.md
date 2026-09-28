# Installation

## Install with the user-scope script

For a fresh Linux installation, run as your normal user. The primary path has two trust-preserving steps: provision the external maintainer key once, then install the newest signed `v*` release without executing repository code before its tag verifies.

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

Independently compare the printed fingerprint through a separate trusted maintainer channel before proceeding:

```text
SHA256:nCiZP+h1YWYCFjh37W8tXjR7oWGpZPF6bP4lbTOlAiI
```

The repository documentation is not the independent channel. Key rotation and revocation procedures are in [Operations](operations.md#automatic-signed-update).

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

This leaves a real Git checkout on local branch `main` whose initial contents are exactly the verified release commit. The external signer file remains outside the repository and is reused by the built-in signed `/update` flow.

The generated `~/.local/share/sillytavern-telegram/.env` automatically points `SILLYTAVERN_UPDATE_ALLOWED_SIGNERS` at the standard trust file. Fill the bot and provider values:

```dotenv
SILLYTAVERN_TELEGRAM_BOT_TOKEN=your-bot-token
SILLYTAVERN_TELEGRAM_ALLOWED_USERS=your-numeric-user-id
SILLYTAVERN_MODEL=default::your-model-id
SILLYTAVERN_PROVIDER_ENDPOINT=https://your-provider.example/v1
SILLYTAVERN_PROVIDER_ALLOWED_HOSTS=your-provider.example
LLM_API_KEY=your-provider-key
# Leave blank for automatic Funnel URL discovery.
SILLYTAVERN_MINIAPP_PUBLIC_URL=
```

Install and sign in to [Tailscale](https://tailscale.com/download/linux) on this server first. Requires **Tailscale 1.52+**, running `tailscaled`, MagicDNS, HTTPS certificates, Funnel authorization and an account allowed to configure Tailscale. An administrator can designate the account with `sudo tailscale set --operator="$USER"`. Account login and tailnet approval are external prerequisites; the bridge does not invent or bypass them. Then run:

```bash
./install.sh --with-tailscale-funnel --linger
```

Deployment: **public HTTPS → Tailscale Funnel → `127.0.0.1:8787`**. No additional reverse proxy, custom domain, inbound public ports or port forwarding is required. Funnel manages TLS and the public hostname. The bot's **Bridge** menu opens the app. Funnel is public; private API operations still require signed Telegram `initData` and an allowed-user ID. The same root proxy carries both `/miniapp/*` and `/api/v1/*`.

A blank URL reuses only an exact already-public Mini App proxy, or selects the first unused HTTPS port: **443, 8443, then 10000**. Only that URL assignment is saved in `.env`. Existing private Serve and public Funnel routes are not reset or replaced. An explicit URL must match this node and an available supported port; conflicts stop setup. The backend uses `SILLYTAVERN_MINIAPP_PORT` (default `8787`).

The installer prepares user-local Python, hash-locked dependencies, a starter character/avatar, an env-derived provider catalog and a user systemd service. `--system-deps` installs Debian/Ubuntu base prerequisites, **not Tailscale**. Existing `.env` values, custom provider YAML, native files and custom service units are preserved. `--replace-service` explicitly replaces a custom unit with a backup. `--no-start` prepares files but never starts the bridge or publishes a new Funnel. Publishing requires a ready loopback Mini App and an unauthenticated API rejection.

Keep the generated starter-character value initially. Add optional model IDs with `SILLYTAVERN_EXTRA_MODELS=model-two,model-three`, or use custom provider YAML for multiple providers. Without the Funnel flag, a blank URL keeps the listener off. The Mini App manages Characters/Optimizer, Models/Generation, Sessions, Personas, Worlds, Memory, Data Bank and System/Update. Ordinary conversation stays in Telegram.

**Upgrade:** stop the user service before changing dependencies, update the clean checkout to `main`, and rerun `./install.sh --with-tailscale-funnel --linger`. Version **0.2.033** changes the dependency lock from previous releases, requiring this manual installation; `/update` keeps its dependency-change guard.

Allowed users share administration of native assets; use separate bridge instances for mutually untrusted users. Existing unrelated proxy packages and system services are not uninstalled. Funnel `--bg` persists across daemon restarts and reboot. Tailscale still documents Funnel as beta with non-configurable bandwidth limits.

See [Mini App installation and operations](miniapp.md) for authorization, upgrade commands, status and troubleshooting.

## Advanced / development installation

These paths are intentionally outside the primary Quick start.

**Bootstrap with an independently obtained installer.** Keep `--release TAG` for cases where a separately authenticated copy of `install.sh` must perform the clone itself:

```bash
./install.sh --release vX.Y.Z \
  --allowed-signers "$HOME/.config/sillytavern-telegram/trusted-maintainers" \
  --system-deps --no-start
```

**Unsigned development `main`.** Use this only for development when signature-pinned bootstrap is deliberately not required:

```bash
./install.sh --unsafe-main --system-deps --no-start
```

**Manual/offline release ZIP.** GitHub releases include `SillyTavern-Telegram-Bridge-vX.Y.Z.zip` and its matching `.sha256`. Verify the checksum, extract into a fresh directory, install the locked dependencies, and point the service at that directory. Do not overlay archives onto an existing source tree. Release-ZIP installations do not satisfy the Git-checkout-on-`main` requirement for automatic `/update`.

---

## 🔧 Requirements

- **Python 3.11**
- A Telegram bot token and your Telegram user ID
- A local SillyTavern installation with at least one PNG character card
- A configured Story model from the private provider catalog — OpenAI-compatible, native OpenAI Codex OAuth, Anthropic Messages, or OpenCode Muse
- Optional: a separate Utility model, Hindsight, embeddings, image generation, STT, and TTS services

---

## 📦 Installation guide

The recommended Linux setup keeps the bridge, its virtual environment, and its
runtime data under your user account. You do not need a system-wide Python
installation or a root-owned service.

### 1. Install the bridge source

**Recommended:** use the two-step signed-release sequence at the top of this guide. A clean branch named `main`, positioned at the verified release commit, remains compatible with the built-in signed `/update` flow. Do not execute repository code until the selected release tag has been verified against the independently provisioned external allowed-signers file.

Alternative bootstrap, unsigned-development, and release-archive procedures are intentionally kept in the **Advanced / development installation** section above.

### 2. Create a private virtual environment

Install only the locked runtime dependencies required to run the bridge:

```bash
python3.11 -m venv .venv
./.venv/bin/python -m pip install -r requirements.lock
```

`requirements-dev.txt` is for contributors and CI; it is not required for a
normal bridge installation.

### 3. Create the user-scoped environment file

The launcher reads `~/.local/share/sillytavern-telegram/.env` by default:

```bash
mkdir -p ~/.local/share/sillytavern-telegram
cp .env.example ~/.local/share/sillytavern-telegram/.env
chmod 600 ~/.local/share/sillytavern-telegram/.env
```

Edit that file with your Telegram token, allowed user ID, SillyTavern path,
default character, provider credentials, and model. The
[Configuration](configuration.md) and [Provider catalog](configuration.md#provider-catalog)
guides describe the available settings.

### 4. Validate and run the bridge

Check the installation without starting Telegram polling:

```bash
cd ~/sillytavern-telegram-bridge
./.venv/bin/python sillytavern_telegram_bridge.py --check
```

When the check passes, run the bridge manually:

```bash
cd ~/sillytavern-telegram-bridge
./.venv/bin/python sillytavern_telegram_bridge.py
```

Press `Ctrl+C` to stop a manual run.

### 5. Run it persistently with user systemd

The repository includes a hardened **user-service** template. The default
template expects:

- bridge checkout: `~/sillytavern-telegram-bridge`
- virtual environment: `~/sillytavern-telegram-bridge/.venv`
- environment file: `~/.local/share/sillytavern-telegram/.env`
- bridge runtime data: `~/.local/share/sillytavern-telegram`
- update staging: `~/.local/share/sillytavern-telegram/live`
- SillyTavern user data: `~/.local/share/SillyTavern/data/default-user`

If your paths differ, edit the copied service file before enabling it. In
particular, keep `WorkingDirectory`, `ExecStart`,
`SILLYTAVERN_BRIDGE_SOURCE_DIR`, `SILLYTAVERN_LIVE_BRIDGE_DIR`, and
`ReadWritePaths` aligned with your actual bridge and SillyTavern locations.

Install and start the service for your user account:

```bash
mkdir -p ~/.config/systemd/user
cp systemd/sillytavern-telegram.service.example \
  ~/.config/systemd/user/sillytavern-telegram.service

systemctl --user daemon-reload
systemctl --user enable --now sillytavern-telegram.service
systemctl --user status sillytavern-telegram.service
```

Useful service commands:

```bash
systemctl --user restart sillytavern-telegram.service
systemctl --user stop sillytavern-telegram.service
journalctl --user -u sillytavern-telegram.service -f
```

`systemctl --user enable` starts the bridge automatically when your user
systemd manager starts. If you also want it to start at boot without an
interactive login and remain running after logout, enable lingering for the
account once:

```bash
loginctl enable-linger "$USER"
```

Some distributions require an administrator to enable lingering for a user.

---
