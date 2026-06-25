#!/usr/bin/env bash
# start_collection_session.sh — the ONE command to begin a data-collection batch.
#
# Why this exists: the teleop bridge only *streams* observations over UDP; it does
# NOT save them. If you teleop without the recorder running, every A/B episode is
# lost silently. This launcher couples the two: it guarantees a healthy bridge AND
# a recorder that is *verified to be capturing* (robot.jsonl growing + both D405
# cameras writing frames) BEFORE it tells you to start. If capture isn't live, it
# aborts loudly instead of letting you collect into the void.
#
# Usage:
#   ./tools/start_collection_session.sh <recording-id> [extra recorder args...]
#   ./tools/start_collection_session.sh batch01
#
# It reuses an already-running bridge (the normal 20x20 case — the bridge stays up
# across batches); only starts one if none is running. Ctrl-C stops the recorder
# (auto-splits the session) and leaves the bridge up for the next batch.
#
# Prereqs to record: FCI active, user-stop released, Quest connected to 127.0.0.1
# with the clutch engaged. Cameras must be free (this script kills a stray viewer).
set -euo pipefail

RECORDING_ID="${1:?usage: $0 <recording-id> [extra recorder args...]}"
shift || true
EXTRA_ARGS=("$@")

ROBOT_IP="192.168.1.11"
OBS_PORT="28081"
TELEOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRIDGE_BIN="$TELEOP_DIR/build/cpp/teleop_bridge/franka_xr_teleop_bridge"
RECORDER="$TELEOP_DIR/tools/record_data_collection_session.py"
CAM_VENV="${EE26_CAM_VENV:-$HOME/ee26_cam_venv}"
SESSION_DIR="$TELEOP_DIR/recordings/$RECORDING_ID"
BRIDGE_LOG="$TELEOP_DIR/recordings/${RECORDING_ID}.bridge.log"

say() { echo "[session] $*"; }
die() { echo "[session] ERROR: $*" >&2; exit 1; }

# --- 0. pre-flight: a stray camera viewer holds the D405s (errno 16 on record) ---
if pgrep -f live_camera_view >/dev/null 2>&1; then
  say "killing stray live_camera_view (holds the cameras)"
  pkill -f live_camera_view || true
  sleep 1
fi

# --- 1. bridge: reuse a running one, else start it and wait for health ---
if pgrep -f franka_xr_teleop_bridge >/dev/null 2>&1; then
  say "reusing the running teleop bridge (obs on :$OBS_PORT)"
else
  say "no bridge running — starting one (needs FCI active + user-stop released)"
  [ -x "$BRIDGE_BIN" ] || die "bridge binary missing: $BRIDGE_BIN (build it first)"
  nohup "$BRIDGE_BIN" --robot-ip "$ROBOT_IP" --obs-port "$OBS_PORT" >"$BRIDGE_LOG" 2>&1 &
  say "waiting for bridge to home + go healthy (up to 45s)..."
  healthy=0
  for _ in $(seq 1 45); do
    sleep 1
    pgrep -f franka_xr_teleop_bridge >/dev/null 2>&1 || { tail -8 "$BRIDGE_LOG"; die "bridge died on startup (see $BRIDGE_LOG)"; }
    if grep -Eq "control_command_success_rate=(1|0\.[0-9])" "$BRIDGE_LOG" 2>/dev/null; then healthy=1; break; fi
  done
  [ "$healthy" = 1 ] || { tail -8 "$BRIDGE_LOG"; die "bridge did not report healthy (see $BRIDGE_LOG)"; }
  say "bridge healthy, holding home"
fi

# --- 2. recorder (background so we can verify capture before you collect) ---
[ -f "$CAM_VENV/bin/activate" ] || die "camera venv not found: $CAM_VENV"
[ -x "$RECORDER" ] || die "recorder not found: $RECORDER"
# shellcheck disable=SC1091
source "$CAM_VENV/bin/activate"

say "starting recorder id=$RECORDING_ID (resetting cameras)"
"$RECORDER" --reset-cameras --recording-id "$RECORDING_ID" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} &
REC_PID=$!

cleanup() {
  echo
  say "stopping recorder (auto-splits the session)..."
  kill -TERM "$REC_PID" 2>/dev/null || true
  wait "$REC_PID" 2>/dev/null || true
  say "recorder stopped. bridge left running for the next batch."
  exit 0
}
trap cleanup INT TERM

WRIST_FR="$SESSION_DIR/cameras/wrist_d405/frames.jsonl"
THIRD_FR="$SESSION_DIR/cameras/third_person_d405/frames.jsonl"
ROBOT_JL="$SESSION_DIR/robot.jsonl"

# --- 3. verify capture is LIVE: robot obs + both cameras growing ---
say "verifying capture (cameras reset ~4s, then checking growth, up to 40s)..."
verified=0
for _ in $(seq 1 40); do
  sleep 1
  kill -0 "$REC_PID" 2>/dev/null || die "recorder exited before capture started — check cameras (errno 16 = busy) and the bridge (see the recorder output above)"
  [ -s "$ROBOT_JL" ] && [ -s "$WRIST_FR" ] && [ -s "$THIRD_FR" ] || continue
  r1=$(wc -c <"$ROBOT_JL"); w1=$(wc -l <"$WRIST_FR"); t1=$(wc -l <"$THIRD_FR")
  sleep 1
  r2=$(wc -c <"$ROBOT_JL"); w2=$(wc -l <"$WRIST_FR"); t2=$(wc -l <"$THIRD_FR")
  if [ "$r2" -gt "$r1" ] && [ "$w2" -gt "$w1" ] && [ "$t2" -gt "$t1" ]; then verified=1; break; fi
done

if [ "$verified" != 1 ]; then
  say "!!! CAPTURE NOT VERIFIED — do NOT collect."
  say "    robot.jsonl / wrist frames / third_person frames are not all growing."
  say "    Likely a camera held busy (errno 16) or the bridge not streaming."
  cleanup
fi

echo "============================================================"
echo "  CAPTURE LIVE  —  session: $RECORDING_ID"
echo "    robot.jsonl growing + both D405 cameras writing frames."
echo "  Now: Quest -> 127.0.0.1, engage clutch, then press A to start ep 1."
echo "  (A=start  B=end+rehome  left-X=discard)   Ctrl-C here = stop + split."
echo "============================================================"

# --- 4. hand the foreground to the recorder until you Ctrl-C ---
wait "$REC_PID"
say "recorder finished. bridge left running for the next batch."
