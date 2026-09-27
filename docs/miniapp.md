# Telegram Mini App

The optional Mini App manages the bridge inside Telegram. Chat continues through the existing bot. Configure `SILLYTAVERN_MINIAPP_PUBLIC_URL=https://your-domain.example/miniapp/`; a public HTTPS reverse proxy must forward `/miniapp/*` and `/api/v1/*` to `127.0.0.1:8787`. An empty URL disables the listener. Never expose its HTTP port directly.

Only IDs in `SILLYTAVERN_TELEGRAM_ALLOWED_USERS` are admitted. Open the Bridge menu from the private bot chat. The API validates signed Telegram initData on every request (default lifetime one hour); reopen from Telegram when expired. Direct browser access intentionally has no login bypass. Credentials stay server-side and must not be included in the public URL. Main Mini App profile/deep links additionally require configuration through BotFather.

Private sessions use the authenticated user ID. Group chats and forum topics are not implicitly authorized by a launch link. Allowed users administer shared native character, persona and world files; use separate bridge instances for mutually untrusted users.

The static UI uses native ES modules and needs no Node runtime or build. It ships inside `bridge/miniapp_assets` and is included in verified live-mirror updates.
