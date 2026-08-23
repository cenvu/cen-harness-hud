#!/bin/sh
# cen-harness-hud bootstrap — tiny POSIX sh. No network, no sudo, no
# package managers, no config surgery here; it only locates a usable
# python3 and execs the stdlib installer CLI.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if ! command -v python3 >/dev/null 2>&1; then
    echo "install.sh: python3 not found in PATH" >&2
    exit 1
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    echo "install.sh: python3 is too old or unusable" >&2
    exit 1
fi

if ! python3 -c 'import tomllib' >/dev/null 2>&1; then
    echo "install.sh: NOTE: this python3 lacks tomllib (needs >= 3.11)." >&2
    echo "  Codex/Herdr config patching will refuse to run (fail-closed)." >&2
    echo "  AGY and Pi components are unaffected." >&2
fi

exec python3 "$SCRIPT_DIR/bin/cen-hud" install --source-root "$SCRIPT_DIR" "$@"
