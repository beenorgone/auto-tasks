#!/bin/sh
# Compatibility entry point. Service output belongs in the systemd journal.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SCRIPT_DIR/docsync_watcher.py" "$@"
