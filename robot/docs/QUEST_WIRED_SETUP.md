# Quest 3 — Wired Teleop Setup (runbook, run on the Desktop)

Goal: Meta Quest 3 driving the Franka over a **USB cable** (no WiFi), end-to-end.
Milestone A: green `--dry-run` showing incoming controller data. Milestone B: live teleop with the
X4 enabling grip held. Run every command on the **Black workstation** (the Quest is wired to it).

**Why wired works without WiFi:** `adb reverse tcp:63901` forwards the headset's `localhost:63901`
to the PC over USB, so the Quest app connects to `127.0.0.1` and reaches the PC Service through the cable.
Lower latency than WiFi — this is the preferred path.

Desktop env facts (from D0): robot IP `192.168.1.11` · libfranka 0.9.2 at `~/opt/libfranka-0.9.2`
(system 0.19.0 is wrong — don't use it) · bridge needs `nlohmann-json3-dev` + the XRoboToolkit SDK.
FCI single-client: Desk OR the bridge, never both.

---

## 1. Headset prerequisites (one-time)
- Quest **Developer Mode** ON (via the Meta Horizon phone app → your headset) and **USB debugging** enabled.
- `adb` installed on the desktop: `sudo apt install -y android-tools-adb`.

## 2. Confirm the USB/ADB link
```bash
adb kill-server && adb start-server && adb devices
```
Put the headset on → accept **"Allow USB debugging" → Always allow from this computer**. Expect the
Quest listed as `device`. If empty/`unauthorized`, see `robot/docs/QUEST3_CONNECTION.md` (cable must be
data-capable, no hubs; udev rule for vendor `2833`; revoke+retry authorizations).

## 3. Install the Quest client app (skip if already installed)
⚠️ **No prebuilt Quest APK exists** — `XRoboToolkit-Unity-Client-Quest` ships as Unity source only.
- **Preferred:** get the already-built `.apk` from the teammate who set up the Quest in the prior project.
- **Fallback (slow):** build it in **Unity 2021.3.45f2** (exact; 2022.x crashes) + Meta XR Interaction SDK
  72.0.0 + Oculus XR Plugin 3.4.1 + Android module — multi-hour, needs a Unity machine.
```bash
adb install <path-to>/XRoboToolkit-Unity-Client-Quest.apk
```
Then find it in the headset App Library under the **"Unknown Sources"** filter.

## 4. Build + install + start the PC Service  ← main blocker
Build from source ([XRoboToolkit-PC-Service](https://github.com/XR-Robotics/XRoboToolkit-PC-Service)):
```bash
git clone https://github.com/XR-Robotics/XRoboToolkit-PC-Service
cd XRoboToolkit-PC-Service
RoboticsService/Package/debPack/setup.sh        # x86_64 (debPackAArch64 only on ARM)
# install the produced .deb, then start the service (leave it running):
runService.sh                                   # service only (run3D.sh also works: service + 3D demo)
```
**Record the install path** — you pass it to the bridge build below. SDK headers live at `SDK/include`,
libs at `SDK/linux/64/`.

## 5. Open the USB tunnel (the "wired" step)
```bash
adb reverse --remove-all
adb reverse tcp:63901 tcp:63901
adb reverse --list        # must show: tcp:63901 tcp:63901
```

## 6. Connect in the headset
Launch the XRoboToolkit app → set address **`127.0.0.1`** → connect. It reaches the PC Service over USB.

## 7. Build the bridge with the SDK
```bash
sudo apt install -y nlohmann-json3-dev
export CMAKE_PREFIX_PATH="$HOME/opt/libfranka-0.9.2:$CMAKE_PREFIX_PATH"
cmake -S robot/franka_xr_teleop -B robot/franka_xr_teleop/build -DCMAKE_BUILD_TYPE=Release \
  -DXROBOTICS_SERVICE_ROOT=<pc-service-install-path>      # add -DXROBOTICS_SDK_ROOT=<...>/SDK if needed
cmake --build robot/franka_xr_teleop/build -j"$(nproc)"
```

## 8. Milestone A — dry-run (NO robot motion, FCI not required)
```bash
./robot/franka_xr_teleop/build/cpp/teleop_bridge/franka_xr_teleop_bridge --dry-run
```
Move the controller; pull the **control (deadman)** and **gripper** triggers; press buttons.
**PASS = the bridge logs incoming XR/controller data.** Visualize with
`robot/franka_xr_teleop/tools/live_teleop_debug.py` / `plot_teleop_trace.py`. **GATE A.**

## 9. Milestone B — live teleop (robot connected)
FCI active in Desk (`franka`/`frankaRSI`); Desk NOT commanding. Hold the **X4 enabling grip** half-pressed.
```bash
# hold-only first (robot connected, no arm motion):
./robot/franka_xr_teleop/build/cpp/teleop_bridge/franka_xr_teleop_bridge \
  --robot-ip 192.168.1.11 --obs-port 28081 --no-motion
# then live (remove --no-motion):
./robot/franka_xr_teleop/build/cpp/teleop_bridge/franka_xr_teleop_bridge \
  --robot-ip 192.168.1.11 --obs-port 28081
```
Tune `robot/franka_xr_teleop/configs/teleop.yaml` (scale_factor, deadbands, rotation_scale).
Release the grip → robot stops; rehearse `error_recovery`. **GATE B = smooth deadman-anchored 6-DOF + gripper.**

## Quick reference
- Tunnel port: **63901**. App connects to **127.0.0.1**.
- Deadman: hold X4 enabling grip (hardware) — see `PLAN_DESKTOP.md` "Enabling-Grip mode".
- Most likely snags: (a) PC Service install path → pass to `-DXROBOTICS_SERVICE_ROOT`; (b) `adb devices` empty → cable/udev, not the app; (c) build can't find libfranka → check `CMAKE_PREFIX_PATH` points at `~/opt/libfranka-0.9.2`.
