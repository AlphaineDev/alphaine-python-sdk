#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v uv >/dev/null 2>&1; then
  uv tool install --force --reinstall --refresh "$SCRIPT_DIR"
else
  python3 -m pip install --user --force-reinstall --no-cache-dir "$SCRIPT_DIR"
fi

if command -v alphaine >/dev/null 2>&1; then
  echo "Alphaine CLI installed:"
  alphaine --help >/dev/null
  command -v alphaine
  exit 0
fi

echo "Alphaine CLI was installed, but 'alphaine' is not on PATH yet."

if command -v uv >/dev/null 2>&1; then
  UV_BIN_DIR="$(uv tool dir --bin 2>/dev/null || true)"
  if [ -n "$UV_BIN_DIR" ]; then
    echo "Add this to your shell rc file:"
    echo "  export PATH=\"$UV_BIN_DIR:\$PATH\""
    exit 0
  fi
fi

PY_USER_BASE="$(python3 -m site --user-base)"
echo "Add this to your shell rc file:"
echo "  export PATH=\"$PY_USER_BASE/bin:\$PATH\""
