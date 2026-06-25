#!/usr/bin/env bash
# Clean + convert ONE raw recording session into the recordings_cc/ store, in a
# single command. CPU-only (no robot, no cameras, no GPU needed), so it is safe
# to run in the BACKGROUND while the next batch is being captured:
#
#   nohup ./tools/process_recording.sh <session_id> \
#       > recordings_cc/<session_id>.process.log 2>&1 &
#
# Outputs (under robot/franka_xr_teleop/recordings_cc/):
#   cleaned/<session_id>/    motion-trimmed, re-segmented (intermediate, kept)
#   lerobot/<session_id>/    LeRobotDataset v3 (train-ready: parquet + mp4 + meta)
#
# Usage:
#   ./tools/process_recording.sh <session_id> [--primary-camera wrist_d405]
#                                             [--recordings-root DIR] [--cc-root DIR]
#                                             [extra args passed to `main.py convert`]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELEOP_DIR="$(dirname "$SCRIPT_DIR")"                 # robot/franka_xr_teleop
REPO_ROOT="$(cd "$TELEOP_DIR/../.." && pwd)"          # repo root
TRAINING_DIR="$REPO_ROOT/training"
VENV="$REPO_ROOT/lerobot/.venv"

RECORDINGS_ROOT="$TELEOP_DIR/recordings"
CC_ROOT="$TELEOP_DIR/recordings_cc"
PRIMARY_CAMERA="wrist_d405"
DROP_EPISODES=""
EXTRA_CONVERT_ARGS=()

if [ $# -lt 1 ]; then
  echo "usage: $0 <session_id> [--primary-camera NAME] [--recordings-root DIR] [--cc-root DIR] [extra convert args]" >&2
  exit 2
fi
SESSION="$1"; shift
while [ $# -gt 0 ]; do
  case "$1" in
    --primary-camera)  PRIMARY_CAMERA="$2"; shift 2;;
    --drop-episodes)   DROP_EPISODES="$2"; shift 2;;
    --recordings-root) RECORDINGS_ROOT="$2"; shift 2;;
    --cc-root)         CC_ROOT="$2"; shift 2;;
    *)                 EXTRA_CONVERT_ARGS+=("$1"); shift;;
  esac
done

SRC="$RECORDINGS_ROOT/$SESSION"
[ -d "$SRC" ] || { echo "ERROR: session not found: $SRC" >&2; exit 1; }
[ -f "$SRC/episode_events.jsonl" ] || { echo "ERROR: $SRC has no episode_events.jsonl (nothing to segment)" >&2; exit 1; }
[ -f "$VENV/bin/activate" ] || { echo "ERROR: lerobot venv not found at $VENV (see robot/franka_xr_teleop/LEROBOT_VENV_SETUP.md)" >&2; exit 1; }

CLEANED_ROOT="$CC_ROOT/cleaned"
LEROBOT_ROOT="$CC_ROOT/lerobot"
mkdir -p "$CLEANED_ROOT" "$LEROBOT_ROOT"

echo "[process_recording] session=$SESSION  primary_camera=$PRIMARY_CAMERA"
echo "[process_recording]   src     = $SRC"
echo "[process_recording]   cleaned = $CLEANED_ROOT/$SESSION"
echo "[process_recording]   lerobot = $LEROBOT_ROOT/$SESSION"

# shellcheck disable=SC1091
source "$VENV/bin/activate"
cd "$TRAINING_DIR"

echo "[process_recording] (1/3) clean ...${DROP_EPISODES:+  (dropping episodes: $DROP_EPISODES)}"
CLEAN_DROP_ARGS=()
[ -n "$DROP_EPISODES" ] && CLEAN_DROP_ARGS+=(--drop-episodes "$DROP_EPISODES")
python main.py clean "$SESSION" --datasets-root "$RECORDINGS_ROOT" --output-root "$CLEANED_ROOT" --force \
  ${CLEAN_DROP_ARGS[@]+"${CLEAN_DROP_ARGS[@]}"}

echo "[process_recording] (2/3) annotate ..."
python main.py annotate "$SESSION" --datasets-root "$CLEANED_ROOT" --overwrite

echo "[process_recording] (3/3) convert ..."
python main.py convert "$SESSION" --datasets-root "$CLEANED_ROOT" --output-root "$LEROBOT_ROOT" \
  --primary-camera "$PRIMARY_CAMERA" --force ${EXTRA_CONVERT_ARGS[@]+"${EXTRA_CONVERT_ARGS[@]}"}

echo "[process_recording] DONE  ->  $LEROBOT_ROOT/$SESSION"
