#!/bin/bash
# Local installation script for Vibe-Trading.
# Replaces the Docker-based setup to avoid container memory limits / OOM.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "==> Installing Vibe-Trading locally (no Docker)"

# 1. Python environment
if [[ ! -d ".venv" ]]; then
    echo "--> Creating Python virtual environment (.venv)"
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "--> Upgrading pip"
pip install --upgrade pip

echo "--> Installing Python package in editable mode"
pip install -e .

# Optional A-share / deepseek / harmonic extras (pick what you need)
# pip install -e ".[ashare,deepseek,harmonic]"

# 2. Node.js frontend dependencies
if [[ -d "frontend" ]]; then
    echo "--> Installing frontend dependencies"
    cd frontend
    npm install
    cd ..
fi

# 3. Environment config
if [[ ! -f "agent/.env" ]]; then
    if [[ -f "agent/.env.example" ]]; then
        echo "--> Copying agent/.env.example -> agent/.env"
        cp agent/.env.example agent/.env
        echo "    IMPORTANT: Edit agent/.env and set your LLM provider API key."
    else
        echo "    WARNING: agent/.env.example not found; please create agent/.env manually."
    fi
fi

echo ""
echo "==> Installation complete."
echo "    Activate venv: source .venv/bin/activate"
echo "    Start services: ./scripts/start_local.sh"
echo "    Or production:  ./scripts/start_local_prod.sh"
