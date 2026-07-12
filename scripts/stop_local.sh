#!/bin/bash
# Stop locally running Vibe-Trading services.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

stop_pid_file() {
    local pid_file="$1"
    if [[ -f "$pid_file" ]]; then
        local pid
        pid="$(cat "$pid_file")"
        if kill -0 "$pid" >/dev/null 2>&1; then
            echo "--> Stopping process $pid ($(basename "$pid_file"))"
            kill "$pid" || true
            sleep 1
            # Force kill if still alive
            if kill -0 "$pid" >/dev/null 2>&1; then
                kill -9 "$pid" || true
            fi
        fi
        rm -f "$pid_file"
    fi
}

stop_pid_file "$PROJECT_ROOT/.local/backend.pid"
stop_pid_file "$PROJECT_ROOT/.local/frontend.pid"

echo "==> Vibe-Trading local services stopped."
