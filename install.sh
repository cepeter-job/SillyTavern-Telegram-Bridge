#!/usr/bin/env bash
# User-scope installer. Private .env values are parsed in Python, never sourced.
set -Eeuo pipefail
umask 077
usage() {
  cat <<'HELP'
Usage: ./install.sh [options]
  --no-start             Prepare files without starting the bridge service.
  --no-deps              Reuse an existing .venv; skip dependency installation.
  --with-caddy           Install/configure HTTPS through Caddy (sudo required).
  --system-deps          Install Debian/Ubuntu prerequisites (sudo required).
  --linger               Enable user services after logout.
  --replace-service      Back up and replace an existing custom bridge unit.
  --env-file PATH        Choose the private .env location.
  --help                 Show this help without changing anything.

First run creates a private .env, starter character/avatar and user systemd unit.
Fill the .env values, then rerun. Mini Apps require public HTTPS, DNS and reachable
ports 80/443. --with-caddy configures the proxy, not DNS or firewall rules.
A running bridge is never upgraded in place: stop it before installing dependencies,
or use --no-deps when its existing environment already satisfies requirements.lock.
HELP
}
START=1; DEPS=1; CADDY=0; SYSTEM_DEPS=0; LINGER=0; REPLACE=0
ENV_FILE="${SILLYTAVERN_ENV_FILE:-$HOME/.local/share/sillytavern-telegram/.env}"
while (($#)); do
  case "$1" in
    --help|-h) usage; exit 0;;
    --no-start) START=0;;
    --no-deps) DEPS=0;;
    --with-caddy) CADDY=1;;
    --system-deps) SYSTEM_DEPS=1;;
    --linger) LINGER=1;;
    --replace-service) REPLACE=1;;
    --env-file) shift; [[ $# -gt 0 ]] || { echo 'Missing env path' >&2;exit 2; }; ENV_FILE=$1;;
    *) echo "Unknown option: $1" >&2;usage;exit 2;;
  esac
  shift
done
[[ $(uname -s) == Linux ]] || { echo 'This installer targets Linux with user systemd.' >&2;exit 1; }
[[ $EUID -ne 0 ]] || { echo 'Run as the intended non-root user, not through sudo.' >&2;exit 1; }
if ((SYSTEM_DEPS)); then
  command -v apt-get >/dev/null || { echo '--system-deps supports Debian/Ubuntu only.' >&2;exit 1; }
  sudo apt-get update
  sudo apt-get install -y git curl ca-certificates openssh-client ffmpeg
fi
for tool in git systemctl; do
  command -v "$tool" >/dev/null || { echo "Missing $tool. Use --system-deps or install it first." >&2;exit 1; }
done
HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SOURCE=$HERE
if [[ ! -f "$SOURCE/bridge/main.py" || ! -f "$SOURCE/requirements.lock" ]]; then
  SOURCE="$HOME/sillytavern-telegram-bridge"
  if [[ -e "$SOURCE" ]]; then
    [[ -f "$SOURCE/bridge/main.py" && -f "$SOURCE/requirements.lock" ]] || { echo "Refusing to overwrite $SOURCE" >&2;exit 1; }
  else
    git clone --branch main --single-branch -- https://github.com/cepeter/SillyTavern-Telegram-Bridge.git "$SOURCE"
  fi
fi
cd -- "$SOURCE"
[[ -f bridge/install_support.py ]] || { echo 'Update this clean checkout explicitly, then rerun the installer.' >&2;exit 1; }
if ((DEPS)) && systemctl --user is-active --quiet sillytavern-telegram.service; then
  echo 'Stop the running bridge before installing dependencies, or use --no-deps to preserve its environment.' >&2
  exit 1
fi
TMP=$(mktemp -d)
trap 'rm -f -- "$TMP/uv-install.sh"; rmdir -- "$TMP"' EXIT
if ((DEPS)); then
  UV="$(command -v uv || true)"
  if [[ -z "$UV" ]]; then
    command -v curl >/dev/null || { echo 'curl is required for user-local uv; use --system-deps.' >&2;exit 1; }
    curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error --max-time 90 \
      https://astral.sh/uv/0.12.19/install.sh -o "$TMP/uv-install.sh"
    printf '%s  %s\n' '61b349611f1b6e1ba33645f30c36da5287df2609dd7af8605d96a031435eb35b' "$TMP/uv-install.sh" | sha256sum --check --status
    UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$TMP/uv-install.sh"
    UV="$HOME/.local/bin/uv"
  fi
  if [[ ! -x .venv/bin/python ]]; then
    "$UV" venv --python 3.11 .venv
  fi
  .venv/bin/python -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11 or newer is required"'
  "$UV" pip install --python "$SOURCE/.venv/bin/python" --require-hashes -r requirements.lock
  "$UV" pip check --python "$SOURCE/.venv/bin/python"
fi
[[ -x .venv/bin/python ]] || { echo 'No virtual environment. Run without --no-deps.' >&2;exit 1; }
PY="$SOURCE/.venv/bin/python"
ENV_FILE=$("$PY" -c 'import pathlib,sys;print(pathlib.Path(sys.argv[1]).expanduser().absolute())' "$ENV_FILE")
ARGS=(--source "$SOURCE" --home "$HOME" --env "$ENV_FILE" --unit-dir "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user")
if ((REPLACE)); then ARGS+=(--replace-unit);fi
"$PY" -m bridge.install_support prepare "${ARGS[@]}"
CHECK_STATUS=0
"$PY" -m bridge.install_support check "${ARGS[@]}" || CHECK_STATUS=$?
if ((CHECK_STATUS==2)); then
  echo "Setup files are ready. Fill $ENV_FILE, then rerun this installer."
  exit 0
elif ((CHECK_STATUS)); then
  echo 'Configuration validation failed; nothing was started.' >&2
  exit "$CHECK_STATUS"
fi
if ((CADDY)); then
  PUBLIC_URL=$("$PY" -m bridge.install_support public-url "${ARGS[@]}")
  [[ -n "$PUBLIC_URL" ]] || { echo 'Set SILLYTAVERN_MINIAPP_PUBLIC_URL before --with-caddy.' >&2;exit 1; }
  PROXY=$("$PY" -m bridge.install_support proxy-path "${ARGS[@]}")
  bash "$SOURCE/tools/install_caddy.sh" "$PROXY"
fi
if ((LINGER)); then
  loginctl enable-linger "$(id -un)" || sudo loginctl enable-linger "$(id -un)"
fi
if ((START)); then
  if ! "$PY" -m bridge.install_support unit-managed "${ARGS[@]}"; then
    echo 'Custom service preserved. Review it or use --replace-service; it was not restarted.';exit 0
  fi
  systemctl --user daemon-reload
  systemctl --user enable sillytavern-telegram.service
  systemctl --user restart sillytavern-telegram.service
  systemctl --user is-active --quiet sillytavern-telegram.service
  echo 'User service is active. Open the bot in Telegram and use its Bridge menu.'
  echo 'Diagnostics: journalctl --user -u sillytavern-telegram.service -n 80 --no-pager'
else
  echo 'Configuration and user service are ready; --no-start left the bridge service unchanged.'
fi
if [[ $(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || true) != yes ]]; then
  echo 'For persistence after logout/reboot, rerun with --linger.'
fi
