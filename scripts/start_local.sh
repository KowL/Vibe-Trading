#!/bin/bash
# Start Vibe-Trading locally (development mode).
# Backend API server + Vite frontend dev server.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

VENV_DIR="${VIBE_TRADING_VENV:-.venv}"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "ERROR: Virtual env not found at $VENV_DIR. Run ./scripts/install_local.sh first." >&2
    exit 1
fi

source "$VENV_DIR/bin/activate"

# Load env if present
if [[ -f "agent/.env" ]]; then
    set -a
    source agent/.env
    set +a
fi

BACKEND_PORT="${VIBE_TRADING_PORT:-8899}"
FRONTEND_PORT="${VIBE_TRADING_FRONTEND_PORT:-5899}"

# Preflight: ensure adshare is reachable if ADSHARE_URL points to localhost
if [[ "${ADSHARE_URL:-http://localhost:8000}" == *"localhost"* ]] || [[ "${ADSHARE_URL:-}" == *"127.0.0.1"* ]]; then
    if ! curl -s "${ADSHARE_URL:-http://localhost:8000}/health" >/dev/null 2>&1; then
        echo "WARNING: adshare does not seem to be running at ${ADSHARE_URL:-http://localhost:8000}." >&2
        echo "         A-share data features will fail until adshare is started." >&2
    fi
fi

mkdir -p "$PROJECT_ROOT/.local/logs"

# Start backend
nohup vibe-trading serve --host 0.0.0.0 --port "$BACKEND_PORT" \
    > "$PROJECT_ROOT/.local/logs/backend.log" 2>&1 &
BACKEND_PID=$!
echo $BACKEND_PID > "$PROJECT_ROOT/.local/backend.pid"
echo "--> Backend started (PID $BACKEND_PID) on http://localhost:$BACKEND_PORT"

# Start frontend dev server
cd frontend
nohup npx vite --host 127.0.0.1 --port "$FRONTEND_PORT" \
    > "$PROJECT_ROOT/.local/logs/frontend.log" 2>&1 &
FRONTEND_PID=$!
echo $FRONTEND_PID > "$PROJECT_ROOT/.local/frontend.pid"
echo "--> Frontend started (PID $FRONTEND_PID) on http://localhost:$FRONTEND_PORT"

cd "$PROJECT_ROOT"

echo ""
echo "==> Vibe-Trading is running locally."
echo "    Backend:  http://localhost:$BACKEND_PORT"
echo "    Frontend: http://localhost:$FRONTEND_PORT"
echo "    Logs:     $PROJECT_ROOT/.local/logs/"
echo "    Stop:     ./scripts/stop_local.sh"
