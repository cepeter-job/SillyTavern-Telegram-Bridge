#!/usr/bin/env bash
# User-scope installer. Private .env values are parsed in Python, never sourced.
set -Eeuo pipefail
umask 077

REPO_URL="https://github.com/cepeter/SillyTavern-Telegram-Bridge.git"
TRUST_LINE='cepeter namespaces="git" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIA+L6kUwaC94495CdAyZWyocRT5u951D4YnXhtceVKky cepeter-release-signing-2026-10-04'
TRUST_FINGERPRINT='SHA256:Au9pahLKr9Wj1ayrHyXAZEO48y/xuVY88dk6zATqYqU'
DEFAULT_SIGNERS="$HOME/.config/sillytavern-telegram/trusted-maintainers"
# Pinned GitHub Ed25519 host key from https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
# This is SSH *server* identity, unrelated to the maintainer release-signing key.
GITHUB_HOST_LINE='github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl'
GITHUB_HOST_FINGERPRINT='SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU'
GITHUB_HOST_PUBLIC='AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl'

usage() {
  cat <<'HELP'
Usage: ./install.sh [options]

With no options in an interactive terminal, a guided multiple-choice installer
auto-detects Linux/system dependencies and an existing SillyTavern installation.

  --no-start               Prepare files without starting the bridge service.
  --no-deps                Reuse an existing .venv; skip Python dependency installation.
  --with-tailscale-funnel  Publish the Mini App through an authenticated Tailscale node.
  --system-deps            Auto-detect and install missing Linux prerequisites.
  --linger                 Enable user services after logout.
  --replace-service        Back up and replace an existing custom bridge unit.
  --release TAG            Clone and verify one signed release tag (advanced/bootstrap).
  --allowed-signers PATH   Externally trusted SSH allowed-signers file used by --release.
  --no-github-known-hosts  Do not configure GitHub's pinned SSH host key.
  --unsafe-main            Explicitly clone unsigned development main (advanced only).
  --env-file PATH          Choose the private .env location.
  --configure              Run guided configuration after preparing files (TTY required).
  --help                   Show this help without changing anything.

Supported package managers for base prerequisites: apt-get, dnf, yum, pacman,
and zypper. Existing SillyTavern Node/npm state is detected but never modified.
The normal guided path installs the pinned maintainer key using trust-on-first-use
from this installer. Use --allowed-signers with an independently provisioned file
when an independent first-key verification boundary is required.
GitHub's published, pinned SSH host key is also added to ~/.ssh/known_hosts
without ssh-keyscan. This does not create an SSH account/authentication key.
HELP
}

START=1
DEPS=1
FUNNEL=0
SYSTEM_DEPS=0
LINGER=0
REPLACE=0
UNSAFE_MAIN=0
INTERACTIVE=0
CONFIGURE=0
REPAIR_DEPS=0
RELEASE=""
GITHUB_KNOWN_HOSTS=1
ALLOWED_SIGNERS_EXPLICIT=0
if [[ -n "${SILLYTAVERN_UPDATE_ALLOWED_SIGNERS:-}" ]]; then
  ALLOWED_SIGNERS_EXPLICIT=1
fi
ALLOWED_SIGNERS="${SILLYTAVERN_UPDATE_ALLOWED_SIGNERS:-$DEFAULT_SIGNERS}"
# An explicit relative trust path must keep its meaning after cd to the source.
if [[ "$ALLOWED_SIGNERS" != /* ]]; then
  ALLOWED_SIGNERS="$PWD/$ALLOWED_SIGNERS"
fi
ENV_FILE="${SILLYTAVERN_ENV_FILE:-$HOME/.local/share/sillytavern-telegram/.env}"
OS_RELEASE_FILE="${SILLYTAVERN_INSTALL_OS_RELEASE_FILE:-/etc/os-release}"
ORIGINAL_ARGC=$#

while (($#)); do
  case "$1" in
    --help|-h) usage; exit 0;;
    --no-start) START=0;;
    --no-deps) DEPS=0;;
    --with-tailscale-funnel) FUNNEL=1;;
    --system-deps) SYSTEM_DEPS=1;;
    --linger) LINGER=1;;
    --replace-service) REPLACE=1;;
    --release)
      shift
      [[ $# -gt 0 ]] || { echo 'Missing release tag' >&2; exit 2; }
      RELEASE=$1
      ;;
    --allowed-signers)
      shift
      [[ $# -gt 0 ]] || { echo 'Missing allowed-signers path' >&2; exit 2; }
      ALLOWED_SIGNERS=$1
      ALLOWED_SIGNERS_EXPLICIT=1
      ;;
    --unsafe-main) UNSAFE_MAIN=1;;
    --no-github-known-hosts) GITHUB_KNOWN_HOSTS=0;;
    --env-file)
      shift
      [[ $# -gt 0 ]] || { echo 'Missing env path' >&2; exit 2; }
      ENV_FILE=$1
      ;;
    --configure) CONFIGURE=1;;
    *) echo "Unknown option: $1" >&2; usage; exit 2;;
  esac
  shift
done

if ((ORIGINAL_ARGC == 0)) && [[ -t 0 && -t 1 ]]; then
  INTERACTIVE=1
fi

[[ $(uname -s) == Linux ]] || { echo 'This installer targets Linux with user systemd.' >&2; exit 1; }
[[ $EUID -ne 0 ]] || { echo 'Run as the intended non-root user, not through sudo.' >&2; exit 1; }

PLATFORM_ID=""
PLATFORM_LIKE=""
PLATFORM_NAME="Linux"
PACKAGE_MANAGER=""

detect_platform() {
  local key value
  if [[ -r "$OS_RELEASE_FILE" ]]; then
    while IFS='=' read -r key value; do
      value=${value%$'\r'}
      if [[ "$value" == '"'*'"' ]]; then
        value=${value:1:${#value}-2}
      fi
      case "$key" in
        ID) PLATFORM_ID=$value;;
        ID_LIKE) PLATFORM_LIKE=$value;;
        PRETTY_NAME) PLATFORM_NAME=$value;;
      esac
    done < "$OS_RELEASE_FILE"
  fi
  local family=" $PLATFORM_ID $PLATFORM_LIKE "
  case "$family" in
    *" debian "*|*" ubuntu "*) command -v apt-get >/dev/null && PACKAGE_MANAGER=apt-get;;
    *" fedora "*|*" rhel "*|*" centos "*|*" rocky "*|*" almalinux "*)
      if command -v dnf >/dev/null; then PACKAGE_MANAGER=dnf
      elif command -v yum >/dev/null; then PACKAGE_MANAGER=yum
      fi
      ;;
    *" arch "*|*" manjaro "*) command -v pacman >/dev/null && PACKAGE_MANAGER=pacman;;
    *" suse "*|*" opensuse "*) command -v zypper >/dev/null && PACKAGE_MANAGER=zypper;;
  esac
  if [[ -z "$PACKAGE_MANAGER" ]]; then
    for candidate in apt-get dnf yum pacman zypper; do
      if command -v "$candidate" >/dev/null; then
        PACKAGE_MANAGER=$candidate
        break
      fi
    done
  fi
}
detect_platform

package_for() {
  local capability=$1
  case "$PACKAGE_MANAGER:$capability" in
    apt-get:git|dnf:git|yum:git|pacman:git|zypper:git) echo git;;
    apt-get:curl|dnf:curl|yum:curl|pacman:curl|zypper:curl) echo curl;;
    apt-get:ca) echo ca-certificates;;
    dnf:ca|yum:ca) echo ca-certificates;;
    pacman:ca) echo ca-certificates;;
    zypper:ca) echo ca-certificates;;
    apt-get:ssh) echo openssh-client;;
    dnf:ssh|yum:ssh) echo openssh-clients;;
    pacman:ssh|zypper:ssh) echo openssh;;
    apt-get:ffmpeg|pacman:ffmpeg|zypper:ffmpeg|yum:ffmpeg) echo ffmpeg;;
    dnf:ffmpeg) echo ffmpeg-free;;
    *) return 1;;
  esac
}

MISSING_PACKAGES=()
collect_missing_packages() {
  MISSING_PACKAGES=()
  local package
  if ! command -v git >/dev/null; then
    package=$(package_for git 2>/dev/null || true); [[ -n "$package" ]] && MISSING_PACKAGES+=("$package")
  fi
  if ! command -v curl >/dev/null; then
    package=$(package_for curl 2>/dev/null || true); [[ -n "$package" ]] && MISSING_PACKAGES+=("$package")
  fi
  if ! command -v ssh-keygen >/dev/null; then
    package=$(package_for ssh 2>/dev/null || true); [[ -n "$package" ]] && MISSING_PACKAGES+=("$package")
  fi
  if ! command -v ffmpeg >/dev/null; then
    package=$(package_for ffmpeg 2>/dev/null || true); [[ -n "$package" ]] && MISSING_PACKAGES+=("$package")
  fi
  if [[ ! -r /etc/ssl/certs/ca-certificates.crt && ! -r /etc/pki/tls/certs/ca-bundle.crt && ! -r /etc/ssl/ca-bundle.pem ]]; then
    package=$(package_for ca 2>/dev/null || true); [[ -n "$package" ]] && MISSING_PACKAGES+=("$package")
  fi
  if (("${#MISSING_PACKAGES[@]}" > 1)); then
    mapfile -t MISSING_PACKAGES < <(printf '%s\n' "${MISSING_PACKAGES[@]}" | awk '!seen[$0]++')
  fi
}
collect_missing_packages

quick_sillytavern_hint() {
  local candidate
  local -a candidates=()
  [[ -n "${SILLYTAVERN_DIR:-}" ]] && candidates+=("$SILLYTAVERN_DIR")
  candidates+=(
    "$HOME/SillyTavern"
    "$HOME/sillytavern"
    "$HOME/.local/share/SillyTavern"
    "$HOME/apps/SillyTavern"
    "$HOME/Applications/SillyTavern"
  )
  for candidate in "${candidates[@]}"; do
    candidate=${candidate/#\~/$HOME}
    if [[ -f "$candidate/server.js" && -f "$candidate/package.json" && -f "$candidate/config.yaml" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

interactive_menu() {
  local st_hint st_deps choice
  st_hint=$(quick_sillytavern_hint || true)
  if [[ -n "$st_hint" ]]; then
    if command -v node >/dev/null && command -v npm >/dev/null && [[ -d "$st_hint/node_modules" ]]; then
      st_deps="detected (Node/npm + node_modules)"
    else
      st_deps="installation found; Node/npm state incomplete or external"
    fi
  else
    st_hint="not detected yet (starter data will be available)"
    st_deps="not applicable"
  fi
  printf '\nSillyTavern Telegram Bridge Installer\n\n'
  printf 'OS:                    %s\n' "$PLATFORM_NAME"
  printf 'Package manager:       %s\n' "${PACKAGE_MANAGER:-unsupported}"
  printf 'SillyTavern:           %s\n' "$st_hint"
  printf 'SillyTavern deps:      %s\n' "$st_deps"
  printf 'Bridge deps missing:   %s\n\n' "${#MISSING_PACKAGES[@]}"
  cat <<'MENU'
1) Standard install                 [recommended]
2) Install + Tailscale Mini App
3) Prepare only, do not start
4) Repair/reinstall dependencies
5) Advanced options
0) Exit
MENU
  read -r -p 'Select: ' choice
  case "$choice" in
    1) SYSTEM_DEPS=1; LINGER=1;;
    2) SYSTEM_DEPS=1; FUNNEL=1; LINGER=1;;
    3) SYSTEM_DEPS=1; START=0;;
    4) SYSTEM_DEPS=1; REPAIR_DEPS=1; DEPS=1;;
    5)
      cat <<'ADV'
Advanced options:
1) Prepare only and skip Python dependency reinstall
2) Replace an existing custom service unit
3) Configure only after preparation
0) Back to standard install
ADV
      read -r -p 'Select: ' choice
      case "$choice" in
        1) SYSTEM_DEPS=1; START=0; DEPS=0;;
        2) SYSTEM_DEPS=1; REPLACE=1;;
        3) SYSTEM_DEPS=1; CONFIGURE=1;;
        0) SYSTEM_DEPS=1; LINGER=1;;
        *) echo 'Invalid selection.' >&2; exit 2;;
      esac
      ;;
    0) exit 0;;
    *) echo 'Invalid selection.' >&2; exit 2;;
  esac
}

if ((INTERACTIVE)); then
  interactive_menu
fi

if [[ -n "$RELEASE" && $UNSAFE_MAIN -eq 1 ]]; then
  echo '--release and --unsafe-main are mutually exclusive.' >&2
  exit 2
fi
if [[ -n "$RELEASE" && "$RELEASE" != latest && ! "$RELEASE" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo 'Release tag must look like vX.Y.Z.' >&2
  exit 2
fi

install_system_dependencies() {
  collect_missing_packages
  if (("${#MISSING_PACKAGES[@]}" == 0)); then
    echo "System dependencies: already present."
    return
  fi
  [[ -n "$PACKAGE_MANAGER" ]] || {
    echo 'Could not identify a supported package manager. Install git, curl, CA certificates, OpenSSH client and ffmpeg.' >&2
    exit 1
  }
  command -v sudo >/dev/null || { echo 'sudo is required to install missing system packages.' >&2; exit 1; }
  echo "Installing missing system packages with $PACKAGE_MANAGER: ${MISSING_PACKAGES[*]}"
  case "$PACKAGE_MANAGER" in
    apt-get)
      sudo apt-get update
      sudo apt-get install -y "${MISSING_PACKAGES[@]}"
      ;;
    dnf) sudo dnf install -y "${MISSING_PACKAGES[@]}";;
    yum) sudo yum install -y "${MISSING_PACKAGES[@]}";;
    pacman) sudo pacman -Sy --needed --noconfirm "${MISSING_PACKAGES[@]}";;
    zypper) sudo zypper --non-interactive install "${MISSING_PACKAGES[@]}";;
    *) echo "Unsupported package manager: $PACKAGE_MANAGER" >&2; exit 1;;
  esac
}

if ((SYSTEM_DEPS)); then
  install_system_dependencies
fi

for tool in git systemctl; do
  command -v "$tool" >/dev/null || {
    echo "Missing $tool. Use the guided installer/--system-deps or install it first." >&2
    exit 1
  }
done
command -v ssh-keygen >/dev/null || {
  echo 'Missing ssh-keygen. Use the guided installer/--system-deps or install OpenSSH client.' >&2
  exit 1
}
if ((LINGER)); then
  command -v loginctl >/dev/null || { echo 'loginctl is required for --linger.' >&2; exit 1; }
fi

validate_default_trust() {
  local file=$1 fingerprints
  [[ -f "$file" && ! -L "$file" ]] || return 1
  fingerprints=$(awk '$1 == "cepeter" { print $3, $4, $5 }' "$file" | ssh-keygen -lf - 2>/dev/null) || return 1
  [[ "$fingerprints" == *"$TRUST_FINGERPRINT"* ]]
}

ensure_trust_file() {
  if ((ALLOWED_SIGNERS_EXPLICIT)); then
    [[ -f "$ALLOWED_SIGNERS" && ! -L "$ALLOWED_SIGNERS" ]] || {
      echo 'The explicitly supplied allowed-signers path must be an existing regular file.' >&2
      exit 1
    }
    return
  fi
  if [[ -L "$ALLOWED_SIGNERS" ]]; then
    echo "Refusing symbolic-link trust file: $ALLOWED_SIGNERS" >&2
    exit 1
  fi
  if [[ -e "$ALLOWED_SIGNERS" ]]; then
    validate_default_trust "$ALLOWED_SIGNERS" || {
      echo "Existing trust file does not contain the pinned maintainer fingerprint; refusing to overwrite: $ALLOWED_SIGNERS" >&2
      exit 1
    }
    return
  fi
  local directory temporary
  directory=$(dirname -- "$ALLOWED_SIGNERS")
  mkdir -p -- "$directory"
  chmod 700 -- "$directory"
  temporary=$(mktemp "$directory/.trusted-maintainers.XXXXXX")
  printf '%s\n' "$TRUST_LINE" > "$temporary"
  chmod 600 -- "$temporary"
  mv -- "$temporary" "$ALLOWED_SIGNERS"
  validate_default_trust "$ALLOWED_SIGNERS" || {
    rm -f -- "$ALLOWED_SIGNERS"
    echo 'Pinned maintainer key fingerprint validation failed.' >&2
    exit 1
  }
  echo "Trust file installed: $ALLOWED_SIGNERS ($TRUST_FINGERPRINT)"
}

# GitHub's host identity is pinned at install time: never accept an unverified
# network scan, touch other hosts, or replace a conflicting GitHub identity.
ensure_github_known_host() {
  local ssh_dir="$HOME/.ssh" known_hosts="$HOME/.ssh/known_hosts"
  local printed_fingerprint entries existing
  printed_fingerprint=$(printf '%s\n' "$GITHUB_HOST_LINE" | ssh-keygen -lf - 2>/dev/null) || {
    echo 'Cannot inspect the pinned GitHub host key.' >&2
    exit 1
  }
  [[ "$printed_fingerprint" == *"$GITHUB_HOST_FINGERPRINT"* ]] || {
    echo 'The pinned GitHub host key has an unexpected fingerprint.' >&2
    exit 1
  }
  if [[ -L "$ssh_dir" || ( -e "$ssh_dir" && ! -d "$ssh_dir" ) ]]; then
    echo "Refusing unsafe SSH directory: $ssh_dir" >&2
    exit 1
  fi
  if [[ -L "$known_hosts" || ( -e "$known_hosts" && ! -f "$known_hosts" ) ]]; then
    echo "Refusing unsafe SSH known-hosts file: $known_hosts" >&2
    exit 1
  fi
  mkdir -p -- "$ssh_dir"
  chmod 700 -- "$ssh_dir"
  if [[ -f "$known_hosts" ]]; then
    entries=$(ssh-keygen -F github.com -f "$known_hosts" 2>/dev/null || true)
    if printf '%s\n' "$entries" | grep -Eq '^@revoked[[:space:]]'; then
      echo 'GitHub has an explicitly revoked host entry; not changing known_hosts.' >&2
      exit 1
    fi
    # ssh-keygen -F also resolves hashed hostnames in existing known_hosts.
    existing=$(printf '%s\n' "$entries" | awk '$2 == "ssh-ed25519" { print $3 }')
    if [[ -n "$existing" ]]; then
      if [[ "$existing" != "$GITHUB_HOST_PUBLIC" ]]; then
        echo 'GitHub Ed25519 host key differs from the pinned key; not changing known_hosts.' >&2
        exit 1
      fi
      chmod 600 -- "$known_hosts"
      return
    fi
  fi
  # Preserve existing entries (including other key algorithms and hashed hosts).
  # The leading newline also handles existing files without a final newline.
  if [[ -s "$known_hosts" ]]; then
    printf '\n' >> "$known_hosts"
  fi
  printf '%s\n' "$GITHUB_HOST_LINE" >> "$known_hosts"
  chmod 600 -- "$known_hosts"
  echo "Pinned GitHub SSH host key installed: $known_hosts ($GITHUB_HOST_FINGERPRINT)"
}

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SOURCE=$HERE
if [[ ! -f "$SOURCE/bridge/main.py" || ! -f "$SOURCE/requirements.lock" ]]; then
  SOURCE="$HOME/sillytavern-telegram-bridge"
  if [[ -e "$SOURCE" ]]; then
    [[ -f "$SOURCE/bridge/main.py" && -f "$SOURCE/requirements.lock" ]] || {
      echo "Refusing to overwrite $SOURCE" >&2
      exit 1
    }
  elif [[ -n "$RELEASE" || $INTERACTIVE -eq 1 ]]; then
    [[ -n "$RELEASE" ]] || RELEASE=latest
    ensure_trust_file
    git clone --no-checkout -- "$REPO_URL" "$SOURCE"
    TAG=$RELEASE
    if [[ "$TAG" == latest ]]; then
      TAG=$(git -C "$SOURCE" tag --list 'v*' --sort=-version:refname | sed -n '1p')
      [[ -n "$TAG" ]] || { echo 'No release tag found.' >&2; exit 1; }
    fi
    git -C "$SOURCE" -c gpg.format=ssh -c "gpg.ssh.allowedSignersFile=$ALLOWED_SIGNERS" \
      -c gpg.minTrustLevel=fully verify-tag "$TAG"
    RELEASE_COMMIT=$(git -C "$SOURCE" rev-parse "$TAG^{commit}")
    [[ "$RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
      echo 'Signed release did not resolve to a commit.' >&2
      exit 1
    }
    git -C "$SOURCE" checkout -B main "$RELEASE_COMMIT"
    RELEASE=$TAG
  elif ((UNSAFE_MAIN)); then
    git clone --branch main --single-branch -- "$REPO_URL" "$SOURCE"
  else
    echo 'First install requires --release vX.Y.Z, or explicit --unsafe-main for development. Run with no options in a terminal for guided signed installation.' >&2
    exit 2
  fi
fi

cd -- "$SOURCE"
[[ -f bridge/install_support.py ]] || {
  echo 'Update this clean checkout explicitly, then rerun the installer.' >&2
  exit 1
}

# Check explicitly provisioned trust files even on non-interactive installs.
# The effective .env path is reconciled after prepare; never replace a custom one.
if ((ALLOWED_SIGNERS_EXPLICIT)); then
  ensure_trust_file
fi

if ((REPAIR_DEPS)) && systemctl --user is-active --quiet sillytavern-telegram.service; then
  echo 'Stopping the bridge temporarily to repair its Python environment.'
  systemctl --user stop sillytavern-telegram.service
elif ((DEPS)) && systemctl --user is-active --quiet sillytavern-telegram.service; then
  if ((INTERACTIVE)) && [[ -x .venv/bin/python ]]; then
    echo 'Bridge is already running; preserving its current Python environment.'
    DEPS=0
  else
    echo 'Stop the running bridge before installing dependencies, or use --no-deps to preserve its environment.' >&2
    exit 1
  fi
fi

TMP=$(mktemp -d)
trap 'rm -f -- "$TMP/uv-install.sh"; rmdir -- "$TMP" 2>/dev/null || true' EXIT

if ((DEPS)); then
  UV="$(command -v uv || true)"
  if [[ -z "$UV" ]]; then
    command -v curl >/dev/null || {
      echo 'curl is required for user-local uv; use the guided installer/--system-deps.' >&2
      exit 1
    }
    curl --proto '=https' --tlsv1.2 --fail --location --silent --show-error --max-time 90 \
      https://astral.sh/uv/0.12.19/install.sh -o "$TMP/uv-install.sh"
    printf '%s  %s\n' '61b349611f1b6e1ba33645f30c36da5287df2609dd7af8605d96a031435eb35b' "$TMP/uv-install.sh" |
      sha256sum --check --status
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

[[ -x .venv/bin/python ]] || { echo 'No virtual environment. Run without --no-deps.' >&2; exit 1; }
PY="$SOURCE/.venv/bin/python"
ENV_FILE=$("$PY" -c 'import pathlib,sys;print(pathlib.Path(sys.argv[1]).expanduser().absolute())' "$ENV_FILE")
ARGS=(--source "$SOURCE" --home "$HOME" --env "$ENV_FILE" --unit-dir "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user")
if ((REPLACE)); then ARGS+=(--replace-unit); fi

"$PY" -m bridge.install_support prepare "${ARGS[@]}"
# Backfill the update signer setting in older .env files without evaluating them.
# Existing configured custom signer paths are not overwritten or auto-provisioned.
EFFECTIVE_SIGNERS=$("$PY" -m bridge.installer_trust signer-path --env "$ENV_FILE" --home "$HOME" --signers-path "$ALLOWED_SIGNERS")
[[ -n "$EFFECTIVE_SIGNERS" ]] || { echo 'Unable to resolve release signer configuration.' >&2; exit 1; }
if [[ "$EFFECTIVE_SIGNERS" == "$ALLOWED_SIGNERS" ]]; then
  ensure_trust_file
else
  echo "Custom update signer file preserved: $EFFECTIVE_SIGNERS"
fi
if ((GITHUB_KNOWN_HOSTS)); then
  ensure_github_known_host
fi
CHECK_STATUS=0
"$PY" -m bridge.install_support check "${ARGS[@]}" || CHECK_STATUS=$?

if ((CHECK_STATUS == 2)) && ((INTERACTIVE || CONFIGURE)); then
  [[ -t 0 && -t 1 ]] || {
    echo 'Guided configuration requires an interactive terminal.' >&2
    exit 1
  }
  CONFIG_STATUS=0
  "$PY" -m bridge.install_support configure "${ARGS[@]}" || CONFIG_STATUS=$?
  if ((CONFIG_STATUS == 2)); then
    echo "Setup files are ready. Configuration was deferred; rerun ./install.sh when ready."
    exit 0
  elif ((CONFIG_STATUS)); then
    echo 'Guided configuration failed; nothing was started.' >&2
    exit "$CONFIG_STATUS"
  fi
  "$PY" -m bridge.install_support prepare "${ARGS[@]}"
  CHECK_STATUS=0
  "$PY" -m bridge.install_support check "${ARGS[@]}" || CHECK_STATUS=$?
fi

if ((CHECK_STATUS == 2)); then
  echo "Setup files are ready. Fill $ENV_FILE, then rerun this installer."
  exit 0
elif ((CHECK_STATUS)); then
  echo 'Configuration validation failed; nothing was started.' >&2
  exit "$CHECK_STATUS"
fi

if ((FUNNEL)); then
  if ! command -v tailscale >/dev/null; then
    echo 'Tailscale is required for the Mini App option. Install/sign in to Tailscale, then rerun option 2.' >&2
    exit 1
  fi
  "$PY" -m bridge.tailscale_funnel prepare --source "$SOURCE" --home "$HOME" --env "$ENV_FILE"
  "$PY" -m bridge.install_support prepare "${ARGS[@]}"
fi

if ((LINGER)); then
  loginctl enable-linger "$(id -un)" || sudo loginctl enable-linger "$(id -un)"
fi

if ((START)); then
  if ! "$PY" -m bridge.install_support unit-managed "${ARGS[@]}"; then
    echo 'Custom service preserved. Review it or use --replace-service; it was not restarted.'
    exit 0
  fi
  systemctl --user daemon-reload
  systemctl --user enable sillytavern-telegram.service
  systemctl --user restart sillytavern-telegram.service
  systemctl --user is-active --quiet sillytavern-telegram.service
  if ((FUNNEL)); then
    "$PY" -m bridge.tailscale_funnel enable --source "$SOURCE" --home "$HOME" --env "$ENV_FILE"
  fi
  echo 'User service is active. Open the bot in Telegram and use its Bridge menu.'
  echo 'Diagnostics: journalctl --user -u sillytavern-telegram.service -n 80 --no-pager'
else
  echo 'Configuration and user service are ready; --no-start left the bridge and Funnel unchanged.'
fi

if [[ $(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null || true) != yes ]]; then
  echo 'For persistence after logout/reboot, rerun with --linger.'
fi
