#!/usr/bin/env bash
# NAVIGUIDE — Stop all local services
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_ROOT/logs"

for SERVICE in naviguide-api orchestrator weather-routing frontend; do
    PID_FILE="$LOG_DIR/$SERVICE.pid"
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        kill "$PID" 2>/dev/null && echo "Stopped $SERVICE (PID $PID)" || echo "$SERVICE not running"
        rm -f "$PID_FILE"
    fi
done

# Also free the ports
for PORT in 8000 3008 3010 5173; do
    lsof -ti tcp:"$PORT" | xargs kill -9 2>/dev/null || true
done
echo "All NAVIGUIDE services stopped."
