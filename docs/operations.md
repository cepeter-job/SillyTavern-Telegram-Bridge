# Operations, updates, and troubleshooting

## 🔒 Reliability, privacy, and safety

- **🔐 Keep secrets out of Git.** That includes `.env`, provider YAML, SQLite
  files, logs, cards, Personas, and private prompts.
- **👥 Lock down access** with the required numeric
  `SILLYTAVERN_TELEGRAM_ALLOWED_USERS` allowlist.
- **🌐 HTTPS for anything external.** Loopback is fine for local services.
- **🚫 Credentials are never displayed.** Provider hosts are validated before
  keys are attached. Health output never shows them.
- **📦 Everything from outside is untrusted.** Provider catalogs, model
  responses, uploaded documents, memories, and RAG references are all treated
  as untrusted input and bounded before use.
- **📤 Uploads are bounded.** File sizes, document expansion, PDF pages,
  extracted text, image prompts, memory context, and embedding work all have
  limits.
- **💾 Persistence before acknowledgment.** Updates are saved before the bot
  acknowledges Telegram, and update IDs are deduplicated.
- **📋 FIFO ordering per chat and topic.** Failed turns are stored before
  offset advancement so `/retry` can replay them.
- **🧹 Panels clean up after themselves.** Keyboards close and bindings are
  removed on Cancel, Close, expiry, or stale callbacks.
- **⚠️ Unknown commands are rejected** before normal generation — no accidental
  messages to the character.
- **✅ `/stscript` is allowlisted** and cannot execute arbitrary commands.
- **🧱 Architecture is CI-enforced.** The repository rejects import cycles and
  reverse imports from the isolated service/port layer. Ruff linting, security
  rules, and formatting cover the complete Python tree. Mypy currently checks
  24 explicitly listed source files, including the network and callback-token policy.
- **🛡️ Use the systemd hardening template** for production deployments.

---

## 🚀 Downloads, updates, and database compatibility

### Release downloads

The latest GitHub release includes a real downloadable archive:

```text
SillyTavern-Telegram-Bridge-vX.Y.Z.zip
SillyTavern-Telegram-Bridge-vX.Y.Z.zip.sha256
```

The ZIP is produced directly from the signed release tag and contains only
tracked repository content. Verify the checksum before manual installation.
GitHub's automatically generated source archives may also appear, but the named
ZIP above is the maintained release asset.

This repository intentionally keeps **only the latest GitHub release and tag**.
Release history remains available in `CHANGELOG.md` and Git history.

### Automatic signed `/update`

Automatic installation is fail-closed. `/update` requires:

1. a clean Git checkout on branch `main`;
2. an SSH-signed annotated release tag;
3. a trusted public key in an allowed-signers file outside the source/live trees;
4. user-owned source/live parent directories that are not group/other-writable;
5. an empty managed live directory or one containing the bridge's
   `.bridge-deployment.json` marker;
6. Git, `ssh-keygen`, `systemctl`, `systemd-run`, and the configured user systemd service.

Create the trust directory/file on Linux:

```bash
mkdir -p ~/.config/sillytavern-telegram
chmod 700 ~/.config/sillytavern-telegram
cat > ~/.config/sillytavern-telegram/trusted-maintainers <<'EOF'
cepeter namespaces="git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGRaxgobK+D+zdXdUzLb1xTQ2EPs9iYkeQGOOlepl+35 cepeter-release-signing
EOF
chmod 600 ~/.config/sillytavern-telegram/trusted-maintainers
```

Verify that key's fingerprint independently before trusting it:

```text
SHA256:nCiZP+h1YWYCFjh37W8tXjR7oWGpZPF6bP4lbTOlAiI
```

Then configure:

```dotenv
SILLYTAVERN_UPDATE_ALLOWED_SIGNERS=/home/you/.config/sillytavern-telegram/trusted-maintainers
SILLYTAVERN_UPDATE_SERVICE=sillytavern-telegram.service
```

and restart the bridge once so it loads the setting.

A successful update verifies the exact signed tag, checks ancestry and archive
safety, compiles the staged Python tree, fast-forwards the unchanged source
checkout, replaces the managed live mirror, retains the previous mirror for
recovery, and requests a nonblocking user-service restart.

If `requirements.lock` changed, automatic installation refuses the update; use a
manual reviewed install so dependency changes are explicit.

#### First update from a pre-hardening installation

If your old `live` directory is a nonempty code copy without
`.bridge-deployment.json`, `/update` returns `unmanaged_target`. Stop the service,
archive **only that old live code mirror**, create an empty private live directory,
and leave the `.env` and SQLite database in place:

```bash
systemctl --user stop sillytavern-telegram.service
mv ~/.local/share/sillytavern-telegram/live    ~/.local/share/sillytavern-telegram/prehardening-live-backup
mkdir ~/.local/share/sillytavern-telegram/live
chmod 700 ~/.local/share/sillytavern-telegram/live
chmod go-w ~/sillytavern-telegram-bridge ~/.local/share/sillytavern-telegram
systemctl --user start sillytavern-telegram.service
```

Do not move/delete:

```text
~/.local/share/sillytavern-telegram/.env
~/.local/share/sillytavern-telegram/scripts/sillytavern_telegram.sqlite3
```

### Manual update

For a Git installation:

```bash
systemctl --user stop sillytavern-telegram.service
cd ~/sillytavern-telegram-bridge
git fetch origin --tags --prune
git switch main
git pull --ff-only origin main
./.venv/bin/python -m pip install --require-hashes -r requirements.lock
./.venv/bin/python sillytavern_telegram_bridge.py --check
systemctl --user start sillytavern-telegram.service
```

For a release ZIP installation, download the latest ZIP and `.sha256`, verify the
checksum, extract to a fresh directory, install the locked dependencies, run
`--check`, then point your service at the new directory. Do not overlay a new ZIP
onto an old source tree.

### Database migrations, backup and restore

The operational SQLite database uses an ordered transactional migration ledger (`schema_migrations`). New releases append forward migrations; unknown or inconsistent migration history fails closed instead of being rewritten silently.

Verified self-update takes an online SQLite snapshot **after** the signed release has been verified and compiled but **before** the source checkout or live mirror is changed. Snapshots use SQLite's online backup API, are integrity-checked, stored mode 0600 under `$SILLYTAVERN_BRIDGE_HOME/backups/database`, and retain the newest ten matching snapshots.

Create an additional online snapshot at any time:

```bash
./.venv/bin/python sillytavern_telegram_bridge.py --backup-database
```

Restore is deliberately offline. Stop the configured user service first; the restore command refuses to replace the database while that service is active. It validates the selected snapshot, takes a `pre-restore` snapshot of the current database, clears stale WAL/SHM sidecars, and atomically replaces the database only with the verified copy:

```bash
systemctl --user stop sillytavern-telegram.service
./.venv/bin/python sillytavern_telegram_bridge.py --restore-database \
  ~/.local/share/sillytavern-telegram/backups/database/<snapshot>.sqlite3
./.venv/bin/python sillytavern_telegram_bridge.py --check
systemctl --user start sillytavern-telegram.service
```

Do not delete migration records to force an older binary onto a newer schema. Rollback across schema changes requires restoring the matching pre-upgrade database snapshot.

## 🧰 Troubleshooting

Start with the built-in check:

```bash
cd ~/sillytavern-telegram-bridge
./.venv/bin/python sillytavern_telegram_bridge.py --check
```

For a systemd installation:

```bash
systemctl --user status sillytavern-telegram.service
journalctl --user -u sillytavern-telegram.service -n 100 --no-pager
```

Common configuration/update failures:

| Symptom / updater code | What to check |
|---|---|
| Provider is refused before a request | Add the exact external host to `SILLYTAVERN_PROVIDER_ALLOWED_HOSTS`; private/LAN hosts also need `SILLYTAVERN_PROVIDER_PRIVATE_HOSTS`. |
| RAG/Hindsight external endpoint refused | Configure the matching `*_ALLOWED_HOSTS` and, for private networks, `*_PRIVATE_HOSTS`. |
| `.env` permission error | On POSIX, ensure the file is owned by the bridge user and `chmod 600`. |
| Changed `.env` appears ignored | Restart the service; settings are captured at application startup. |
| `SILLYTAVERN_ENV_FILE` appears ignored | Set it in systemd/process environment, not only inside the alternate file. |
| `/update` → `trust` or `signature` | Check `SILLYTAVERN_UPDATE_ALLOWED_SIGNERS`, file ownership/mode, and that the GitHub tag displays **Verified**. |
| `/update` → `target` | Source/live paths must be real, non-overlapping, user-owned, and not group/other-writable. |
| `/update` → `unmanaged_target` | Archive the old pre-hardening live code mirror and create an empty managed `live` directory. |
| `/update` → `dirty` / `branch` | Restore a clean source checkout and switch to `main`. |
| `/update` → `dependencies` | `requirements.lock` changed; perform a manual locked-dependency update. |
| TTS says voice is missing | Set `SILLYTAVERN_TTS_VOICE` and ensure `SILLYTAVERN_TTS_BIN` points to a working `edge-tts`. |

Never paste real bot/provider passwords or private signing keys into issues,
README files, release assets, or Telegram messages.

---

### Helper subprocess environment

The bridge launches trusted helper binaries with a minimal environment instead of inheriting the full bridge process environment. Provider keys, the Telegram bot token, Hindsight credentials, Live Sync passwords and other application secrets are not passed to TTS/ffmpeg, document parser workers, runtime-health Git commands, Tailscale CLI operations or self-update supervisor commands. Only process plumbing such as PATH/HOME/locale, temporary-directory settings, user-systemd bus variables and explicit TLS CA paths is retained.
