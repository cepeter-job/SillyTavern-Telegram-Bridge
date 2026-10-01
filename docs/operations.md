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
- **📨 Saved-output delivery recovery.** Once a reply/greeting is committed,
  recovery stays bound to its original actor, session, job and rendered payload.
  Acknowledged Telegram chunks are checkpointed, and retry does not make another
  model call when the saved output is still valid.
- **📋 FIFO ordering per chat and topic.** Failed turns are stored before
  offset advancement so `/retry` can replay them.
- **🧹 Panels clean up after themselves.** Keyboards close and bindings are
  removed on Cancel, Close, expiry, or stale callbacks.
- **⚠️ Unknown commands are rejected** before normal generation — no accidental
  messages to the character.
- **✅ `/stscript` is allowlisted** and cannot execute arbitrary commands.
- **🧱 Architecture is CI-enforced.** The repository rejects import cycles and
  reverse imports from the isolated service/port layer. Ruff linting, security
  rules, and formatting cover the complete Python tree. Mypy checks a progressively
  typed surface emitted by `python tools/static_analysis.py --print-type-targets`;
  CI reports `--print-type-target-count` and refuses to shrink below the versioned
  `tools/type_surface_baseline.json` minimum.
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

#### Release-signing key redundancy, rotation, and revocation

Maintain a **second offline signing key** under separate custody from the primary key. Never place either private key on the bridge host, in Git, in release assets, or in the allowed-signers file. Only independently verified public keys belong in the external trust file.

The updater accepts **multiple public signer entries**. Use that capability as an overlap mechanism, not as a requirement for multi-signature tags: each release tag still needs one valid authorized SSH signature. A planned rotation is:

1. Generate the next signing key offline and record its fingerprint through an independent maintainer channel.
2. Add the next **public** key as a second line in every installation's external `trusted-maintainers` file while retaining the current public key. This is the **overlap period**.
3. Verify both public fingerprints locally and deploy the updated trust file before any release is signed only by the next key.
4. Sign a reviewed release with the next private key and confirm a representative installation accepts it while both public keys are trusted.
5. After all active installations trust the next key and the cutover release is verified, sign future releases with the next key.
6. End the overlap by **remove the old public key** from each installation's allowed-signers file. Keep any historical trust-policy record offline if you need archival verification; **do not rewrite historical tags**.

For emergency revocation, treat a suspected **compromised** signing key differently from a planned rotation: remove its public key from the trust file immediately, distribute an uncompromised public key through an independent channel, and resume releases only with that uncompromised key. Never “repair” trust by force-moving or re-signing an existing release tag. If every trusted private key is lost or compromised, automatic update must remain unavailable until operators manually provision a new independently verified public trust anchor.

The repository intentionally does not publish or invent a production backup public key until a real second offline key exists and its fingerprint has been verified out of band.

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

Use this only when the signed `/update` flow refuses automatic installation—for
example, because `requirements.lock` changed—or when you intentionally want a
reviewed offline update. Keep the same external trust anchor used by `/update`.

For a Git installation:

```bash
set -euo pipefail
cd ~/sillytavern-telegram-bridge
git switch main
test -z "$(git status --porcelain)" || { echo "Source checkout is dirty" >&2; exit 1; }

signers="$HOME/.config/sillytavern-telegram/trusted-maintainers"
git fetch origin --tags --prune --prune-tags
tag="$(git tag --list 'v*' --sort=-version:refname | sed -n '1p')"
test -n "$tag" || { echo "No release tag found" >&2; exit 1; }

git -c gpg.format=ssh \
  -c "gpg.ssh.allowedSignersFile=$signers" \
  -c gpg.minTrustLevel=fully verify-tag "$tag"

systemctl --user stop sillytavern-telegram.service
git checkout -B main "$tag^{commit}"
./install.sh --no-start
./.venv/bin/python sillytavern_telegram_bridge.py --check
systemctl --user start sillytavern-telegram.service
```

Signature verification occurs before the checkout moves. Do not substitute
`git pull origin main`: the branch can contain commits newer than the latest
published signed release.

For a release ZIP installation, download the latest ZIP and `.sha256`, verify the
checksum, and establish authenticity from the corresponding signed release/tag
before extraction. Extract to a fresh directory, install the locked dependencies,
run `--check`, then point your service at the new directory. Do not overlay a new
ZIP onto an old source tree.

### Database migrations, backup and restore

The operational SQLite database uses an ordered transactional migration ledger
(`schema_migrations`). New releases append forward migrations; unknown or
inconsistent migration history fails closed instead of being rewritten silently.

The current schema contains migrations **1–9**. In addition to the earlier
conversation, token-usage, episodic-memory and NPC Bank migrations, the current
delivery/recovery boundary adds:

- **7 — `message_identity`**: non-reusable explicit message identity while
  preserving existing row IDs and indexes.
- **8 — `assistant_delivery_progress`**: committed rendered payload,
  acknowledged Telegram chunk IDs and completion state.
- **9 — `job_delivery_intents`**: immutable job-bound delivery intent used to
  reject stale or mismatched recovery.

These are forward migrations. Restoring a binary that predates them requires the
matching pre-upgrade database snapshot rather than deleting migration rows.

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

### Memory OOM diagnostics

Memory diagnostics are disabled by default and are never added to the service
automatically. Enable them only while investigating unexplained process growth:

```dotenv
SILLYTAVERN_MEMORY_DIAGNOSTICS=1
```

Restart the bridge after changing the environment. While enabled, the bridge
samples process RSS every 20 seconds with no Python allocation tracing below
320 MiB. The fixed incident thresholds are:

- 256 MiB RSS: arm a warning incident;
- 320 MiB RSS: start one-frame `tracemalloc` allocation tracing;
- 384 MiB RSS: write one private incident report and stop tracing when the
  diagnostics subsystem owns the tracing session.

Reports are stored at:

```text
$SILLYTAVERN_BRIDGE_HOME/diagnostics/memory/memory-*.json
```

The directory is mode 0700, report files are mode 0600, and only the newest
three reports are retained. Reports contain process-level aggregates such as
RSS, selected `smaps_rollup` totals, Python traced current/peak bytes, thread
count, and top allocation sites. They never include prompts, model responses,
credentials, provider bodies, database contents, Python object values, or
arbitrary exception messages.

For interpretation, compare `rss_kib` and `smaps_kib` with
`traced_current_bytes` / `traced_peak_bytes`. Large traced growth that tracks
RSS points toward Python allocations; large RSS/private-memory growth with
relatively small traced growth points toward native or otherwise untraced
memory.

After collecting useful incidents, remove `SILLYTAVERN_MEMORY_DIAGNOSTICS=1`
and restart the bridge. Do not leave diagnostic tracing enabled as a normal
production setting.

Common configuration/update failures:

| Symptom / updater code | What to check |
|---|---|
| Provider is refused before a request | Add the exact external host to `SILLYTAVERN_PROVIDER_ALLOWED_HOSTS`; private/LAN hosts also need `SILLYTAVERN_PROVIDER_PRIVATE_HOSTS`. |
| RAG external endpoint refused | Add the exact embedding host to `SILLYTAVERN_RAG_ALLOWED_HOSTS`; private/LAN hosts also need `SILLYTAVERN_RAG_PRIVATE_HOSTS`. |
| Hindsight endpoint refused | Hindsight must use a numeric loopback origin. For a remote service, expose it through a separately trusted local loopback tunnel/proxy. |
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
