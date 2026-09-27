# Private Telegram Mini App management surface

## Approved outcome
Deliver the six Mini App waves and a user-scope install.sh, through reviewed/tested pull requests merged to main. The user delegated design and merge decisions and requested only a final report. Ordinary character conversations remain in Telegram.

## Architecture
An opt-in aiohttp server in a dedicated thread of the bridge process binds 127.0.0.1:8787. Tailscale Funnel owns public HTTPS and forwards directly to loopback. Static ES modules live under bridge/miniapp_assets so verified updates include the frontend. No Node runtime/build is required. Startup and shutdown own HTTP resources. API requests open their own canonical SQLite connection and reuse application services; no shared cross-thread connection, provider work inside transactions, or calls back into Telegram menu handlers.

## Security contract
Every /api/v1 request verifies the raw Telegram initData HMAC, constant-time comparison, authentication age (default 3600 seconds, 30 seconds maximum future skew), bounded strict query parsing, and the existing allowed-user set. Never trust initDataUnsafe, client user/chat IDs, chat_instance, or deep links for authorization. The authenticated user's numeric ID is the private-chat scope. Group/forum management is deliberately unsupported. Allowed users are administrators of shared native character/persona/world assets; sessions and memory remain user scoped. Credentials and complete provider configuration never enter HTTP responses, browser storage, URLs, or request logs. API responses are no-store; no CORS. Cross-origin requests and foreign Host headers are rejected. Only fixed static asset names are served. User/model text uses textContent, not HTML. All uploads are size bounded and filename/path checked.

## Consistency and long operations
Session selections are explicit; mutations carry the displayed session ID and are rejected if stale. Shared chat locks coordinate bot and HTTP mutations. Native file changes retain existing preview/digest/backup/unused-reference guards. Expensive optimizer/RAG/update requests produce actor-bound operation records and expose queued/running/succeeded/failed/interrupted state; uncompleted work is not silently replayed on restart. Destructive actions require explicit confirmation; stale revisions and repeated proposal application fail closed. API submission limits and worker admission are bounded.

## Wave contracts
1. Shell/auth: theme/safe-area/navigation/access errors, authenticated bootstrap, loopback lifecycle, menu launch, direct aiohttp dependency and checked lock.
2. Characters/optimizer: search, images, info/ranks, guarded selection/deletion/upload, digest-bound optimizer preview/manual suggestion/apply/discard, long-operation status.
3. Models/generation: sanitized story/utility catalog and selection, bounded numeric settings, stop sequences/reasoning budget, user-scoped presets.
4. Sessions/personas/worlds: list/create/rename/select/delete sessions; existing persona service CRUD and selection; safe world JSON create/edit/select/delete with backups and reference checks.
5. Memory/Data Bank: memory enable/scope and summary/curator inspection/edit/delete; RAG toggle/list/search/upload/versions/activate/remove/reindex, existing RagService as owner.
6. System/install: redacted live status and version/release review, confirmed verified update with restart state, operational documentation, safe re-runnable install.sh (venv, example env, starter resources, user systemd, optional direct Tailscale Funnel configuration).

## Installation boundary
The installer never overwrites a private env/provider/native card, never sources env as shell, never edits the live deployment while authoring this feature, and never silently downgrades code or bypasses signed-update verification. Token, allowed IDs and provider endpoint/key/model are supplied in env. A blank Mini App URL can be discovered from the authenticated Tailscale node and a free supported HTTPS port. Tailscale installation/login and HTTPS/Funnel authorization are external prerequisites; existing private Serve routes must not be converted or replaced. Re-running after filling env validates configuration and starts/enables the user service. Existing installations are preserved.

## Acceptance
Fresh test fixtures prove signed identity, tampering/expiry/duplicate-field rejection, access isolation, no credential disclosure, startup failure cleanup, explicit disabled mode, valid UI asset delivery, safe writes and upload paths, proposal one-shot behavior, numeric validation, operation ownership/restart behavior and installer idempotency. Run focused tests before code (red), full Python suite and static checks per wave, browser/JS smoke checks, and GitHub required checks before merging. No real Telegram or paid model traffic in automated tests.

## Primary references (checked 2026-09-27)
- https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
- https://core.telegram.org/bots/api#menubuttonwebapp
- https://docs.aiohttp.org/en/stable/web_advanced.html
