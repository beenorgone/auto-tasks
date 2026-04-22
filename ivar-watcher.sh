#!/bin/bash
# =============================================================================
# IVAR Folder Watcher
# Monitors the 2026 folder for new PDF files and auto-runs the accounting sync
#
# SETUP (run once in your terminal):
#   sudo apt install inotify-tools
#   bash ~/path/to/2026/ivar-watcher.sh --install
#
# USAGE:
#   bash ivar-watcher.sh           # run in foreground
#   bash ivar-watcher.sh --install # install as systemd service (auto-starts on login)
#   bash ivar-watcher.sh --stop    # stop the systemd service
#   bash ivar-watcher.sh --status  # check service status
# =============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
FOLDER_2026="$(dirname "$SCRIPT_DIR")"   # one level up: the 2026/ root
SYNC_SCRIPT="$SCRIPT_DIR/ivar-documents-organizer.sh"
LOG_FILE="$SCRIPT_DIR/ivar-watcher.log"
SERVICE_NAME="ivar-watcher"

WATCH_DIRS=(
  "$FOLDER_2026"
  "$FOLDER_2026/01"
  "$FOLDER_2026/02"
  "$FOLDER_2026/03"
  "$FOLDER_2026/04"
  "$FOLDER_2026/05"
  "$FOLDER_2026/06"
  "$FOLDER_2026/07"
  "$FOLDER_2026/08"
  "$FOLDER_2026/09"
  "$FOLDER_2026/10"
  "$FOLDER_2026/11"
  "$FOLDER_2026/12"
)

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# ── INSTALL AS SYSTEMD SERVICE ───────────────────────────────────────────────
install_service() {
  SERVICE_DIR="$HOME/.config/systemd/user"
  mkdir -p "$SERVICE_DIR"

  cat > "$SERVICE_DIR/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=IVAR 2026 Folder Watcher - Auto accounting sync on new files
After=network.target

[Service]
Type=simple
ExecStart=/bin/bash $SCRIPT_DIR/ivar-watcher.sh
Restart=on-failure
RestartSec=5
StandardOutput=append:$LOG_FILE
StandardError=append:$LOG_FILE

[Install]
WantedBy=default.target
EOF

  systemctl --user daemon-reload
  systemctl --user enable "${SERVICE_NAME}.service"
  systemctl --user start "${SERVICE_NAME}.service"

  echo "✓ Service installed and started."
  echo "  Check status: systemctl --user status $SERVICE_NAME"
  echo "  View logs:    tail -f $LOG_FILE"
}

stop_service() {
  systemctl --user stop "${SERVICE_NAME}.service" 2>/dev/null && echo "✓ Stopped."
  systemctl --user disable "${SERVICE_NAME}.service" 2>/dev/null && echo "✓ Disabled."
}

status_service() {
  systemctl --user status "${SERVICE_NAME}.service"
}

# ── ARGUMENT HANDLING ────────────────────────────────────────────────────────
case "${1:-}" in
  --install) install_service; exit 0 ;;
  --stop)    stop_service;    exit 0 ;;
  --status)  status_service;  exit 0 ;;
esac

# ── DEPENDENCY CHECK ─────────────────────────────────────────────────────────
if ! command -v inotifywait &>/dev/null; then
  echo "ERROR: inotifywait not found. Install it with:"
  echo "  sudo apt install inotify-tools"
  exit 1
fi

# ── DEBOUNCE: avoid running sync multiple times for rapid file drops ──────────
LAST_RUN=0
DEBOUNCE_SECS=1200  # 20 minutes

run_sync() {
  local NOW
  NOW=$(date +%s)
  local ELAPSED=$(( NOW - LAST_RUN ))

  if [ $ELAPSED -lt $DEBOUNCE_SECS ]; then
    REMAINING=$(( DEBOUNCE_SECS - ELAPSED ))
    log "Debounce: waiting — next sync allowed in ${REMAINING}s ($(( REMAINING / 60 ))m)"
    return
  fi

  log "New file detected: $1"
  log "Running accounting sync..."
  bash "$SYNC_SCRIPT" >> "$LOG_FILE" 2>&1 && log "Sync complete." || log "Sync had errors — check log."
  LAST_RUN=$(date +%s)
}

# ── BUILD VALID WATCH LIST ───────────────────────────────────────────────────
VALID_DIRS=()
for d in "${WATCH_DIRS[@]}"; do
  [ -d "$d" ] && VALID_DIRS+=("$d")
done

log "=== IVAR Watcher started ==="
log "Watching ${#VALID_DIRS[@]} directories under: $FOLDER_2026"
log "Sync script: $SYNC_SCRIPT"

# ── MAIN WATCH LOOP ──────────────────────────────────────────────────────────
inotifywait -m -r \
  --event close_write \
  --event moved_to \
  --format '%w%f' \
  "${VALID_DIRS[@]}" 2>/dev/null |
while read -r FILEPATH; do
  # Only act on PDF files that match known patterns
  FILENAME=$(basename "$FILEPATH")
  case "$FILENAME" in
    *.pdf|*.PDF)
      # Ignore temp/hidden files
      [[ "$FILENAME" == .* ]] && continue
      run_sync "$FILEPATH"
      ;;
  esac
done
