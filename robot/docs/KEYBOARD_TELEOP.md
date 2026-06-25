# Keyboard / mouse teleop (Quest-free)

Drive the Franka from the desktop with **keyboard** (and optional mouse) instead of the Quest, reusing
the **exact** teleop mapper + damped-least-squares IK + safety. Use this to collect C1 demos while the
Quest controllers are down (e.g. no batteries).

## How it works
`teleop_keyboard.py` streams synthetic `XRCommand` JSON over UDP to a new bridge input, **`UdpXrSource`**,
which publishes to the same command buffer the Quest fills. So `control_source` stays `kXr` and nothing
in the IK/mapper/safety changes — only the *source* of controller poses.

```
teleop_keyboard.py --(UDP XRCommand JSON :28083)--> bridge UdpXrSource --> TeleopMapper --> IK --> Franka
```

## Build (Desktop)
The new source is part of the bridge — just rebuild:
```bash
export CMAKE_PREFIX_PATH="$HOME/opt/libfranka-0.9.2:$CMAKE_PREFIX_PATH"
cmake -S robot/franka_xr_teleop -B robot/franka_xr_teleop/build -DCMAKE_BUILD_TYPE=Release \
  -DXROBOTICS_SERVICE_ROOT=<...>      # SDK still required to link the (unused-here) Quest source
cmake --build robot/franka_xr_teleop/build -j
```
(The XRoboToolkit SDK is still a build dependency of the bridge; this mode just doesn't *use* the Quest
at runtime. If the SDK isn't installed yet, that's the one thing blocking the build.)

## Run
```bash
# 1) bridge with the UDP keyboard source (uses the tuned configs/teleop.yaml):
./robot/franka_xr_teleop/build/cpp/teleop_bridge/franka_xr_teleop_bridge \
  --dry-run --xr-input-source udp                       # validate input path, no robot

# 2) the keyboard driver (run on the SAME machine, or pass --bridge-ip):
python robot/franka_xr_teleop/tools/teleop_keyboard.py     # needs: pip install pynput
```
Dry-run should log `rx_count` climbing and `right_grip`/buttons changing as you press keys. Then go live:
```bash
./robot/franka_xr_teleop/build/cpp/teleop_bridge/franka_xr_teleop_bridge \
  --robot-ip 192.168.1.11 --obs-port 28081 --xr-input-source udp
```

## Controls (hold the deadman to move)
| Key | Action | Key | Action |
|---|---|---|---|
| **SPACE (hold)** | deadman / enable motion | **G** | toggle gripper |
| W / S | robot +x / −x (fwd/back) | ENTER | episode start (button A) |
| A / D | robot +y / −y (left/right) | BACKSPACE | episode end (button B) |
| R / F | robot +z / −z (up/down) | ESC | quit |
| U/O J/L I/K | roll / yaw / pitch (`--rotate`) | | |

Tunables: `--trans-speed` (m/s), `--rot-speed` (rad/s), `--rate` (Hz), `--invert X Y Z` (flip a reversed
axis), `--rotate` (enable wrist rotation), `--print` (show pose). Self-test: `--selftest`.

## Safety
- **SPACE is only a software deadman** (the `control_trigger`). The **X4 hardware enabling grip is still
  the real safety** — for live motion someone must hold it (the keyboard operator's hands are busy, so
  have a second person hold the grip, or release it = instant stop).
- Defaults are conservative (0.10 m/s, smooth 100 Hz integration) and well under the bridge's per-step /
  speed / jump-reject limits, so motion is gentle. Start slow; raise `--trans-speed` once it feels right.
- First run: verify each axis moves the expected way; flip with `--invert` if your frame config differs
  from the default `xr_to_robot_rotation`.

## Tests
`python -m pytest robot/franka_xr_teleop/tools/test_teleop_keyboard.py -q` (logic only, no robot).
