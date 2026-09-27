#!/usr/bin/env bash
# Optional user-requested HTTPS setup. Never replace unrelated proxy sites.
set -Eeuo pipefail
[[ $# == 1 && -f "$1" && ! -L "$1" ]] || { echo 'Expected generated regular Caddyfile.' >&2;exit 2; }
GENERATED=$1
head -1 "$GENERATED" | grep -Fxq '# Managed by SillyTavern Bridge install.sh' || { echo 'Not an installer-generated proxy file.' >&2;exit 1; }
if ! command -v caddy >/dev/null; then
  command -v apt-get >/dev/null || { echo 'Install Caddy with your package manager, then rerun --with-caddy.' >&2;exit 1; }
  sudo apt-get update
  sudo apt-get install -y caddy
fi
TMP=$(mktemp -d)
trap 'rm -f -- "$TMP/original" "$TMP/candidate" "$TMP/site-original"; rmdir -- "$TMP"' EXIT
MAIN=/etc/caddy/Caddyfile
SITE=/etc/caddy/conf.d/sillytavern-telegram.caddy
sudo mkdir -p /etc/caddy/conf.d
HAD_MAIN=0; HAD_SITE=0
if sudo test -L "$MAIN" || sudo test -L "$SITE"; then
  echo 'Symlink proxy configuration was preserved; configure it manually.' >&2;exit 1
fi
if sudo test -f "$MAIN"; then HAD_MAIN=1;sudo cat "$MAIN" > "$TMP/original";else : > "$TMP/original";fi
if sudo test -e "$SITE"; then
  sudo head -1 "$SITE" | grep -Fxq '# Managed by SillyTavern Bridge install.sh' || { echo 'Existing unrelated proxy site was preserved.' >&2;exit 1; }
  HAD_SITE=1;sudo cat "$SITE" > "$TMP/site-original"
fi
cp "$TMP/original" "$TMP/candidate"
if ! grep -Fxq 'import /etc/caddy/conf.d/*.caddy' "$TMP/candidate"; then
  printf '\nimport /etc/caddy/conf.d/*.caddy\n' >> "$TMP/candidate"
fi
rollback() {
  if ((HAD_SITE));then sudo install -m 0644 "$TMP/site-original" "$SITE";else sudo rm -f -- "$SITE";fi
  if ((HAD_MAIN));then sudo install -m 0644 "$TMP/original" "$MAIN";else sudo rm -f -- "$MAIN";fi
}
sudo install -m 0644 "$GENERATED" "$SITE"
if ! sudo caddy validate --adapter caddyfile --config "$TMP/candidate"; then
  rollback;echo 'Proxy validation failed; previous files restored.' >&2;exit 1
fi
if ((HAD_MAIN));then sudo cp -p "$MAIN" "$MAIN.backup.$(date +%s)";fi
sudo install -m 0644 "$TMP/candidate" "$MAIN"
if ! sudo systemctl reload-or-restart caddy; then
  rollback;sudo systemctl reload-or-restart caddy || true;exit 1
fi
sudo systemctl enable caddy
echo 'HTTPS proxy installed. DNS must point here and ports 80/443 must be reachable for certificates.'
