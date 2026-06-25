# CLAUDE.md — East End Effectors / EE26

Hackathon repo for the **Intel Industrial Robotics Arm Challenge** on our own **Franka Emika Panda**.
Strategy: `PLAN.md`. Two-machine workflow + merge model: `README.md`. Frozen interface: `CONTRACT.md`.

## Which session am I?
- **DESKTOP** (Black workstation · RT kernel · RTX 5090 · wired to the Panda): owns `robot/`, `challenge2/` runtime, and **training execution**. Plan: `plans/PLAN_DESKTOP.md`.
- **OFF-ROBOT** (a Mac · no GPU · no robot): owns `training/` + `CONTRACT.md` authoring; produces code the Desktop runs. Plan: `plans/PLAN_OFFROBOT.md`.

Work only in your owner dirs. If unsure which machine you're on: GPU + robot present ⇒ Desktop.

## Current state (2026-06-24) — off-robot built out, waiting on hardware/data
- ✅ **Off-robot (this Mac) complete & pushed:** repo scaffold + frozen `CONTRACT.md`; 110 tests green
  (training 105 + challenge2 5); C1 training turn-key (`training/TASK_C1.md`); Intel-bonus runner
  (`training/src/inference/openvino_runner.py`, `--self-test` PASS); C2 ball-balance stack (`challenge2/`).
- ⏳ **Desktop in progress:** hardware bring-up, teleop, demo collection, training, deploy — none done yet.
- ⛔ **Blocked:** off-robot O2 (real SmolVLA/Pi0 training) needs the Desktop's **first dataset**. Nothing more
  to do off-robot until then except optional speculative work (e.g. synthetic mini-dataset dry-run, needs `lerobot`).
- 🔧 **Fix-on-arrival (flagged in code):** (1) confirm `InferenceModel.select_action` obs-dict/action shapes on
  the Intel box → `build_policy_obs` in `openvino_runner.py`; (2) C2 tilt **sign** vs camera mounting →
  `tilt_to_pose(signs=…)` in `challenge2/src/plate_command.py` (calibrate early with low gains).
- ⚠️ **Desktop: `git pull` first** — a scaffold bug had dropped `training/src/data/` (the converter); it's
  recovered on `main`, the training pipeline is broken without it.

## ⏳ Desktop D0 bring-up — IN PROGRESS (transient; delete this section when D0 is done)
State as of 2026-06-24, mid-D0, **after the reboot for the GPU driver**. Full detail in memory `ee26-desktop-d0-env.md`.

**Robot IP is now `192.168.1.11`** (Edwin-confirmed; was `192.168.2.200`). It's on `192.168.1.0/24` via NIC `enp8s0f2` (desktop `192.168.1.6`). Old `enp8s0f0/f1` still DOWN — ignore them.

**libfranka — system 0.19.0 is WRONG (FR3-era).** The correct **0.9.2 is built at `~/opt/libfranka-0.9.2`** (system 0.19.0 left untouched). Build ALL `robot/` C++ with it:
```bash
export CMAKE_PREFIX_PATH="$HOME/opt/libfranka-0.9.2:$CMAKE_PREFIX_PATH"
```
✅ **Confirmed a real Panda, not an FR3** — read-only sanity connect reports **FCI server version 5** (FR3 is v7+/libfranka 0.10+). `0.9.2` is the right choice.

**Already done (survives reboot):**
- `franka-sanity-checks` built → `robot/franka-sanity-checks/build/panda_libfranka_sanity` (rpath-links 0.9.2; no LD_LIBRARY_PATH needed).
- ✅ **Robot connection check PASS** — `--mode read-only` against `192.168.1.11`: 100-sample 1 kHz `read()` loop clean, `robot_mode: Idle`, no errors. RT comms + libfranka↔FCI verified. No motion run yet.

**🖥️ GPU ↔ RT-kernel conflict — RESOLVED as DUAL-BOOT (Edwin-decided 2026-06-24):**
NVIDIA 595 open **refuses to build on the RT kernel** (`preempt_rt_sanity_check` bails: "does not support real-time kernels"). So **do NOT install/keep `nvidia-dkms-595-open`** — its post-install fails on `6.8.0-rt8-franka`. Instead:
- **GPU / training:** boot **`6.8.0-124-generic`** (prebuilt 595-open modules already at `/lib/modules/6.8.0-124-generic/kernel/nvidia-595-open/*.ko`; `nvidia-smi` works there with no build).
- **Robot / teleop / data collection:** boot **`6.8.0-rt8-franka`** (current default; 1 kHz loop needs RT). No GPU here.
- **D3 deploy (GPU+robot at once):** run inference via **OpenVINO on Pantherlake** → UDP 28082 to the bridge, so the RT desktop needs no GPU. (Only if Torch-on-desktop deploy is required would we patch NVIDIA onto RT — unsupported.)
- Cleared the failed dkms state: `sudo apt-get remove -y nvidia-dkms-595-open`. `nlohmann-json3-dev` is installed.

**cu128 validation (run after booting into `6.8.0-124-generic`):**
```bash
nvidia-smi                                   # should list the RTX 5090
python3 -m venv ~/ee26_gpu_venv && source ~/ee26_gpu_venv/bin/activate
pip install --index-url https://download.pytorch.org/whl/cu128 torch
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0)); x=torch.randn(4096,4096,device='cuda'); print((x@x).sum().item())"
```

**✅ Bridge BUILT + dry-run PASS (2026-06-24).** XRoboToolkit PC Service installed via the `.deb` → `/opt/apps/roboticsservice` (SDK at `SDK/include/PXREARobotSDK.h` + `SDK/x64/libPXREARobotSDK.so`; the published deb is in `~/Downloads/`). Built with `CMAKE_PREFIX_PATH=$HOME/opt/libfranka-0.9.2` (default `XROBOTICS_SERVICE_ROOT` resolves). Binary `robot/franka_xr_teleop/build/cpp/teleop_bridge/franka_xr_teleop_bridge` links libfranka 0.9.2 + libPXREARobotSDK + yaml-cpp. `--dry-run --control-source policy` runs clean: policy action source on `udp://0.0.0.0:28082`, obs publisher up, no robot/motion. To install the deb yourself: `sudo apt install -y ~/Downloads/XRoboToolkit_PC_Service_1.0.0_ubuntu_22.04_amd64.deb`.

**✅ Arm motion + homing GATE cleared (2026-06-24):** tiny-motion OK; new `move_to_home` tool drove the arm to `q_home=[0,-π/4,0,-3π/4,0,π/2,π/4]` (slow 6 s quintic). Build: `cmake -S robot/franka-sanity-checks -B …/build && cmake --build …/build` (CMAKE_PREFIX_PATH=libfranka-0.9.2), run `./build/move_to_home 192.168.1.11`. Motion prereq each time: user-stop released + FCI active + brakes unlocked; `self_collision_avoidance_violation` needs a manual hand-guide (auto-recovery rejected).
**✅ Cameras at HD (2026-06-24):** both D405 stream 1280×720@30 over USB 3.2. Real serials (old contract placeholders were wrong): external `130322273529`→`third_person_d405`; wrist `130322270179`→`top` in configs. ⚠️ Unit `130322273880` is a **dead D405** (USB-2 only even on a known-good cable) — keep it retired; mount a working spare on the wrist and confirm its serial matches `data_collection.yaml`.

**Still pending:**
- **GPU cu128:** reboot into `6.8.0-124-generic` (recipe above), then validate.

## ✅ Live teleop (D1) bring-up — Quest 3 → bridge → robot (WORKING 2026-06-25)
Full ADB/connection detail in `robot/docs/QUEST3_CONNECTION.md`. End-to-end sequence that worked:

**1. Quest USB/ADB (headset awake + worn, USB direct port, data cable):**
```bash
adb kill-server && adb start-server && adb devices -l   # want state "device"
```
- `unauthorized` → accept **Allow USB debugging** in-headset (check *Always allow*). If the prompt never sticks: `rm -f ~/.android/adbkey*` then restart server to force a fresh prompt; if still stuck, on-headset *Revoke USB debugging authorizations* + toggle USB debugging off/on.
- empty list / no `lsusb -d 2833:5013` → headset asleep or cable/port issue (use a **direct USB3 port**, data cable).

**2. Reverse tunnel (must re-add every time the headset reconnects):**
```bash
adb reverse --remove-all && adb reverse tcp:63901 tcp:63901 && adb reverse --list
```

**3. Service + Unity (one script launches both — `RoboticsServiceProcess` + `RobotLinuxDemo.x86_64`):**
```bash
cd /opt/apps/roboticsservice && ./run3D.sh    # 63901 should LISTEN; runService.sh = service only, no Unity
```

**4. Robot must be at home & contact-free BEFORE the bridge** (see reflex gotcha below), then:
```bash
cd robot/franka_xr_teleop && ./build/cpp/teleop_bridge/franka_xr_teleop_bridge --robot-ip 192.168.1.11 --obs-port 28081
```
Healthy log: `Gripper ready …`, `control_command_success_rate≈0.97-1`, `q_err_max≈0.002` (holding home). In the Quest app connect to `127.0.0.1`; engage the clutch with controllers near the arm pose.

**⚠️ `cartesian_reflex` on bridge start / during homing:** if the bridge's first teleop target jumps (controllers not aligned) the robot trips `["cartesian_reflex"]` and the bridge exits, leaving the arm in **Reflex** mode (and any commanded move then rejected). Recover + re-home, in order:
```bash
cd robot/franka-sanity-checks
./build/panda_libfranka_sanity --robot-ip 192.168.1.11 --mode recover-only   # Reflex→Idle (automaticErrorRecovery)
./build/move_to_home 192.168.1.11                                            # slow 6 s quintic to q_home
```
- `"User stopped"` rejection → physical user-stop is pressed; release it, then recover again.
- Homing itself trips `cartesian_reflex` mid-move with `cartesian_collision:[…,2,…]` + steady `O_F_ext_hat_K` Z (~5 N) → the **gripper is in contact** (table/plate/cable). Guiding is envelope-capped right at the contact so you can't hand-pull it free. Break contact with a commanded lift (base-frame +Z, away from a downward contact), then recover + home:
```bash
./build/panda_libfranka_sanity --robot-ip 192.168.1.11 --mode tiny-cartesian --cart-axis z --delta-m 0.02 --duration-s 10
```
  (tiny-cartesian shifts `O_T_EE` translation in the **base frame**; min 8 s, ≤0.05 m, ≤0.01 m/s.)

**🔁 Headset sleeps / "connect error" / timeout in the app:** taking the headset off drops the USB→ADB link, which kills the reverse tunnel — the bridge, service, and robot keep running. Fix is just: **wake/wear the headset, then redo steps 1–2** (`adb kill-server && adb start-server`; re-add `adb reverse tcp:63901 tcp:63901`) and reconnect the app to `127.0.0.1`. Do **not** restart the bridge or re-home.

## Repo map
| Path | What |
|---|---|
| `robot/franka_xr_teleop/` | libfranka bridge (C++), teleop, recorders, `tools/run_vla_policy.py` deploy |
| `robot/franka-sanity-checks/` | gripper / safe-translate hardware checks |
| `training/` | SmolVLA-Testing pipeline (clean/annotate/convert/train/eval); `main.py` CLI |
| `training/TASK_C1.md` | turn-key C1 record→…→train recipe (run on Desktop) |
| `training/src/inference/` | OpenVINO Pantherlake runner + README (Intel bonus) |
| `training/configs/training/` | `smolvla_baseline.yaml`, `pi0_c1.yaml`, `pi0_subtask.yaml`, … |
| `challenge2/` | ball-balance: tracker + PD + plate-tilt; `README.md` has the bring-up + `CommandSink` boundary |
| `CONTRACT.md` `plans/` `README.md` | frozen interface · per-machine plans · merge model |

## Conventions (apply on every machine)
- **Commits: NO `Co-Authored-By: Claude` / no AI attribution trailer.** Plain messages. Same for PRs.
- Small, frequent commits on `main`; `git pull` before starting, push when a step works.
- **`CONTRACT.md` is frozen** — don't change a contract value (image keys, dims, ports, serials, IP) without asking Edwin first; it breaks the other machine.
- **Never commit** secrets or large artifacts — `CREDENTIALS.md`, `*.pdf`, datasets, checkpoints, videos are gitignored. Keep it that way.

## Data flow between machines
- **Code/configs travel through git.** Datasets and checkpoints **do not** (gitignored).
- Data collection + training + deploy all happen on the **Desktop**, so the dataset never needs to leave it. The OFF-ROBOT session works against the `CONTRACT.md` spec, not against real data.
- Don't train and teleop at the same time (shared machine) — sequence them, or SSH-launch training when the arm is idle.

## Key facts / constants
| | |
|---|---|
| Robot (FCI IP) | `192.168.1.11` · libfranka `>=0.9.1,<0.10.0` (rec 0.9.2) · firmware 4.2.x |
| Control stack | OUR libfranka bridge (`robot/franka_xr_teleop`), `kJointImpedance`. NOT multipanda, NOT ROS, NOT Noetic |
| Cameras | wrist D405 `128422271845` → `observation.images.top`; external D405 `128422271175` → `observation.images.third_person_d405`; 1280×720@30 |
| Policy IO | `observation.state`=[8] (7 joints + gripper width); `action`=[8] (7 joint pos + gripper) |
| UDP | actions → bridge port **28082** (`joint_position_absolute`); observations ← port **28081** |
| GPU | RTX 5090 = Blackwell **sm_120** → needs **PyTorch cu128** + driver ≥570 |
| Teleop | Meta Quest 3 via XRoboToolkit PC Service → `xrobotics_source`; keyboard fallback |
| Intel bonus | OpenVINO inference on Pantherlake workstation (`physical-ai-studio`) |

## Quick commands
```bash
# DESKTOP — build the bridge
cmake -S robot/franka_xr_teleop -B robot/franka_xr_teleop/build -DCMAKE_BUILD_TYPE=Release
cmake --build robot/franka_xr_teleop/build -j

# OFF-ROBOT (this Mac) — system python is 3.14 (no torch wheels); use the py3.12 venv:
source ~/Downloads/ee26_venv/bin/activate    # uv venv, CI deps from training/requirements-ci.txt
cd training  && python -m pytest tests -m "not gpu" \
    --deselect tests/test_smolvla_fork.py::test_policy_class_importable -q   # CI-faithful (no lerobot/GPU)
cd challenge2 && python -m pytest tests -q
# wire-format check for the Intel runner (no hardware):
cd training && python -m src.inference.openvino_runner --self-test
```

## Gotchas (have burned teams before)
- **cu128 PyTorch** for the 5090 — validate `torch.cuda.is_available()` + a matmul before training.
- **FCI single-client:** Desk OR the bridge, never both.
- **RT kernel** needed for the 1 kHz loop (Black workstation has it).
- Compliance: stiff position control can fight the hole on insertion — fallback is VLA+jitter (proven) or a Cartesian-impedance torque controller (tune gains in sim first).
- MuJoCo sim sandbox (optional multipanda): Eigen **3.3.9**, not 3.4.0.
- **Action = commanded joints** `q_cmd` (converter falls back to `backfilled_q_cmd`), not measured next-q.
- **Convert with `--primary-camera wrist_d405`** so wrist→`observation.images.top` — wrong value silently
  mismatches train/deploy image keys (see `CONTRACT.md §1`).
- C2 commands a **Cartesian-pose** target (the teleop/pose path), NOT the joint policy port 28082.

## Reference clones (OFF-ROBOT / this Mac only — not in git)
`~/Downloads/ee26_refs/`: `vla-teleop-franka-v2` (source of `robot/`), `SmolVLA-Testing` (source of `training/`; extra branches: feat/qwen-thomas, labeler, qwen-prompting, xav-qwen).
