#!/usr/bin/env bash
# Launch the EE26 operator console (read-only — only listens to the bridge obs stream).
# Runs from the repo root so `python -m robot.operator_console.app` resolves.
#
#   ./robot/operator_console/run_console.sh                         # live: UDP 28081 + D405s
#   ./robot/operator_console/run_console.sh --source synthetic      # demo, nothing connected
#   ./robot/operator_console/run_console.sh --source replay --replay recordings/obs.jsonl
#
# Uses ~/ee26_console_venv if present, else whatever python3 is active.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

PY="python3"
if [[ -x "$HOME/ee26_console_venv/bin/python" ]]; then
  PY="$HOME/ee26_console_venv/bin/python"
fi

exec "$PY" -m robot.operator_console.app "$@"
