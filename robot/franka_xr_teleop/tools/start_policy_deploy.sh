#!/usr/bin/env bash
# start_policy_deploy.sh — the runner side of SmolVLA deploy.
#
# It wraps tools/run_vla_policy.py with the right defaults for the c1_insertion
# model: the contract camera config, the canonical task prompt, and the 28081/28082
# UDP ports. It does NOT start the bridge — see the kernel note below.
#
# ⚠️ KERNEL / TOPOLOGY: the 1 kHz bridge needs the RT kernel (6.8.0-rt8-franka);
# CUDA torch inference needs the generic kernel (NVIDIA won't build on RT). So the
# policy-mode bridge and a --device cuda runner cannot share the desktop. Real deploy
# is cross-process/cross-machine (see DEPLOY.md):
#   • bridge (RT desktop):  franka_xr_teleop_bridge --robot-ip <ip> \
#                             --control-source policy --policy-action-port 28082 \
#                             --obs-ip <inference-host> --obs-port 28081
#   • runner (this script, on the inference host): sends actions to --bridge-ip.
# On a single GPU box you can still test the wire with --device cpu (slow).
#
# Usage:
#   ./tools/start_policy_deploy.sh [--bridge-ip IP] [--policy-type smolvla|pi0]
#                                  [--policy-path DIR] [--task STR]
#                                  [--device cuda|cpu] [extra run_vla_policy args...]
# Defaults: bridge-ip 127.0.0.1, policy-type smolvla, policy
#           training/outputs/c1_insertion_smolvla, canonical task, device cuda.
#           (For the pi0 checkpoint: --policy-type pi0 --policy-path
#            ../../training/outputs/pretrained_model.) Operator keys (in this terminal):
#           p=pause  h=pause+rehome  r=resume  q=quit.
set -euo pipefail

TELEOP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$TELEOP_DIR/../.." && pwd)"
RUNNER="$TELEOP_DIR/tools/run_vla_policy.py"
CONFIG="$TELEOP_DIR/configs/data_collection.yaml"
VPY="${EE26_LEROBOT_VENV:-$REPO_ROOT/lerobot/.venv}/bin/python"

BRIDGE_IP="127.0.0.1"
OBS_PORT="28081"
ACTION_PORT="28082"
DEVICE="cuda"
POLICY_TYPE="smolvla"   # smolvla | pi0 (both flow-matching, RTC-compatible)
POLICY_PATH="$REPO_ROOT/training/outputs/c1_insertion_smolvla"
TASK="Insert the white cylindrical block into the white socket."  # canonical (see policy_card.md)
# RTC (Real-Time Chunking) ON by default: this checkpoint is smolvla (RTC-compatible,
# chunk_size=50). RTC infers chunks in a background thread and streams them at --rate-hz,
# so motion stays fluent even when a single inference is slow (e.g. --device cpu). Running
# WITHOUT it (sync single-step) collapses to the inference rate — sub-1 Hz creeping on CPU.
# Disable only to debug raw per-step behaviour: --no-rtc.
USE_RTC=1
RATE_HZ=""   # empty => pick per device below (cpu gets headroom for slow inference)
RESET_CAMS=1 # hardware-reset the D405s right before the runner opens them (see below)
EXTRA=()

while [ $# -gt 0 ]; do
  case "$1" in
    --bridge-ip)   BRIDGE_IP="$2"; shift 2;;
    --policy-type) POLICY_TYPE="$2"; shift 2;;
    --policy-path) POLICY_PATH="$2"; shift 2;;
    --task)        TASK="$2"; shift 2;;
    --device)      DEVICE="$2"; shift 2;;
    --obs-port)    OBS_PORT="$2"; shift 2;;
    --action-port) ACTION_PORT="$2"; shift 2;;
    --rate-hz)     RATE_HZ="$2"; shift 2;;
    --no-rtc)      USE_RTC=0; shift;;
    --rtc)         USE_RTC=1; shift;;
    --no-reset-cameras) RESET_CAMS=0; shift;;
    --reset-cameras)    RESET_CAMS=1; shift;;
    *)             EXTRA+=("$1"); shift;;
  esac
done

# Control rate: with RTC, the producer must finish a chunk inference within
# execution_horizon/RATE_HZ seconds or the queue underruns (jerky pauses). Measured
# CPU inference for this smolvla checkpoint ≈555 ms with a 17-step horizon, so 30 Hz
# (567 ms budget) is marginal — drop CPU to 25 Hz (680 ms budget) for headroom. GPU
# (~146 ms) easily holds 30 Hz. Override anytime with --rate-hz.
if [ -z "$RATE_HZ" ]; then
  case "$DEVICE" in
    cpu) RATE_HZ="25.0";;
    *)   RATE_HZ="30.0";;
  esac
fi

say() { echo "[deploy] $*"; }
die() { echo "[deploy] ERROR: $*" >&2; exit 1; }

[ -x "$RUNNER" ] || die "runner not found: $RUNNER"
[ -f "$CONFIG" ] || die "camera config not found: $CONFIG"
[ -x "$VPY" ] || die "lerobot venv python not found: $VPY (set EE26_LEROBOT_VENV)"
[ -e "$POLICY_PATH" ] || die "policy path not found: $POLICY_PATH"

# --- camera single-consumer rule: the runner opens both D405s directly ---
if pgrep -f live_camera_view >/dev/null 2>&1; then
  say "killing stray live_camera_view (holds the cameras)"; pkill -f live_camera_view || true; sleep 1
fi
if pgrep -f record_data_collection_session >/dev/null 2>&1 || pgrep -f record_realsense_camera >/dev/null 2>&1; then
  die "a recorder is holding the cameras — stop it before deploy (one camera consumer at a time)"
fi

# --- verify the policy-mode bridge is publishing observations on :$OBS_PORT ---
say "checking for robot observations on udp/$OBS_PORT (bridge must be in --control-source policy)..."
if ! "$VPY" - "$OBS_PORT" <<'PY'
import socket, sys
port = int(sys.argv[1])
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", port)); s.settimeout(5.0)
try:
    s.recvfrom(65535); print("  obs stream OK"); sys.exit(0)
except socket.timeout:
    sys.exit(1)
PY
then
  die "no observations on :$OBS_PORT in 5s. Start the policy-mode bridge first (see DEPLOY.md), and ensure it publishes obs to this host (--obs-ip)."
fi

RTC_ARGS=()
RTC_LABEL="OFF (sync single-step)"
if [ "$USE_RTC" = "1" ]; then
  RTC_ARGS=(--use-rtc)
  RTC_LABEL="ON (background chunk producer)"
fi

echo "============================================================"
echo "  DEPLOY  policy=$(basename "$POLICY_PATH")  type=$POLICY_TYPE  device=$DEVICE"
echo "    bridge=$BRIDGE_IP  obs=:$OBS_PORT  action=:$ACTION_PORT"
echo "    task=\"$TASK\""
echo "    RTC=$RTC_LABEL  rate=${RATE_HZ}Hz"
echo "  SAFETY: arm should be at the data-collection home & contact-free."
echo "  Keys: p=pause  h=pause+rehome  r=resume  q=quit.  Hover finger on p."
echo "============================================================"

# Hardware-reset the D405s as the LAST step before exec. On the flaky/bridged USB
# controller a D405 only delivers frames on the FIRST pipeline open after a USB reset
# (see tools/reset_cameras.py docstring + memory ee26-wrist-d405-flaky). Nothing may
# open a camera between this reset and the runner — so the runner is that first open.
# Disable with --no-reset-cameras.
if [ "$RESET_CAMS" = "1" ]; then
  say "resetting RealSense cameras (first-open-after-reset rule; ~3-4s)..."
  "$VPY" "$TELEOP_DIR/tools/reset_cameras.py" || say "camera reset reported an issue; continuing to runner"
fi

exec "$VPY" "$RUNNER" \
  --policy-type "$POLICY_TYPE" \
  --policy-path "$POLICY_PATH" \
  --lerobot-root "$REPO_ROOT/lerobot" \
  --config "$CONFIG" \
  --device "$DEVICE" \
  --task "$TASK" \
  --bridge-ip "$BRIDGE_IP" \
  --obs-port "$OBS_PORT" \
  --action-port "$ACTION_PORT" \
  --rate-hz "$RATE_HZ" \
  ${RTC_ARGS[@]+"${RTC_ARGS[@]}"} \
  ${EXTRA[@]+"${EXTRA[@]}"}
