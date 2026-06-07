#!/bin/bash
# GroupIQ Watchdog — Keeps the server alive, restarts on crash
# Runs every 60 seconds via launchd

PROJ_DIR="/Users/bgudi536/GroupIQ"
PID_FILE="$PROJ_DIR/data/server.pid"
LOG_FILE="$PROJ_DIR/data/groupiq.log"
PYTHON="$PROJ_DIR/.venv/bin/python3"
if [ ! -f "$PYTHON" ]; then PYTHON=$(which python3); fi

# Check if server is responding
if ! curl -sSk "https://localhost:5556/bookings" -o /dev/null --max-time 5 2>/dev/null; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Server down — restarting..." >> "$LOG_FILE"
    
    # Kill stale process
    if [ -f "$PID_FILE" ]; then
        kill $(cat "$PID_FILE") 2>/dev/null
    fi
    
    # Restart
    cd "$PROJ_DIR"
    $PYTHON web/server.py >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Server restarted (PID $!)" >> "$LOG_FILE"
fi
