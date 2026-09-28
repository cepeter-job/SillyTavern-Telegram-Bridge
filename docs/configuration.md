# Configuration and providers

### Environment-file parsing

Duplicate keys inside the private environment file are rejected as configuration errors. Existing process environment variables still take precedence over file values. A `#` starts a comment only when it is the first non-whitespace character on a line; `#` inside an assignment value is preserved literally.

## ⚙️ Configuration

The bridge is configured primarily through a private environment file. The
recommended location is:

```text
~/.local/share/sillytavern-telegram/.env
```

Create it from the maintained example and keep it private:

```bash
mkdir -p ~/.local/share/sillytavern-telegram
cp .env.example ~/.local/share/sillytavern-telegram/.env
chmod 600 ~/.local/share/sillytavern-telegram/.env
```

On POSIX, startup rejects an environment file that is not owned by the current
user, is group/other-accessible, is a symlink/non-regular file, exceeds 1 MiB, or
contains invalid UTF-8/NUL data. The complete file is parsed before any values are
applied. On Windows, protect the file with a user-only ACL.

Existing process/systemd environment variables take precedence over values from
the file. Configuration is captured when the bridge starts, so restart the
service after changing `.env`.

> **Special case:** `SILLYTAVERN_ENV_FILE` selects the file *before* that file is
> read. Set it in the process or systemd environment when using a non-default
> path; putting it only inside the alternate file cannot select that same file.

### Minimum required configuration

The bridge validates these values before polling Telegram:

```dotenv
SILLYTAVERN_TELEGRAM_BOT_TOKEN=replace-me
SILLYTAVERN_TELEGRAM_ALLOWED_USERS=123456789
SILLYTAVERN_DEFAULT_CHARACTER=example-character.png
SILLYTAVERN_MODEL=provider-one::provider-one/model-a
```

`SILLYTAVERN_TELEGRAM_ALLOWED_USERS` must contain comma-separated **numeric**
Telegram user IDs. `SILLYTAVERN_DEFAULT_CHARACTER` must name an existing card in
the configured character directory. `SILLYTAVERN_MODEL` uses
`provider-id::model-id` from the private provider catalog. A bare model ID is accepted only when it exactly matches one provider; ambiguous or unknown IDs are refused. Qualified IDs must exist in the provider’s configured `models` or its opted-in discovery cache. Startup validates the route, endpoint policy, and credential before polling; seed the default model in YAML for a first install without a discovery cache.

`SILLYTAVERN_DIR` defaults to `~/.local/share/SillyTavern`; set it when your
SillyTavern installation lives elsewhere.

### Environment variable reference

#### Bot, model, and catalog

| Variable | Default | Purpose |
|---|---|---|
| `SILLYTAVERN_TELEGRAM_BOT_TOKEN` | required | Telegram BotFather token. Secret. |
| `SILLYTAVERN_TELEGRAM_ALLOWED_USERS` | required | Comma-separated numeric Telegram user IDs allowed to use the bot. |
| `SILLYTAVERN_DEFAULT_CHARACTER` | required | PNG character filename used for new/default sessions. |
| `SILLYTAVERN_MODEL` | required | Default Story route in `provider-id::model-id` form. |
| `SILLYTAVERN_DEFAULT_USER_NAME` | empty | Fallback display value for `{{user}}`. |
| `LLM_API_KEY` | empty | Generic provider-key fallback. Prefer a provider-specific `api_key_env`. |
| `SILLYTAVERN_PROVIDER_CONFIG` | `$SILLYTAVERN_BRIDGE_HOME/sillytavern_telegram_providers.yaml` | Private YAML provider catalog. |
| `SILLYTAVERN_MODEL_CACHE` | `$SILLYTAVERN_BRIDGE_HOME/model_catalog_cache.json` | Cache for discovered provider model IDs. |
| `SILLYTAVERN_MODEL_REFRESH_SECONDS` | `3600` | Model discovery cache lifetime; range `1..86400`. |
| `OPENCODE_CLIENT_VERSION` | `1.18.31` | Client-version header used by the OpenCode Muse transport. |
| `SILLYTAVERN_CODEX_AUTH_FILE` | `$SILLYTAVERN_BRIDGE_HOME/codex_oauth.json` | Private rotating OAuth state for the native OpenAI Codex transport. |
| `SILLYTAVERN_CODEX_CLIENT_VERSION` | `1.0` | Version segment used in the Codex request `User-Agent`. |

Provider-specific credential names are intentionally dynamic: whatever string you
put in a catalog entry's `api_key_env` must exist in the private environment, for
example `PROVIDER_ONE_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENAI_API_KEY`.

#### Paths and native SillyTavern data

| Variable | Default | Purpose |
|---|---|---|
| `SILLYTAVERN_ENV_FILE` | `~/.local/share/sillytavern-telegram/.env` | Environment-file selector; set outside the file when overriding. |
| `SILLYTAVERN_BRIDGE_HOME` | `~/.local/share/sillytavern-telegram` | Private bridge data root; database/log paths derive from it. |
| `SILLYTAVERN_BRIDGE_SOURCE_DIR` | current repository root | Git checkout used by signed `/update`. |
| `SILLYTAVERN_LIVE_BRIDGE_DIR` | `$SILLYTAVERN_BRIDGE_HOME/live` | Managed code mirror used by the updater. |
| `SILLYTAVERN_DIR` | `~/.local/share/SillyTavern` | SillyTavern installation/data root used to derive native paths. |
| `SILLYTAVERN_CHARACTER_DIR` | `$SILLYTAVERN_DIR/data/default-user/characters` | Native character cards. |
| `SILLYTAVERN_CHARACTER_BACKUP_DIR` | `$SILLYTAVERN_BRIDGE_HOME/backups/sillytavern/characters` | Character backup destination. |
| `SILLYTAVERN_WORLD_DIR` | `$SILLYTAVERN_DIR/data/default-user/worlds` | Native World Info/lorebooks. |
| `SILLYTAVERN_SYSTEM_PROMPTS_DIR` | `$SILLYTAVERN_DIR/data/default-user/sysprompt` | Native System Prompt directory. |
| `SILLYTAVERN_NATIVE_SETTINGS_FILE` | `$SILLYTAVERN_DIR/data/default-user/settings.json` | Persona names/descriptions and native defaults. |
| `SILLYTAVERN_NATIVE_AVATAR_DIR` | `$SILLYTAVERN_DIR/data/default-user/User Avatars` | Persona avatars. |
| `SILLYTAVERN_ENFORCE_PROMPT_PERMISSIONS` | `false` | Enable prompt-file permission enforcement where supported. |

Derived private paths that do **not** have separate environment variables:

```text
$SILLYTAVERN_BRIDGE_HOME/scripts/sillytavern_telegram.sqlite3
$SILLYTAVERN_BRIDGE_HOME/logs/sillytavern_telegram_bridge.log
$SILLYTAVERN_BRIDGE_HOME/backups/sillytavern/personas/
```

#### Provider and network policy

| Variable | Default | Purpose |
|---|---|---|
| `SILLYTAVERN_PROVIDER_ALLOWED_HOSTS` | empty | Exact external provider/image hostnames. Empty intentionally denies external destinations. |
| `SILLYTAVERN_PROVIDER_PRIVATE_HOSTS` | empty | Separate opt-in for approved LAN/tailnet provider hosts. |
| `SILLYTAVERN_RAG_ALLOWED_HOSTS` | empty | Exact external embedding hostnames. |
| `SILLYTAVERN_RAG_PRIVATE_HOSTS` | empty | Separate LAN/tailnet embedding-host opt-in. |
| `SILLYTAVERN_HINDSIGHT_ALLOWED_HOSTS` | empty | Exact external Hindsight hostnames. |
| `SILLYTAVERN_HINDSIGHT_PRIVATE_HOSTS` | empty | Separate LAN/tailnet Hindsight-host opt-in. |

Host entries are plain exact hostnames: no scheme, path, port, or wildcard. Local
loopback HTTP is allowed for local services. External destinations require HTTPS.
Private/LAN/tailnet destinations require both their normal allowlist and matching
`*_PRIVATE_HOSTS` opt-in.

#### Context planning and diagnostics

| Variable | Default | Valid range / behavior |
|---|---:|---|
| `SILLYTAVERN_CONTEXT_WINDOW_TOKENS` | `32768` | `4096..1000000`; total prompt context window. |
| `SILLYTAVERN_CONTEXT_OUTPUT_RESERVE_TOKENS` | `4096` | `512..131072`; tokens reserved for model output. |
| `SILLYTAVERN_CONTEXT_HISTORY_CANDIDATES` | `96` | `8..512`; recent transcript messages considered before compaction. |
| `SILLYTAVERN_PERF_LOG` | `false` | Boolean (`true/yes/on/1` or `false/no/off/0`); logs low-overhead timing spans. |

The prompt input budget is approximately context window minus output reserve.
When over budget, older history, Data Bank context, Hindsight recall and continuity
summary are reduced before fixed character/system instructions or the current
user turn.

#### Hindsight memory

| Variable | Default | Purpose |
|---|---|---|
| `HINDSIGHT_API_URL` | `http://127.0.0.1:8890` | Hindsight service URL. |
| `HINDSIGHT_API_KEY` | empty | Optional Hindsight credential. Secret. |
| `SILLYTAVERN_HINDSIGHT_ALLOWED_HOSTS` | empty | External Hindsight allowlist. |
| `SILLYTAVERN_HINDSIGHT_PRIVATE_HOSTS` | empty | Private/LAN Hindsight opt-in. |

Hindsight is optional. Session generation only recalls memory scoped to the active
session. Reset/session deletion refuses destructive local cleanup when required
Hindsight cleanup cannot be verified.

#### Data Bank semantic embeddings

| Variable | Default | Valid range / purpose |
|---|---|---|
| `SILLYTAVERN_RAG_EMBEDDING_URL` | `http://127.0.0.1:8891/v1/embeddings` | OpenAI-compatible embeddings endpoint. |
| `SILLYTAVERN_RAG_EMBEDDING_API_KEY` | empty | Dedicated key; required for external embedding endpoints. |
| `SILLYTAVERN_RAG_ALLOWED_HOSTS` | empty | External embedding hostname allowlist. |
| `SILLYTAVERN_RAG_PRIVATE_HOSTS` | empty | Private/LAN embedding-host opt-in. |
| `SILLYTAVERN_RAG_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model ID. |
| `SILLYTAVERN_RAG_EMBEDDING_DIMENSIONS` | `1536` | `1..65536`; vector size. |
| `SILLYTAVERN_RAG_EMBEDDING_REVISION` | `1` | User-controlled embedding revision; change when embeddings become incompatible. |
| `SILLYTAVERN_RAG_MAX_EXTRACTED_CHARS` | `1000000` | `1..10000000`; extraction cap per document. |
| `SILLYTAVERN_RAG_MAX_PDF_PAGES` | `200` | `1..10000`; PDF page cap. |
| `SILLYTAVERN_RAG_PDF_PARSE_TIMEOUT_SECONDS` | `45` | `1..300`; isolated PDF parser timeout. |
| `SILLYTAVERN_RAG_SEMANTIC_CANDIDATES` | `384` | `64..2048`; candidate chunks considered by semantic retrieval. |

Full-text Data Bank search works without embeddings. Reindex documents after
changing embedding model, dimensions, or revision.

#### Live Sync

| Variable | Default | Valid range / purpose |
|---|---|---|
| `SILLYTAVERN_SYNC_API_URL` | empty (off) | SillyTavern Live API base URL. |
| `SILLYTAVERN_SYNC_API_HANDLE` | empty | API account/handle when required. |
| `SILLYTAVERN_SYNC_API_PASSWORD` | empty | API password when required. Secret. |
| `SILLYTAVERN_SYNC_API_TIMEOUT_SECONDS` | `10` | `2..30`; request timeout. |
| `SILLYTAVERN_SYNC_API_INTERVAL_SECONDS` | `2.0` | `1..30`; realtime polling interval. |

Live Sync is disabled until `SILLYTAVERN_SYNC_API_URL` is set. It uses the API,
not chat-file polling or JSONL transfer.

#### Voice

| Variable | Default | Purpose |
|---|---|---|
| `SILLYTAVERN_STT_MODEL` | `base` | Speech-to-text model name. |
| `SILLYTAVERN_TTS_BIN` | `$SILLYTAVERN_BRIDGE_HOME/venv/bin/edge-tts` | `edge-tts` executable path. Override when your executable lives elsewhere. |
| `SILLYTAVERN_TTS_VOICE` | empty | Edge TTS voice; required when TTS output is enabled. |

#### Signed self-update

| Variable | Default | Purpose |
|---|---|---|
| `SILLYTAVERN_UPDATE_ALLOWED_SIGNERS` | unset | External OpenSSH allowed-signers file containing trusted **public** release keys. Required for automatic installation. |
| `SILLYTAVERN_UPDATE_SERVICE` | `sillytavern-telegram.service` | User systemd unit restarted after a verified update. |
| `SILLYTAVERN_BRIDGE_SOURCE_DIR` | repository root | Clean `main` checkout that the updater fast-forwards. |
| `SILLYTAVERN_LIVE_BRIDGE_DIR` | `$SILLYTAVERN_BRIDGE_HOME/live` | Managed mirror replaced after verification/staging. |

The current maintainer release-signing key has fingerprint:

```text
SHA256:nCiZP+h1YWYCFjh37W8tXjR7oWGpZPF6bP4lbTOlAiI
```

Its public allowed-signers record is:

```text
cepeter namespaces="git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGRaxgobK+D+zdXdUzLb1xTQ2EPs9iYkeQGOOlepl+35 cepeter-release-signing
```

Verify the fingerprint against a GitHub **Verified** release tag or another
independent maintainer channel before installing it as trust material. Do not use
a private key as an allowed-signers file and do not store the trust file inside
the source checkout or managed live mirror.

### Environment syntax and validation

The parser accepts `KEY=VALUE` and optional `export KEY=VALUE`. Matching single or
double quotes are removed. Existing process variables win over file values.
Integer/float/boolean validation reports the variable name without printing the
supplied secret value.

Use the maintained `.env.example` as the copyable configuration template. It
contains the same supported user-facing variables documented above.

---

## 🌐 Provider catalog

The bridge uses its own **private YAML provider catalog**; it does not import
SillyTavern provider credentials/settings. Start from the maintained example:

```bash
cp config/providers.example.yaml ~/.local/share/sillytavern-telegram/sillytavern_telegram_providers.yaml
chmod 600 ~/.local/share/sillytavern-telegram/sillytavern_telegram_providers.yaml
```

Point `SILLYTAVERN_PROVIDER_CONFIG` elsewhere if you prefer another private path.
A minimal OpenAI-compatible provider looks like:

```yaml
providers:
  provider-one:
    name: Provider One
    api_endpoint: https://provider.example/v1
    api_key_env: PROVIDER_ONE_API_KEY
    transport: chat_completions
    streaming: true
    models:
      - provider-one/model-a
```

Then place the referenced credential in your private environment file:

```dotenv
PROVIDER_ONE_API_KEY=replace-me
SILLYTAVERN_PROVIDER_ALLOWED_HOSTS=provider.example
```

For native OpenAI Codex OAuth, use an explicit static model list and the Codex
Responses endpoint:

```yaml
providers:
  openai-codex:
    name: OpenAI Codex OAuth
    api_endpoint: https://chatgpt.com/backend-api/codex
    transport: openai_codex
    adapter: openai_codex
    discover_models: false
    models:
      - gpt-5.6-sol
      - gpt-5.6-sol-900k
      - gpt-5.6-terra
      - gpt-5.6-terra-900k
      - gpt-5.6-luna
      - gpt-5.6-luna-900k
```

The `-900k` entries are bridge picker aliases, not OpenAI model IDs. They opt
Sol, Terra, or Luna into a conservative 900,000-token bridge planning ceiling inside the model's larger supported context window; the bridge preserves
the alias in session state for budgeting and strips it only from the Codex wire
request. Other models—including invented `-900k` names—keep the configured
`SILLYTAVERN_CONTEXT_WINDOW_TOKENS` budget and are sent unchanged so invalid
names fail honestly. The output reserve is still subtracted, so the default
900K input budget is 895,904 tokens.

`SILLYTAVERN_CONTEXT_HISTORY_CANDIDATES` remains an independent resource cap.
Increase it, up to 512, if a long-running 900K session should load more than the
default 96 recent transcript messages before compaction.

Allow both OAuth and inference hosts, then complete a separate bridge login:

```dotenv
SILLYTAVERN_PROVIDER_ALLOWED_HOSTS=chatgpt.com,auth.openai.com
```

```bash
python sillytavern_telegram_bridge.py --codex-login
python sillytavern_telegram_bridge.py --codex-status
```

On a VPS, run `--codex-login` in an interactive SSH terminal (`ssh -t` when
needed). Open the displayed OpenAI URL on your phone or computer and enter the
one-time device code. Device-code login must be enabled for your OpenAI account
or workspace. The code is displayed only on the controlling terminal, never on
captured stdout/stderr; do not record or share that terminal session. Without a
controlling terminal, the command can use a configured local browser instead.
Status and errors never display access or refresh tokens.

This native transport accepts only `https://chatgpt.com/backend-api/codex`;
listing another provider in the network allowlist does not authorize sending
Codex OAuth tokens to it. Streams are bounded and require a completion event;
interrupted streams fail rather than committing an incomplete response. Preview
updates are throttled. Cancellation preserves the existing partial-output
contract and avoids a new request when already cancelled.

The native Codex backend does not receive Chat Completions sampling settings or
`max_output_tokens`. The session's numeric reasoning budget maps to a supported
effort label; zero leaves the backend default in effect, rather than promising
reasoning is disabled. Provider timeouts bound individual network requests, not
total wall-clock time. The bridge separately bounds received stream data.

The bridge stores its own rotating token family in a private `0600` JSON file;
it neither copies nor modifies Hermes or Codex CLI credentials. Use
`--codex-logout` to remove it. Codex does not expose the normal provider
`GET /models` contract, so keep `discover_models: false` and update the explicit
model list when your account's available models change.

If the catalog contains no available model IDs, the panel shows setup guidance
rather than inventing fallback providers or models. The Health and Refresh
buttons remain available. For a Chat Completions provider, set `api_endpoint`
(or its accepted `api` alias) explicitly; a missing endpoint produces a
configuration error before any network request or credential attachment.

### Provider catalog field reference

| Field | Default / values | Purpose |
|---|---|---|
| `name` | provider ID | Human-readable label shown in Telegram panels. |
| `api_endpoint` | none | Provider base URL. `api` is accepted as an alias. Remote providers must use HTTPS. |
| `api_key_env` | `LLM_API_KEY` | Environment-variable name that contains this provider's credential. |
| `transport` | `chat_completions` | `chat_completions`/`openai`/`openai_compatible`, `anthropic_messages`, `opencode_muse`, or `openai_codex`. |
| `adapter` | `transport` | Model-menu capability label; normally match the transport. |
| `models` | empty | Explicit model IDs for this provider. Required when discovery is disabled/unavailable. |
| `discover_models` | false | When true, refresh model IDs from `GET /models` and cache them. |
| `streaming` | false | Enable SSE streaming for compatible Chat Completions providers. `stream` is also accepted. |
| `health_check` | `GET /models` | Set `chat_completion` for providers without a useful `/models` endpoint. |
| `extra_headers` | `{}` | Additional HTTP headers merged into provider requests. Do not put secrets here if the YAML might be shared. |
| `anthropic_version` | `2023-06-01` | Anthropic `anthropic-version` header for `anthropic_messages`. |
| `image_enabled` | false | Opt this provider into `/imagine`. |
| `image_endpoint` | `<api_endpoint>/images/generations` | Explicit OpenAI-compatible Images endpoint override. |
| `image_models` | empty | Image model IDs; first item is the provider default for image selection. |

`config/providers.example.yaml` contains normal Chat Completions, Anthropic,
native OpenAI Codex OAuth, OpenCode Muse, and image-provider examples. Only
fields consumed by the current runtime are shown there.

### Model discovery and health checks

`discover_models: true` enables `GET /models` discovery. Results are stored in
`SILLYTAVERN_MODEL_CACHE` and refreshed according to
`SILLYTAVERN_MODEL_REFRESH_SECONDS`. If a provider has no usable `/models`
endpoint, keep explicit `models`, set `discover_models: false`, and optionally set
`health_check: chat_completion`.

The provider panel is the normal user interface:

Open `/providers`, choose **Story** or **Utility**, then use **Provider health**
or **Refresh models** in the provider list. These maintenance actions are
panel-only; typing a provider subcommand returns guidance instead of running it.

### Outbound host policy

The YAML catalog describes **where** to call; it does not grant network trust.
External endpoints must also be present in the corresponding environment
allowlist. An empty external-host list is fail-closed.

```dotenv
SILLYTAVERN_PROVIDER_ALLOWED_HOSTS=provider.example,images.example
SILLYTAVERN_RAG_ALLOWED_HOSTS=embedding.example
SILLYTAVERN_HINDSIGHT_ALLOWED_HOSTS=memory.example
```

Entries are exact hostnames without schemes, paths, ports, or wildcards.
Loopback addresses/`localhost` remain available to local HTTP services. LAN and
tailnet destinations also require the appropriate `*_PRIVATE_HOSTS` entry.
Metadata/link-local, unspecified, multicast, and reserved addresses are refused.

The built-in provider/image/embedding HTTP transport validates DNS addresses,
pins an approved numeric address for the connection, retains the original host
for TLS verification, rejects cross-origin redirects, and does not inherit
OS/environment proxy settings. Hindsight receives the same endpoint-policy
validation before its SDK client is created, but the SDK owns its own transport.

---
