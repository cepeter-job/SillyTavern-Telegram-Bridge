# Operations, updates and troubleshooting

[Back to README](../README.md) · [Installation](installation.md) ·
[Configuration](configuration.md) · [Mini App](miniapp.md)

Use this guide to check the bot, update it or recover a saved story. Terminal
commands run on the Linux host as the user who installed the bridge, not inside
Telegram and not in a root shell. The default checkout is
`~/sillytavern-telegram-bridge`; adjust that path for a custom installation.

For a failed reply, try the matching [recovery action](user-guide.md#retry-regenerate-or-continue)
first. Reinstalling, resetting the story or restoring an older database is not
the first step for a provider timeout.

- [Service controls](#service-controls)
- [Troubleshooting](#troubleshooting)
- [Memory diagnostics](#memory-oom-diagnostics)
- [Signed updates](#automatic-signed-update)
- [Manual update](#manual-update)
- [Backup and restore](#database-migrations-backup-and-restore)
- [Closed-story recovery](#closed-story-recovery)
- [Alternate-ending recovery](#alternate-ending-recovery)

## Service controls

Check the local configuration, service status and recent log in that order:

```bash
cd ~/sillytavern-telegram-bridge
./.venv/bin/python sillytavern_telegram_bridge.py --check
systemctl --user status sillytavern-telegram.service --no-pager
journalctl --user -u sillytavern-telegram.service -n 100 --no-pager
```

`--check` validates local configuration. `active (running)` means the service
process is up; it does not prove that the provider can answer. The log is the
place to look for the actual error. If a command opens a scrollable view, press
`q` to return to the terminal.

After a configuration edit passes `--check`, restart to load it:

```bash
systemctl --user restart sillytavern-telegram.service
```

For maintenance, use `systemctl --user stop sillytavern-telegram.service` and
`systemctl --user start sillytavern-telegram.service`. Stop the service before
replacing Python dependencies or restoring a database.

To check whether the user service can remain active after logout:

```bash
loginctl show-user "$USER" -p Linger
```

`Linger=yes` means the user service may keep running after you log out.
If it reports `Linger=no`, run `sudo loginctl enable-linger "$USER"` from the
bridge user's terminal, or ask an administrator to enable it for that username.
Do not substitute the root account. The installer also offers lingering setup.

## Troubleshooting

Start with the configuration check and service log above, then use the row that
matches the symptom. When reporting an issue, include the failure time and
session/model details from `/status`. If you use the Mini App, include the
**System → Running** version too. Without it, report the release you installed
and recent redacted service logs; installed files alone do not identify a running
process.

| Symptom | Next step |
|---|---|
| Bot does not answer | Check that the service is active, the bot token is correct, and your numeric ID is in `SILLYTAVERN_TELEGRAM_ALLOWED_USERS`. |
| `Please use /start command.` | The standard session is unstarted. Complete `/character` setup and choose a greeting with `/start`. |
| Story response failed or delivery stopped | Return to its session and use `/retry`. Valid saved-output recovery reuses the reply; a failed generation may need another model request. |
| Story arrived but choices are unavailable | Reopen `/lightnovel` and use **Retry Choices**. This repairs the choices without regenerating the story. |
| Current Scene image failed | Reopen `/imagine`, check the selected image model and retry there. `/providers` controls text models, not image configuration. |
| Provider timed out, rate-limited or rejected credentials | Open `/providers` and inspect provider health. Fix the reported cause or select another model. **Reset runtime** only clears local cooldowns. |
| Prompt cannot fit | Open `/prompt` → Budget. Shorten fixed card/world/system instructions or select a model with a verified larger context window. |
| Provider or embedding endpoint is refused | Check its exact hostname allowlist. Private/LAN hosts also require the corresponding `*_PRIVATE_HOSTS` opt-in. |
| Hindsight endpoint is refused | Use a numeric loopback origin. Reach a remote service through a separately trusted local tunnel/proxy. |
| `.env` permission error | Ensure it is owned by the bridge user and, on POSIX, has mode `600`. |
| `.env` changes appear ignored | Restart the service. Process/systemd values override file values. |
| Alternate environment file is ignored | Set `SILLYTAVERN_ENV_FILE` in the process/systemd environment, not only inside that alternate file. |
| TTS reports a missing voice | Set `SILLYTAVERN_TTS_VOICE` and verify the configured `SILLYTAVERN_TTS_BIN` executable. |
| Mini App will not open or rejects a form | Use the [Mini App troubleshooting table](miniapp.md#troubleshooting). |

Some failed provider requests still consume tokens. Repeated retries are not a
substitute for checking an authentication, credit or model-availability error.
See [provider diagnostics](configuration.md#provider-diagnostics-and-catalog-maintenance)
for the difference between model discovery, manual probes and runtime health.

### Memory OOM diagnostics

OOM means out of memory. RSS is the amount of physical RAM currently attributed
to the process. These diagnostics help explain memory growth; they do not add
RAM or impose a new memory limit.

Memory diagnostics are off by default. To investigate unexplained bridge memory
growth, add this to the private `.env`, then restart:

```dotenv
SILLYTAVERN_MEMORY_DIAGNOSTICS=1
```

The sampler checks process RSS every 20 seconds:

| RSS threshold | Action |
|---|---|
| 256 MiB | Arm a warning incident. |
| 320 MiB | Start temporary one-frame Python allocation tracing. |
| 384 MiB | Save a private incident report and stop tracing if this subsystem started it. |

Reports live under
`$SILLYTAVERN_BRIDGE_HOME/diagnostics/memory/memory-*.json`. The directory is
private (`0700`), files are `0600`, and the newest three reports are retained.
They contain memory aggregates, thread counts and allocation-site information,
not prompts, responses, credentials, database contents or Python object values.
The Mini App can show sanitized summaries of retained incidents.

Compare RSS/private-memory growth with `traced_current_bytes` and
`traced_peak_bytes`. Low traced memory does not by itself prove a native leak:
tracing starts only after the threshold, so earlier Python allocations may be
missing too. Treat a report as evidence for investigation, not a diagnosis by itself.

After collecting useful reports, remove the setting and restart. Monitoring is
for diagnosis, not a RAM limit. It measures the bridge process; separately running
Hindsight, speech/embedding services and other programs need their own resource
checks.

## Updates

### Release downloads

The maintained assets are `SillyTavern-Telegram-Bridge-vX.Y.Z.zip` and its
`.sha256` file. Verify the checksum and authenticate the corresponding signed tag
before using an archive. Extract into a fresh directory.

Choose a published release and read its notes before updating. A tag or commit
on `main` is not by itself a published release. Release archives contain code,
not your private settings or stories; keep your own backups for recovery.
[CHANGELOG.md](../CHANGELOG.md) records the release history.

### Automatic signed /update

Open `/update` in Telegram or the Mini App's System page. Review the release and
confirm installation. Let the update finish before requesting it again. Then
check the running version and send a short test message. You normally do not
need to run Git commands or edit the trust file for each release.

**Running** is the code loaded by the current process; **Installed** describes
the files on disk. If only Installed has changed, investigate the restart rather
than assuming the bot is already using the update.

The checks below explain why the updater may refuse a request. Do not bypass
them to force an update. The updater requires:

- A clean Git checkout on `main` and an SSH-signed annotated release tag.
- An external allowed-signers file containing trusted public release keys.
- User-owned source/live parent directories without group/other write access.
- An empty managed live directory or its `.bridge-deployment.json` marker.
- Git, `ssh-keygen`, `systemctl`, `systemd-run` and the configured user service.

The installer prepares the standard trust file at
`~/.config/sillytavern-telegram/trusted-maintainers`. For manual setup, the current
public signer record is:

```text
cepeter namespaces="git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA+L6kUwaC94495CdAyZWyocRT5u951D4YnXhtceVKky cepeter-release-signing-2026-10-04
```

Its fingerprint is:

```text
SHA256:Au9pahLKr9Wj1ayrHyXAZEO48y/xuVY88dk6zATqYqU
```

Verify the fingerprint through an independent trusted channel when provisioning
trust manually. Preserve existing authorized signer entries during a planned
rotation. Keep the directory mode `700`, file mode `600`, and only public keys in
this file. The guided install's first-key trust is explained in
[Installation](installation.md#trust-model).

Manual environment settings are:

```dotenv
SILLYTAVERN_UPDATE_ALLOWED_SIGNERS=/home/you/.config/sillytavern-telegram/trusted-maintainers
SILLYTAVERN_UPDATE_SERVICE=sillytavern-telegram.service
```

A successful update verifies the tag, checks and compiles the staged tree, takes
an online database snapshot, advances the source checkout, replaces the managed
live mirror and requests a service restart. It retains the previous mirror for
recovery. Confirm the running version and resumed polling afterward.

Changed `requirements.lock` dependencies require a manual update. Use the table
below if automatic installation refuses to continue:

| Updater code | What to check |
|---|---|
| `trust` / `signature` | Trusted signer path, ownership/permissions and the signed release tag. |
| `target` | Source/live paths must be real, non-overlapping, user-owned and not group/other-writable. |
| `unmanaged_target` | An old live code mirror lacks the deployment marker; see the recovery note below. |
| `dirty` / `branch` | Save/review local changes and restore a clean source checkout on `main`. |
| `dependencies` | Use the manual procedure to install reviewed locked dependencies. |

#### Upgrading to the 0.3 release signer

Starting with `v0.3.000`, releases use the public signer shown above. The previous
signer fingerprint is `SHA256:nCiZP+h1YWYCFjh37W8tXjR7oWGpZPF6bP4lbTOlAiI`.
Existing installations that trust only that key will refuse the new signature.

Authenticate the new fingerprint through an independent maintainer channel before
adding its public allowed-signers record to your external trust file. Keep the
previous authorized record during the overlap period; do not replace the entire
file. Then retry `/update`. Never add a private key to the allowed-signers file.

The updater does not download or automatically trust a replacement key. The
installer uses the new pin for fresh trust-on-first-use installations, but refuses
to overwrite an existing old-only trust file. An explicitly provided
`--allowed-signers` file remains the operator's authority. No historical release
tag has been rewritten or re-signed as part of this rotation.

#### Release-signing key rotation and revocation

This section is for release maintainers. Keep a **second offline signing key**
under separate custody. Private signing keys belong outside the bridge host,
repository, release assets and public trust file.

The updater accepts **multiple public signer entries**. For a planned rotation:

1. Verify the new public key's fingerprint through an independent channel.
2. Add it alongside the current public key on each installation. This is the
   **overlap period**; a release still needs one authorized signature.
3. Sign a reviewed release with the new key and verify that installations accept it.
4. Complete deployment of the new trust entry before switching future releases.
5. **Remove the old public key** after the cutover; **do not rewrite historical tags**.

For a suspected **compromised** key, remove its public entry immediately and
provision an uncompromised key through an independent channel. If all trusted
private keys are lost or compromised, automatic updates remain unavailable until
operators establish a new trusted signer. No production backup public key is
published until an actual second key exists and is independently verified.

#### First update from a pre-hardening installation

If a nonempty `live` code mirror has no `.bridge-deployment.json`, stop the service
and archive only that code mirror. Inspect your service configuration first if
its paths differ from the installer defaults. Leave `.env` and the database under
`~/.local/share/sillytavern-telegram/scripts/` in place.

```bash
systemctl --user stop sillytavern-telegram.service
mv ~/.local/share/sillytavern-telegram/live ~/.local/share/sillytavern-telegram/prehardening-live-backup
mkdir ~/.local/share/sillytavern-telegram/live
chmod 700 ~/.local/share/sillytavern-telegram/live
chmod go-w ~/sillytavern-telegram-bridge ~/.local/share/sillytavern-telegram
systemctl --user start sillytavern-telegram.service
```

Use a fresh backup destination if `prehardening-live-backup` already exists.

### Manual update

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

### Database migrations, backup and restore

A backup preserves your current data; a restore replaces it with an older saved
copy. Restoring is for recovery, not ordinary troubleshooting. Messages and
settings created after the chosen snapshot will not be present in that restored
copy, so check its date and keep the current database too.

New releases apply ordered forward migrations recorded in `schema_migrations`.
The [schema definition](../bridge/schema.py) is the current migration reference.
Do not delete migration rows to make an older binary accept newer data. A rollback
across schema changes needs the matching pre-upgrade database snapshot.

Signed self-update snapshots the database after verifying/compiling the release
and before changing source or live code. Snapshots use SQLite's online backup API,
are integrity-checked, stored with mode `0600`, and retain the newest ten matching
files under `$SILLYTAVERN_BRIDGE_HOME/backups/database`.

To make an additional snapshot while the bot runs:

```bash
cd ~/sillytavern-telegram-bridge
./.venv/bin/python sillytavern_telegram_bridge.py --backup-database
```

A database snapshot is **not a complete installation backup**. Separately preserve
private `.env`, the provider catalog, native cards/Personas/Worlds/prompts and the
external public signer file. If using Hindsight, back up that service's data too.
Keep backups private and retain a copy outside the VPS you are protecting.

Restore is offline. Replace `snapshot-file.sqlite3` with the filename of the
snapshot you reviewed:

```bash
set -euo pipefail
cd ~/sillytavern-telegram-bridge
snapshot="$HOME/.local/share/sillytavern-telegram/backups/database/snapshot-file.sqlite3"
systemctl --user stop sillytavern-telegram.service
./.venv/bin/python sillytavern_telegram_bridge.py --restore-database "$snapshot"
./.venv/bin/python sillytavern_telegram_bridge.py --check
systemctl --user start sillytavern-telegram.service
```

Restore refuses to replace a database while the configured service is active.
It validates the snapshot, saves a pre-restore snapshot of the current database,
clears stale WAL/SHM sidecars and atomically installs the verified copy. If an
operation was interrupted, inspect its state before submitting it again.

## Privacy and maintenance boundaries

Use the numeric Telegram allowlist and protect private files. Character, Persona
and World edits affect shared native data. Providers receive the context and
media needed for selected tasks. Logs and stored failures may contain external
text, so inspect and redact them before sharing an issue; never publish secrets,
private cards or a database as a diagnostic shortcut.

The bridge validates outbound provider/embedding hosts and bounds uploads,
retrieval and background work. Helper subprocesses receive a minimal environment
instead of the bot/provider credentials. Their own network behavior is separate
from the bridge's hardened HTTP transport. See [Security](../SECURITY.md).

Contributor checks cover import direction, resource ownership and a growing typed
surface. `python tools/static_analysis.py --print-type-target-count` reports the
current typed count; [Contributing](../CONTRIBUTING.md) describes the verification
workflow. These checks are useful evidence, not a guarantee against every leak
or operational failure.

## Closed-story recovery

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

## Alternate-ending recovery

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
needs in memory.
