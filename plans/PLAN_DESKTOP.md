# Desktop Plan — Black Workstation (RT kernel · RTX 5090 · wired to Franka)

## ▶ SESSION KICKOFF (read this first, fresh session)
You are the **DESKTOP** Claude Code session for the EE26 hackathon. Machine: Ubuntu 22.04 Black
workstation, RT kernel, **RTX 5090 (training runs here)**, ROS2 Humble installed (kept, unused),
our Franka Panda at **192.168.1.11**. **The robot is real** — confirm before any motion, keep the
E-stop in reach, never run Desk and the bridge at once.
**Do first:** `git pull origin main`, then read `README.md`, `CONTRACT.md`, and this file. You own
`robot/`, `challenge2/` runtime, and **training execution on the 5090**. Work top-to-bottom from D0.
Commit/push small and often; stay in your owner dirs; `CONTRACT.md` is frozen (ping before changing).

## Ground rules
- Our **libfranka bridge is the control stack** (not multipanda, not ROS). Ignore Humble.
- FCI single-client: Desk OR bridge, never both. Unlock joints + activate FCI in Desk (`franka`/`frankaRSI`, see `CREDENTIALS.md`).
- E-stop known; loosen collision thresholds before contact tasks; rehearse error recovery.

## D0 — Bring-up (no policy needed)
- [ ] Build bridge: `cmake -S robot/franka_xr_teleop -B robot/franka_xr_teleop/build -DCMAKE_BUILD_TYPE=Release && cmake --build robot/franka_xr_teleop/build -j`
- [ ] Build + run `robot/franka-sanity-checks` (gripper, safe EE translate).
- [ ] RT + connection check (libfranka communication test to 192.168.1.11; bridge `--dry-run` then live).
- [ ] Both D405s enumerate (`robot/franka_xr_teleop/tools/record_realsense_camera.py`); serials match `data_collection.yaml` (wrist `…845`, external `…175`).
- [ ] **5090 GPU check:** PyTorch **cu128** (Blackwell sm_120) + driver ≥570; `torch.cuda.is_available()` + a GPU matmul. (Do this early — it's the silent time-sink.)
- [ ] Robot homes safely to start pose. **GATE.**

## D1 — Teleop (Quest)
- [ ] Install **XRoboToolkit PC Service**; pair Quest 3 → `xrobotics_source` → bridge. Tune `teleop.yaml`.
- [ ] Verify keyboard fallback as insurance.
- [ ] **GATE:** smooth deadman-anchored 6-DOF teleop + gripper.

## D2 — Data collection (C1 insertion demos)
- [ ] `tools/record_data_collection_session.py` → synced obs (28081) + both D405 videos.
- [ ] **30–50 diverse** insertion demos (vary approach angle, start pose, placement). Diversity > volume; fixed `task` prompt.
- [ ] Align + convert to LeRobot v3 per `CONTRACT.md §1`. Commit/push the dataset (or shared path) for training.

## DT — Train on the 5090 (configs authored off-robot)
- [ ] Pull off-robot's training configs/scripts from `training/`.
- [ ] Train SmolVLA on the insertion dataset (off-robot may SSH in to launch/monitor — coordinate so training and robot use don't overlap in time).
- [ ] Produce policy artifact per `CONTRACT.md §4` (checkpoint + `policy_card.md`).

## D3 — Deploy
- [ ] Torch deploy: `tools/run_vla_policy.py` (apply output **jitter** for mm precision — see report).
- [ ] OpenVINO deploy: run inference on **Pantherlake**, stream UDP 28082 actions to the bridge here (Intel bonus).
- [ ] Eval; push failure notes for off-robot to fold into retrain.

## C2 — Ball-balance (parallel, classical)
- [ ] Standalone controller: D405 ball tracker → PD/LQR → Cartesian-pose (plate tilt) command. See `challenge2/`.
- [ ] Prototype risky gains in MuJoCo sim sandbox BEFORE the real arm. Base task → 4-pose bonus. Optional OpenVINO tracker → Intel bonus.

## Optional — compliance
- [ ] If stiff control fights the hole: add Cartesian-impedance torque controller to the bridge; tune gains in sim first.
