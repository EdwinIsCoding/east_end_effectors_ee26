#!/usr/bin/env bash
# Rehome the Panda to the current EE26 home (a hand-guided capture, kept in sync
# with the teleop bridge's start_joint_positions_rad / B-button rehome).
# Pass --legacy to instead drive to the classic ready pose
# q_home = [0, -pi/4, 0, -3pi/4, 0, pi/2, pi/4]. Slow 6 s quintic move (override
# duration as a positional arg). Wraps build/move_to_home.
#
# Prereqs (same as any FCI motion):
#   - user-stop RELEASED, brakes UNLOCKED, FCI ACTIVE, E-stop in hand, path clear.
#   - If the arm is in Reflex / self_collision_avoidance_violation, hand-guide it out
#     first — that error needs manual recovery; this tool will not move.
#
# Usage:
#   ./rehome.sh                        # default IP, 6 s, current home
#   ./rehome.sh 192.168.1.11 8         # explicit IP + duration (s)
#   ./rehome.sh --legacy               # classic q_home instead of current home
#   ./rehome.sh 192.168.1.11 8 --legacy
set -euo pipefail

HOME_FLAG=()        # forwarded to move_to_home (e.g. --home legacy)
POSITIONAL=()
for arg in "$@"; do
  case "${arg}" in
    --legacy)  HOME_FLAG=(--home legacy) ;;
    --home=*)  HOME_FLAG=(--home "${arg#--home=}") ;;
    *)         POSITIONAL+=("${arg}") ;;
  esac
done

ROBOT_IP="${POSITIONAL[0]:-192.168.1.11}"
DURATION="${POSITIONAL[1]:-6.0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${SCRIPT_DIR}/build/move_to_home"

if [[ ! -x "${BIN}" ]]; then
  echo "move_to_home binary not found at ${BIN}" >&2
  echo "Build it first:" >&2
  echo "  cd ${SCRIPT_DIR}" >&2
  echo "  export CMAKE_PREFIX_PATH=\"\$HOME/opt/libfranka-0.9.2:\$CMAKE_PREFIX_PATH\"" >&2
  echo "  cmake -S . -B build && cmake --build build -j" >&2
  exit 1
fi

HOME_DESC="current home"
[[ "${HOME_FLAG[*]:-}" == *legacy* ]] && HOME_DESC="legacy q_home"
echo "Rehoming Panda @ ${ROBOT_IP} over ${DURATION}s to ${HOME_DESC} — keep the E-stop in hand."
echo "(needs: user-stop released, brakes unlocked, FCI active, path clear)"
exec "${BIN}" "${ROBOT_IP}" "${DURATION}" "${HOME_FLAG[@]}"
