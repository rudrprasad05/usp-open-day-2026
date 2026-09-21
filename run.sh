#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${PROJECT_DIR}"

export PORT="${PORT:-8888}"
echo "DrawAI is starting on port ${PORT}"
echo "Laptop display: http://localhost:${PORT}/display"
echo "Phone controller: http://<this-laptop-lan-ip>:${PORT}/draw"
if [[ -x .venv/bin/uvicorn ]]; then
  echo "Using project virtual environment"
  exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
fi
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
