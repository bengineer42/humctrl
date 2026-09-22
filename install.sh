#!/usr/bin/env bash
# Sets this repo up to run: uv (if missing), this repo's Python deps
# (uv sync -- flyball/flyball-linux/flyball-chips come from the pinned
# commit in pyproject.toml, no local flyball checkout needed), and the
# flyball Go CLI, built from that same pinned commit so the CLI and the
# runner are always the same flyball version.
#
#   ./install.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v uv >/dev/null 2>&1; then
  echo "==> installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> uv sync"
uv sync

if grep -qs "Raspberry Pi" /proc/device-tree/model 2>/dev/null; then
  echo "==> Raspberry Pi detected: enabling I2C/PWM (scripts/setup-pi-hardware.sh, needs sudo)"
  sudo ./scripts/setup-pi-hardware.sh "$USER"
fi

if ! command -v go >/dev/null 2>&1; then
  echo "flyball CLI needs Go to build (https://go.dev/doc/install); skipping it -- re-run this script once Go is installed" >&2
  exit 0
fi

rev=$(grep -m1 -oP '(?<=rev = ")[0-9a-f]{40}' pyproject.toml)
echo "==> building the flyball CLI @ ${rev}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git init -q "$tmp"
git -C "$tmp" fetch -q --depth 1 https://github.com/bengineer42/flyball "$rev"
git -C "$tmp" checkout -q FETCH_HEAD
(cd "$tmp/daemon" && go build -o flyball ./cmd/flyball)

mkdir -p "$HOME/.local/bin"
mv "$tmp/daemon/flyball" "$HOME/.local/bin/flyball"
echo "==> installed $HOME/.local/bin/flyball"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "$HOME/.local/bin is not on PATH -- add it (e.g. in .bashrc/.zshrc: export PATH=\"\$HOME/.local/bin:\$PATH\")" >&2 ;;
esac

echo "==> done. Try: uv run flyball-runner rig-multi-sensor.yaml sim.yaml"
