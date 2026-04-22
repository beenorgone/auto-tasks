#!/bin/bash
# Fix the ivar-watcher systemd service (wrong _sync/_sync path)
# Run once in your terminal: bash ~/Documents/2026/_sync/fix-service.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
SERVICE_NAME="ivar-watcher"
SERVICE_FILE="$HOME/.config/systemd/user/${SERVICE_NAME}.service"
LOG_FILE="$SCRIPT_DIR/ivar-watcher.log"

echo "=== IVAR Watcher Service Fix ==="
echo "Script dir : $SCRIPT_DIR"
echo "Service    : $SERVICE_FILE"
echo ""

# Stop & disable old (broken) service
systemctl --user stop  "${SERVICE_NAME}.service" 2>/dev/null && echo "✓ Stopped old service"  || echo "  (service was not running)"
systemctl --user disable "${SERVICE_NAME}.service" 2>/dev/null && echo "✓ Disabled old service" || true

# Write corrected service file
mkdir -p "$HOME/.config/systemd/user"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=IVAR 2026 Folder Watcher - Auto accounting sync on new files
After=network.target

[Service]
Type=simple
ExecStart=/bin/bash ${SCRIPT_DIR}/ivar-watcher.sh
Restart=on-failure
RestartSec=5
StandardOutput=append:${LOG_FILE}
StandardError=append:${LOG_FILE}

[Install]
WantedBy=default.target
EOF

echo "✓ Service file written: $SERVICE_FILE"
echo "  ExecStart=/bin/bash ${SCRIPT_DIR}/ivar-watcher.sh"
echo ""

# Reload, enable, start
systemctl --user daemon-reload
systemctl --user enable "${SERVICE_NAME}.service"
systemctl --user start  "${SERVICE_NAME}.service"

echo ""
echo "✓ Service restarted. Checking status..."
sleep 2
systemctl --user status "${SERVICE_NAME}.service" --no-pager
