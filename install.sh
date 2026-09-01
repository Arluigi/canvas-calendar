#!/usr/bin/env bash
# Install canvas-calendar. macOS only.
set -euo pipefail

if [ "$(uname)" != "Darwin" ]; then
  echo "canvas-calendar supports macOS only." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

uv tool install --force git+https://github.com/Arluigi/canvas-calendar

BIN="$(command -v canvas-calendar || echo "$HOME/.local/bin/canvas-calendar")"
echo
echo "Installed: $BIN"
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "Note: add ~/.local/bin to your PATH, or call it by full path." ;;
esac
echo
echo "Next: canvas-calendar setup"
