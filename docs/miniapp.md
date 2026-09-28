# Telegram Mini App

The optional Mini App manages the bridge inside Telegram. Chat continues through the bot. The supported public deployment is Tailscale Funnel directly to `127.0.0.1:8787`. Use `--with-tailscale-funnel` to discover and save the node's public `/miniapp/` URL. Without that flag, an empty `SILLYTAVERN_MINIAPP_PUBLIC_URL` disables the listener. Never expose its HTTP port directly.

Only IDs in `SILLYTAVERN_TELEGRAM_ALLOWED_USERS` are admitted. Open the Bridge menu from the private bot chat. The API validates signed Telegram initData on every request (default lifetime one hour); reopen from Telegram when expired. Direct browser access intentionally has no login bypass. Credentials stay server-side and must not be included in the public URL. Main Mini App profile/deep links additionally require configuration through BotFather.

Private sessions use the authenticated user ID. Group chats and forum topics are not implicitly authorized by a launch link. Allowed users administer shared native character, persona and world files; use separate bridge instances for mutually untrusted users.

The static UI uses native ES modules and needs no Node runtime or build. It ships inside `bridge/miniapp_assets` and is included in verified live-mirror updates. The responsive presentation uses a compact Telegram-themed status header, desktop sidebar, five-item mobile bottom navigation, and a More sheet for secondary pages. The Home dashboard emphasizes the active session, quick actions, recent operations and observed bridge health; loading states use skeletons instead of replacing the page with a generic loading card.

## Navigation and usage

The primary destinations are **Home, Characters, Usage, Sessions and More**.
More contains searchable links to Models, Memory, Personas, Worlds, Data Bank and
System. Home puts the current session first, with a direct return to Telegram,
quick controls and a token overview. Pages load on demand; a slower previous
navigation cannot replace the page you just selected. Native theme/safe-area
updates, labeled local SVG icons, keyboard focus and reduced-motion preferences
are supported without an icon font or external font download.

**Usage** reports actual provider counters for the selected private session or
all sessions in that private chat. See [Token usage](token-usage.md) for coverage,
missing counters, UTC time windows, retention and the migration/rollback note.
No historical token totals, currency costs or subscription quotas are invented.

## Characters and optimizer

Use Characters to browse/search PNG cards, view portraits/info, upload cards and create a new normal conversation session. Ranked cards show the canonical S/A/B/C/D WEBM animation from `assets/character-ranks/telegram`; reduced-motion clients and media failures fall back to the static tier badge. Empty or failed portrait loads show the explicit portrait-unavailable state instead of a broken image. Selecting a character creates a new session instead of changing existing conversation history; send `/start` in Telegram for its opening. Active/default/referenced characters cannot be deleted; deletion verifies a backup and the revision you reviewed.

Optimizer uses the configured Utility model and supports an optional Manual suggestion. It returns an original/proposed preview. Apply consumes the actor/session-bound proposal once and verifies the original digest; Discard leaves the card untouched. Existing-filename uploads similarly require an explicit replacement preview. Simple upload filenames must not contain path or wildcard characters.

Slow work is admitted to the existing bounded utility executor and has an actor-owned operation ID. Identical retries return the existing operation rather than spending twice. Interrupted operations are marked after restart, never silently replayed.

## Models and generation

Models displays provider/model names only, never private provider configuration. Story and Utility selections are scoped to the current session; Utility can inherit Story. Search filters up to 500 results. Generation uses the same canonical limits as Telegram: temperature 0–2, top-p 0–1, output tokens 1–16000, frequency/presence penalties -2–2, reasoning budget 0–32000 and at most four stop sequences of 100 characters. Reasoning provides the same named levels as Telegram—None (0), Low (1024), Medium (4096), High (8192) and Max (16384)—plus Custom for any other valid budget. A stored non-preset value automatically reopens as Custom. Settings are fully validated before mutation. Presets are private to your chat; replacing/deleting a preset requires confirmation.

## Sessions, personas and worlds

Sessions can be searched, created, renamed, selected and deleted. Deletion is a background operation and refuses the active session or sessions with pending work. Persona creation/editing/selection/deletion uses the native integrity-checked PersonaService. An existing native avatar is required for new personas; the installer creates a starter avatar for fresh installations.

Worlds offers a JSON editor/file import limited to 1 MB and 2000 entries, multi-file session selection, revision-checked save and backed-up deletion. Files active in any session cannot be deleted. Native personas and World Info are shared administrator-managed resources, not tenant-private files.

## Memory and Data Bank

Memory is session-scoped. Continuity summaries and curated facts can be reviewed and edited locally with revision checks. Save local list does not silently alter Hindsight: Sync reviewed list explicitly publishes the saved list, and Clear session Hindsight memory performs a confirmed external purge. Curate new messages and summary regeneration use the configured Utility model. Provider/connection failures are not treated as successful synchronization.

Data Bank documents are private to the authenticated bot chat. Uploading the same filename creates a version; users can search, activate an older version, remove all copies of a filename, or reindex. Upload and provider work run as durable-status operations outside database transactions. Supported file types match the bridge document parser, with a 10 MB input limit. The app reports indexed/total counts without claiming an unavailable embedding backend is healthy.

## System and verified updates

The dashboard distinguishes immutable running-version/commit evidence from the version of installed files. Telegram status comes from successful polling observations, not a guessed connected flag. Operations show actor-owned queued/running/succeeded/failed/interrupted outcomes. Release review produces a five-minute actor-bound confirmation; applying consumes it once and calls the canonical signed-release updater, not arbitrary commands. Dependency changes still require manual installation.

Before restart is scheduled, a protected pending notification binds the target version and verified commit. The newly started process acknowledges only after its first successful Telegram poll. Invalid or older-than-one-day state is discarded; delivery failure can retry while polling. A process crash between send and marker deletion can duplicate a notification: this is best-effort one-shot delivery, not an exactly-once network guarantee. The Mini App similarly waits for a new boot, matching revision and resumed polling before claiming update completion.

## Installer options and external prerequisites

Run `./install.sh --system-deps --no-start` as the non-root bridge user. Fill the
`.env` bot/provider values, leaving `SILLYTAVERN_MINIAPP_PUBLIC_URL` blank for
discovery. Install Tailscale **1.52+**, authenticate the device and run:

```bash
./install.sh --with-tailscale-funnel --linger
```

The installer prepares user-local uv/Python, locked runtime packages, a starter
PNG/avatar, native directories and a user systemd unit. It does not install
Tailscale, a separate SillyTavern frontend or a Hindsight server. `--system-deps`
installs only Debian/Ubuntu base prerequisites. Tailscale needs external setup:

- Follow [official Linux installation](https://tailscale.com/download/linux), then
  use `sudo tailscale up` if the device has not been authenticated.
- Enable MagicDNS, HTTPS certificates and the `funnel` node attribute for this
  device. A tailnet administrator must authorize policy changes.
- Run as a Tailscale-authorized operator. An administrator can set
  `sudo tailscale set --operator="$USER"`; do not run the bridge installer as root.

Funnel terminates TLS in `tailscaled` and uses the node's `*.ts.net` name, without
custom DNS or inbound public ports. Certificate names are public in transparency
logs, so avoid sensitive device names. Funnel is public even for clients without
Tailscale; Serve is tailnet-only. Funnel remains beta with non-configurable bandwidth
limits. See the [Funnel guide](https://tailscale.com/docs/features/tailscale-funnel)
and [CLI reference](https://tailscale.com/docs/reference/tailscale-cli/funnel).

### Safe discovery and activation

The installer reads node identity and Serve/Funnel status. A blank URL reuses only
an exact already-public root proxy to the Mini App, or selects the first unused
HTTPS port from **443, 8443, 10000**. It never converts private Serve services into
public services or knowingly overwrites another root/path/TCP/foreground listener.
An explicit URL selects its exact port and a conflict is refused. Clear only the
URL assignment to request fresh discovery; changing the backend port does not
silently overwrite an old Funnel. Review that exact listener first.

Only a blank URL is filled; other `.env` assignments and private permissions are preserved.
Both paths pass unchanged through the same direct root proxy:

```text
https://device.tailnet.ts.net[:port]/miniapp/ → http://127.0.0.1:8787/miniapp/
https://device.tailnet.ts.net[:port]/api/v1/… → http://127.0.0.1:8787/api/v1/…
```

After service startup, the installer requires the expected shell and a 401 from
`/api/v1/me` without credentials, rechecks listener conflicts, then runs
`tailscale funnel --bg --https=PORT http://127.0.0.1:BACKEND` and verifies the exact
public mapping. Tailscale may require interactive HTTPS/Funnel approval. A failure
exits with an error, never a false completion. No Serve/Funnel reset or unrelated
service removal is attempted. Avoid concurrent manual Serve changes during setup.
Mapping verification does not prove public DNS propagation or every Telegram
client's connectivity. Public DNS propagation can delay access after provisioning.

The script never sources `.env`. It preserves private settings and native files;
generated provider YAML changes only while its recorded digest matches. Custom
service units require `--replace-service` and are backed up. Dependencies cannot
be modified while the service is active. `--no-deps` reuses a compatible venv;
`--no-start` leaves the bridge and existing Funnel unchanged, but can prepare a
blank URL. `--env-file PATH` selects another private configuration file.

### Upgrade to the Mini App release

Version 0.2.033 changes `requirements.lock` from v0.2.032. The signed `/update`
refuses dependency changes, so install from a clean checkout explicitly:

```bash
systemctl --user stop sillytavern-telegram.service
cd ~/sillytavern-telegram-bridge
git fetch origin
git switch main
git pull --ff-only origin main
./install.sh --with-tailscale-funnel --linger
```

Do not reset the database or replace `.env`. A prior custom-domain URL is preserved;
review and clear only that assignment to let Funnel choose the node URL. The old
proxy installer/configuration generator has been removed. Existing system proxy
services/packages are left for their operator to manage, not automatically purged.

For subsequent automatic signed releases, obtain the maintainer's public SSH key
independently and set `SILLYTAVERN_UPDATE_PUBLIC_KEY` and an external
`SILLYTAVERN_UPDATE_ALLOWED_SIGNERS` path, then rerun preparation. Never provide a
private key or put the trust file inside source/live directories. Without trust
setup, manual installation and the Mini App work; automatic update stays refused.

### Status and targeted shutdown

```bash
tailscale funnel status
systemctl --user status sillytavern-telegram.service
journalctl --user -u sillytavern-telegram.service -n 80 --no-pager
```

Funnel `--bg` persists through reboot and Tailscale restart. To turn off only this
listener, use its printed HTTPS port; 443 is an example, not necessarily your port:

```bash
tailscale funnel --https=443 off
```

Do not use `funnel reset` or `serve reset`; they can disrupt unrelated services.

## Verification and troubleshooting

Use `journalctl --user -u sillytavern-telegram.service -n 80 --no-pager` for operational errors. A 401 means the signed launch expired or is not authorized; reopen from Telegram. A 409 indicates a stale session/revision or missing confirmation; refresh before retrying. A 429 means one of your operations is still pending; inspect System → Operations rather than resubmitting. “Interrupted” means the process restarted, not that side effects were automatically rolled back.

The UI smoke harness is a development-only DOM test. Run `npm ci --prefix tests/miniapp-ui --ignore-scripts`, set `MINIAPP_JSDOM_ROOT=tests/miniapp-ui` and `PYTHON` to the project test interpreter, then run `node --experimental-vm-modules tools/miniapp_ui_smoke.mjs` with Node 24 or newer. CI runs this locked, development-only test harness automatically. It starts a temporary authenticated loopback fixture, renders all ten pages and verifies model/session mutations plus saved optimizer-preview resumption and application, token/empty states, exact large counts and out-of-order navigation. No Node runtime is needed for production. It does not replace testing the deployment on actual Telegram mobile/desktop clients.

Completed optimizer previews can be reopened from System → Operations without making another model call. Apply still enforces the originating actor/session and card revision; a switched or expired session fails closed. Update notifications preserve the existing bot's forum-topic scope even though the Mini App itself only manages private chats.

Every rendered management page owns a separate session snapshot for mutations. Navigating, refreshing or changing the global active-session display cannot silently redirect an older form or pending confirmation to a different session. The server rejects stale snapshots; refresh and review before trying again.
