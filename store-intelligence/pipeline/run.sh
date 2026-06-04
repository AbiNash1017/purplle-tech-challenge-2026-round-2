#!/bin/bash
# =============================================================================
# Store Intelligence Pipeline — Entry Point
# =============================================================================
# Usage:
#   In Docker:   entrypoint is set to this script automatically
#   On Linux/macOS:  bash run.sh
#   On Windows:  Run from Git Bash: bash run.sh
#                Or use PowerShell: .\venv\Scripts\python simulate.py --loop --speed 1.5
# =============================================================================

# Exit immediately on error, treat unset variables as errors
set -euo pipefail

echo ""
echo "============================================="
echo "  Store Intelligence Pipeline Runner"
echo "============================================="

# Resolve the directory this script lives in so we can find Python files
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Read environment mode — defaults to SIMULATED if not set
MODE="${EXECUTION_MODE:-SIMULATED}"
API_URL="${API_URL:-http://localhost:8000}"
SPEED="${PIPELINE_SPEED:-1.5}"

echo "  Mode    : $MODE"
echo "  API URL : $API_URL"
echo "  Speed   : ${SPEED}x"
echo "============================================="
echo ""

# Wait for the API to be reachable before starting (important in Docker compose)
echo "Waiting for API at $API_URL/health ..."
MAX_RETRIES=30
RETRY=0
until curl -sf "$API_URL/health" > /dev/null 2>&1; do
    RETRY=$((RETRY + 1))
    if [ "$RETRY" -ge "$MAX_RETRIES" ]; then
        echo "ERROR: API not reachable after $MAX_RETRIES attempts. Exiting."
        exit 1
    fi
    echo "  API not ready yet (attempt $RETRY/$MAX_RETRIES), retrying in 3s..."
    sleep 3
done
echo "API is reachable. Starting pipeline..."
echo ""

if [ "$MODE" = "LIVE" ]; then
    echo "Running in LIVE video processing mode (YOLOv11 + ByteTrack)..."
    exec python video_processor.py
else
    echo "Running in SIMULATED event playback mode (JSONL replay)..."
    exec python simulate.py --loop --speed "$SPEED" --api-url "$API_URL"
fi
