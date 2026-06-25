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
#   ./tools/start_policy_deploy.sh [--bridge-ip IP] [--policy-path DIR] [--task STR]
#                                  [--device cuda|cpu] [extra run_vla_policy args...]
# Defaults: bridge-ip 127.0.0.1, policy training/outputs/c1_insertion_smolvla,
#           canonical task, device cuda. Operator keys (in this terminal):
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
POLICY_PATH="$REPO_ROOT/training/outputs/c1_insertion_smolvla"
TASK="Insert the white cylindrical block into the white socket."  # canonical (see policy_card.md)
EXTRA=()

while [ $# -gt 0 ]; do
  case "$1" in
    --bridge-ip)   BRIDGE_IP="$2"; shift 2;;
    --policy-path) POLICY_PATH="$2"; shift 2;;
    --task)        TASK="$2"; shift 2;;
    --device)      DEVICE="$2"; shift 2;;
    --obs-port)    OBS_PORT="$2"; shift 2;;
    --action-port) ACTION_PORT="$2"; shift 2;;
    *)             EXTRA+=("$1"); shift;;
  esac
done

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

echo "============================================================"
echo "  DEPLOY  policy=$(basename "$POLICY_PATH")  device=$DEVICE"
echo "    bridge=$BRIDGE_IP  obs=:$OBS_PORT  action=:$ACTION_PORT"
echo "    task=\"$TASK\""
echo "  SAFETY: arm should be at the data-collection home & contact-free."
echo "  Keys: p=pause  h=pause+rehome  r=resume  q=quit.  Hover finger on p."
echo "============================================================"

exec "$VPY" "$RUNNER" \
  --policy-type smolvla \
  --policy-path "$POLICY_PATH" \
  --lerobot-root "$REPO_ROOT/lerobot" \
  --config "$CONFIG" \
  --device "$DEVICE" \
  --task "$TASK" \
  --bridge-ip "$BRIDGE_IP" \
  --obs-port "$OBS_PORT" \
  --action-port "$ACTION_PORT" \
  --rate-hz 30.0 \
  ${EXTRA[@]+"${EXTRA[@]}"}
