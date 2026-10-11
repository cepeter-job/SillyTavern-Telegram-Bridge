# Local development setup

[Developer handbook](README.md) · Next: [Architecture](architecture.md)

Use a separate source checkout and virtual environment. The first steps below
prepare an offline test environment; they do not install a service or start a
bot. Run commands from the repository root in a Linux Bash shell unless a step
says otherwise.

## Prerequisites

| Requirement | Why it is needed |
|---|---|
| Python 3.11 with `venv` and pip support | Matches the protected Python CI target and checked-in dependency locks |
| Git | Source control and disposable integration-test repositories |
| `ssh-keygen` from OpenSSH | Disposable signature and key fixtures in integration tests |
| Network access for initial dependency installation | Fetches the artifacts identified by the hash-pinned locks |
| Node 24+ and npm, only for Mini App development | Runs the locked DOM and browser test projects |

The Python target comes from [CI](../../.github/workflows/ci.yml) and
[pyproject.toml](../../pyproject.toml). Use that target even if another interpreter
is available or an experimental Python workflow exists. For Windows development,
use a Linux environment such as WSL for this documented path; the protected CI
does not establish a native Windows runtime guarantee. Keep your clone and
virtual environment together in that Linux environment.

You do not need systemd, a provider API key, Telegram credentials, Hindsight or
the SillyTavern web frontend to run the normal automated tests. Optional connected
development is covered separately below.

## Clone and install the locked environment

Choose a new development directory, outside any running deployment:

```bash
git clone https://github.com/cepeter-job/SillyTavern-Telegram-Bridge.git
cd SillyTavern-Telegram-Bridge
git switch -c change/my-first-contribution

python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install --require-hashes -r requirements-dev.lock
python -m pip check
```

`change/my-first-contribution` is an example branch name; choose one that describes
your work. The clone is public, so you can study and test it before configuring
write access. [Opening a PR](workflow.md#open-the-pull-request) covers pushing to
your own fork or a repository branch.

The runtime manifest is `requirements.txt`; its resolved, hash-pinned artifacts
are in `requirements.lock`. The development lock contains runtime dependencies
plus the exact tools from `requirements-dev.txt`, constrained to the runtime
versions. Installing only `requirements-dev.txt` does not reproduce CI. This
repository runs directly from the checkout; `pyproject.toml` configures tools and
does not define an editable package installation.

Success means both installation commands exit successfully and `pip check`
reports no broken requirements. Select `.venv/bin/python` in your editor. When
opening a new shell, reactivate the environment and return to the repository
root before running commands.

## Run the first offline checks

With the environment active:

```bash
python --version
python tools/check_dependency_lock.py
python -m pytest -q tests/test_installation_docs.py tests/test_governance.py
python sillytavern_telegram_bridge.py --help
```

Expect Python 3.11, a successful lock check, passing tests and the entrypoint's
usage text. These commands exercise the local development setup without real
service credentials. They are a starting check, not the full merge gate. The
[testing guide](testing.md) explains the full suite, coverage, static analysis,
security checks and UI checks.

The tests construct temporary state and inject collaborators. Do not copy a
production database, bot configuration or real conversation into the checkout
to satisfy a failing fixture. Use the patterns in
[tests/settings_test_support.py](../../tests/settings_test_support.py) and the
nearest test module instead.

## Optional connected development

Use this section only when you need to test a real Telegram interaction or a
provider integration. Use a dedicated development bot, disposable native content
and a separate bridge data directory. One bot must have one active polling
process. The production installation procedure belongs in
[Installation](../installation.md); it is not required to start development from
source.

### Prepare private configuration and native content

Start in a clean development shell so old exported service settings do not
override your new files. From the repository root, create a fresh private
directory and export its paths:

```bash
umask 077
sttb_dev_dir="$(mktemp -d "${TMPDIR:-/tmp}/sttb-dev.XXXXXX")"
cp .env.example "$sttb_dev_dir/.env"
cp config/providers.example.yaml "$sttb_dev_dir/providers.yaml"
mkdir -p "$sttb_dev_dir/native/data/default-user/characters"

export SILLYTAVERN_ENV_FILE="$sttb_dev_dir/.env"
export SILLYTAVERN_BRIDGE_HOME="$sttb_dev_dir/state"
export SILLYTAVERN_DIR="$sttb_dev_dir/native"
export SILLYTAVERN_PROVIDER_CONFIG="$sttb_dev_dir/providers.yaml"
printf '%s\n' "$sttb_dev_dir"
```

These copies target a newly created directory. Save the printed location if you
want to resume the same development session; temporary directories may be
removed by the operating system. For longer work, use a new private directory
you manage and set the same four variables to its absolute paths.

Edit the copied `.env` and provider catalog with your editor:

| Setting or file | What to configure |
|---|---|
| `SILLYTAVERN_TELEGRAM_BOT_TOKEN` | The dedicated development bot's token |
| `SILLYTAVERN_TELEGRAM_ALLOWED_USERS` | Your numeric Telegram user ID; comma-separated IDs are supported |
| Native character directory | Place a disposable valid SillyTavern PNG character card here |
| `SILLYTAVERN_DEFAULT_CHARACTER` | That PNG's filename, relative to the configured character directory |
| Private provider catalog | One actual provider endpoint, transport, model ID and `api_key_env` reference |
| Credential variable named by `api_key_env` | The development credential in the private `.env` |
| `SILLYTAVERN_MODEL` | The matching `provider-id::model-id` from the private catalog |
| `SILLYTAVERN_PROVIDER_ALLOWED_HOSTS` | The exact hostnames required by that provider |

The example provider domains and model IDs are placeholders and cannot serve
requests. Keep Live Sync and the Mini App disabled until they are needed. The
[configuration guide](../configuration.md) owns provider-specific settings,
endpoint restrictions, optional services and native path overrides.

The executable loads the file selected by the process's
`SILLYTAVERN_ENV_FILE`; it does not automatically load a `.env` in the current
directory. Its parser accepts assignments and optional matching quotes, but does
not execute shell expressions or expand `$VARIABLE` references. Existing process
variables win over file values. A path setting such as `~/...` is resolved by
settings loading; other relative configured paths are resolved against the
user's home, not the checkout. Prefer absolute paths for development isolation.

Keep private files owned by the development user and set `.env` permissions to
`600`. The loader rejects symlinks, non-regular files, duplicate assignments and
insecure permissions on POSIX. Details are implemented in
[environment.py](../../bridge/environment.py) and
[settings.py](../../bridge/settings.py).

### Check and run in the foreground

In the same shell with the virtual environment and the four path variables set:

```bash
python sillytavern_telegram_bridge.py --check
python sillytavern_telegram_bridge.py
```

**`--check` makes real network calls.** It validates startup settings and the
selected provider's credential configuration, reads the character, calls
Telegram `getMe`, and authenticates Live Sync when configured. It also initializes
runtime collaborators and private directories. A successful result ends with
`check=ok`; it does not prove the provider can complete a story request. Use
`--help`, the lock checker and automated tests for offline checks. See
[run_check](../../bridge/main.py) for the exact scope.

The second command starts the bot. `python -m bridge.main` uses the same bootstrap.
Use the [first conversation walkthrough](../user-guide.md#your-first-conversation)
in the development bot to verify greeting, provider response and delivery. Stop
the foreground process with Ctrl-C. Startup snapshots configuration, so restart
the development process after editing settings; do not expect an already running
application to reread every environment value.

For Mini App work, start with the isolated fixtures in
[Testing and CI](testing.md) and the
[browser validation guide](../miniapp-browser-validation.md). Real Telegram
launch authentication and client behavior need a later connected acceptance
check.

## Setup troubleshooting

| Symptom | Next step |
|---|---|
| `python3.11` or `venv` is missing | Install the matching interpreter and venv support for your Linux distribution, then recreate only this development environment |
| A package has no compatible wheel or fails on import | Check interpreter, operating system and CPU compatibility; record the failing package before proposing an update to the reviewed locks |
| Hash mismatch during installation | Stop and inspect the source, index and artifact mismatch; keep hash enforcement enabled |
| `ModuleNotFoundError` for a test tool | Activate `.venv` and install the complete development lock; confirm the editor and shell use the same interpreter |
| `ModuleNotFoundError: bridge` | Run `python -m pytest` from the repository root |
| The bot reads the wrong settings | Check the explicitly exported `SILLYTAVERN_ENV_FILE` and inherited process overrides; do not print credentials into a bug report |
| Private environment permission error | Use a regular file owned by your user and `chmod 600` on that specific development `.env` |
| Unexpected external socket attempt in a test | Mock the actual transport boundary; see [test isolation](testing.md) |
| `--check` fails | Read the named setting or service failure; use the dedicated development paths and confirm required endpoints are reachable |

After setup works, continue to [Architecture](architecture.md) and
[Making a change](workflow.md).
