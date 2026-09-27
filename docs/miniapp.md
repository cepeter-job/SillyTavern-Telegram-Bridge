# Telegram Mini App

The optional Mini App manages the bridge inside Telegram. Chat continues through the existing bot. Configure `SILLYTAVERN_MINIAPP_PUBLIC_URL=https://your-domain.example/miniapp/`; a public HTTPS reverse proxy must forward `/miniapp/*` and `/api/v1/*` to `127.0.0.1:8787`. An empty URL disables the listener. Never expose its HTTP port directly.

Only IDs in `SILLYTAVERN_TELEGRAM_ALLOWED_USERS` are admitted. Open the Bridge menu from the private bot chat. The API validates signed Telegram initData on every request (default lifetime one hour); reopen from Telegram when expired. Direct browser access intentionally has no login bypass. Credentials stay server-side and must not be included in the public URL. Main Mini App profile/deep links additionally require configuration through BotFather.

Private sessions use the authenticated user ID. Group chats and forum topics are not implicitly authorized by a launch link. Allowed users administer shared native character, persona and world files; use separate bridge instances for mutually untrusted users.

The static UI uses native ES modules and needs no Node runtime or build. It ships inside `bridge/miniapp_assets` and is included in verified live-mirror updates.

## Characters and optimizer

Use Characters to browse/search PNG cards, view portraits/info, upload cards and create a new normal conversation session. Selecting a character creates a new session instead of changing existing conversation history; send `/start` in Telegram for its opening. Active/default/referenced characters cannot be deleted; deletion verifies a backup and the revision you reviewed.

Optimizer uses the configured Utility model and supports an optional Manual suggestion. It returns an original/proposed preview. Apply consumes the actor/session-bound proposal once and verifies the original digest; Discard leaves the card untouched. Existing-filename uploads similarly require an explicit replacement preview. Simple upload filenames must not contain path or wildcard characters.

Slow work is admitted to the existing bounded utility executor and has an actor-owned operation ID. Identical retries return the existing operation rather than spending twice. Interrupted operations are marked after restart, never silently replayed.

## Models and generation

Models displays provider/model names only, never private provider configuration. Story and Utility selections are scoped to the current session; Utility can inherit Story. Search filters up to 500 results. Generation uses the same canonical limits as Telegram: temperature 0–2, top-p 0–1, output tokens 1–16000, frequency/presence penalties -2–2, reasoning budget 0–32000 and at most four stop sequences of 100 characters. Settings are fully validated before mutation. Presets are private to your chat; replacing/deleting a preset requires confirmation.

## Sessions, personas and worlds

Sessions can be searched, created, renamed, selected and deleted. Deletion is a background operation and refuses the active session or sessions with pending work. Persona creation/editing/selection/deletion uses the native integrity-checked PersonaService. An existing native avatar is required for new personas; the installer creates a starter avatar for fresh installations.

Worlds offers a JSON editor/file import limited to 1 MB and 2000 entries, multi-file session selection, revision-checked save and backed-up deletion. Files active in any session cannot be deleted. Native personas and World Info are shared administrator-managed resources, not tenant-private files.

## Memory and Data Bank

Memory is session-scoped. Continuity summaries and curated facts can be reviewed and edited locally with revision checks. Save local list does not silently alter Hindsight: Sync reviewed list explicitly publishes the saved list, and Clear session Hindsight memory performs a confirmed external purge. Curate new messages and summary regeneration use the configured Utility model. Provider/connection failures are not treated as successful synchronization.

Data Bank documents are private to the authenticated bot chat. Uploading the same filename creates a version; users can search, activate an older version, remove all copies of a filename, or reindex. Upload and provider work run as durable-status operations outside database transactions. Supported file types match the bridge document parser, with a 10 MB input limit. The app reports indexed/total counts without claiming an unavailable embedding backend is healthy.
