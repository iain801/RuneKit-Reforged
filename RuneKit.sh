#!/usr/bin/env bash
# Install project dependencies, build Qt resources, and launch RuneKit.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
export PATH="$HOME/.local/bin:$PATH"

if ! ldconfig -p 2>/dev/null | awk '/libxcb-cursor/{found=1} END{exit !found}'; then
    echo "Missing Qt dependency: install xcb-util-cursor on CachyOS/Arch," >&2
    echo "or libxcb-cursor0 on Debian/Ubuntu, then run this launcher again." >&2
    exit 1
fi

if ! command -v poetry >/dev/null 2>&1; then
    if ! command -v pipx >/dev/null 2>&1; then
        echo "Install Poetry or pipx with your distribution's package manager first." >&2
        exit 1
    fi
    pipx install poetry
fi

# An existing virtualenv may be incomplete or use an older lock file.
if [[ "${RUNEKIT_OPENCL:-1}" != "0" ]]; then
    poetry install --no-interaction --extras gpu
else
    poetry install --no-interaction
fi
poetry run make dev
exec poetry run python main.py "$@"
