#!/usr/bin/env bash
# Installs nginx (if missing) and reverse-proxies it at the runner, so the
# rig is reachable on :80 instead of directly on its own port. Idempotent,
# safe to re-run -- installs nginx once, always rewrites the site config to
# match the current args.
#
#   sudo ./scripts/setup-nginx.sh [--port PORT] [--server-name NAME]
#
# --port: the runner's own port (default 8000, flyball-runner's default;
#   pass the same value you gave flyball-runner --port, if you did).
# --server-name: nginx server_name (default `_`, nginx's catch-all -- works
#   for a bare IP or hostname with no DNS setup needed).
#
# The runner itself should stay bound to loopback only (flyball-runner's
# own default, no --host flag) -- this script makes it reachable on the
# network *through* nginx, not directly, so nginx is the only thing that
# needs a firewall hole opened for it, if any.
set -euo pipefail

port=8000
server_name=_
while [ $# -gt 0 ]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --server-name) server_name="$2"; shift 2 ;;
    *) echo "usage: sudo $0 [--port PORT] [--server-name NAME]" >&2; exit 2 ;;
  esac
done

if [ "$EUID" -ne 0 ]; then
  echo "run as root: sudo $0" >&2
  exit 2
fi

SITE=/etc/nginx/sites-available/flyball-humidity

if ! command -v nginx >/dev/null 2>&1; then
  echo "==> installing nginx"
  apt-get update -qq
  apt-get install -y nginx
fi

echo "==> writing $SITE (port $port, server_name $server_name)"
cat > "$SITE" <<CONF
server {
    listen 80;
    server_name $server_name;

    location /ws/ {
        proxy_pass http://127.0.0.1:$port;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    location / {
        proxy_pass http://127.0.0.1:$port;
    }
}
CONF

echo "==> enabling the site"
ln -sf "$SITE" /etc/nginx/sites-enabled/flyball-humidity
if [ -e /etc/nginx/sites-enabled/default ]; then
  rm -f /etc/nginx/sites-enabled/default
  echo "  disabled nginx's stock default site (unlinked, not deleted --"
  echo "  still at /etc/nginx/sites-available/default if you want it back)"
fi

echo "==> testing and reloading nginx"
nginx -t
systemctl enable --now nginx >/dev/null 2>&1 || true
systemctl reload nginx

echo
echo "Done. The rig should now be reachable on :80 (http://$(hostname -I | awk '{print $1}')/),"
echo "proxied to the runner on 127.0.0.1:$port. Make sure flyball-runner is actually running"
echo "there (no --host flag -- it should stay loopback-only) before testing."
