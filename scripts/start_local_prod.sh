#!/bin/bash
# Start Vibe-Trading locally in production mode.
# Builds the frontend and serves it as static files from the backend.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

VENV_DIR="${VIBE_TRADING_VENV:-.venv}"
if [[ ! -d "$VENV_DIR" ]]; then
    echo "ERROR: Virtual env not found at $VENV_DIR. Run ./scripts/install_local.sh first." >&2
    exit 1
fi

source "$VENV_DIR/bin/activate"

if [[ -f "agent/.env" ]]; then
    set -a
    source agent/.env
    set +a
fi

BACKEND_PORT="${VIBE_TRADING_PORT:-8899}"

# Preflight: ensure adshare is reachable if ADSHARE_URL points to localhost
if [[ "${ADSHARE_URL:-http://localhost:8000}" == *"localhost"* ]] || [[ "${ADSHARE_URL:-}" == *"127.0.0.1"* ]]; then
    if ! curl -s "${ADSHARE_URL:-http://localhost:8000}/health" >/dev/null 2>&1; then
        echo "WARNING: adshare does not seem to be running at ${ADSHARE_URL:-http://localhost:8000}." >&2
        echo "         A-share data features will fail until adshare is started." >&2
    fi
fi

# Build frontend
echo "--> Building frontend"
cd frontend
npm install
npm run build
cd ..

mkdir -p "$PROJECT_ROOT/.local/logs"

# Start backend (serves frontend/dist as static files)
nohup vibe-trading serve --host 0.0.0.0 --port "$BACKEND_PORT" \
    > "$PROJECT_ROOT/.local/logs/backend.log" 2>&1 &
BACKEND_PID=$!
echo $BACKEND_PID > "$PROJECT_ROOT/.local/backend.pid"

echo ""
echo "==> Vibe-Trading is running locally in production mode."
echo "    URL:      http://localhost:$BACKEND_PORT"
echo "    Logs:     $PROJECT_ROOT/.local/logs/backend.log"
echo "    Stop:     ./scripts/stop_local.sh"
