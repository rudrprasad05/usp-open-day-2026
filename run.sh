#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8888}"
echo "DrawAI is starting on port ${PORT}"
echo "Laptop display: http://localhost:${PORT}/display"
echo "Phone controller: http://<this-laptop-lan-ip>:${PORT}/draw"
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"

