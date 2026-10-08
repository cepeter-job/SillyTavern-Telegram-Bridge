#!/usr/bin/env bash
# Pinned upstream MIT CLI; do not replace a failed secret scan with success.
set -euo pipefail
umask 077

version=8.30.1
archive="gitleaks_${version}_linux_x64.tar.gz"
# GitHub release asset 378332058; reviewed upstream SHA256, not a runtime checksum download.
checksum=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
repo_root=$(git rev-parse --show-toplevel)
work=$(mktemp -d "${RUNNER_TEMP:-/tmp}/sttb-gitleaks.XXXXXX")
trap 'rm -rf -- "$work"' EXIT

curl --proto '=https' --tlsv1.2 --fail --location --retry 2 --max-time 120 \
  "https://github.com/gitleaks/gitleaks/releases/download/v${version}/${archive}" \
  --output "$work/$archive"
printf '%s  %s\n' "$checksum" "$work/$archive" | sha256sum --check --strict -
tar --extract --gzip --file "$work/$archive" --no-same-owner --no-same-permissions \
  -C "$work" gitleaks LICENSE
chmod 700 "$work/gitleaks"
test "$("$work/gitleaks" version)" = "$version"
cd "$repo_root"
"$work/gitleaks" git --redact=100 --exit-code=1 --log-opts="--all" .
