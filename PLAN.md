# EE26 — Intel Industrial Robotics Arm Challenge — Plan

> Split into two machine workstreams — see `README.md`, `plans/PLAN_DESKTOP.md`, `plans/PLAN_OFFROBOT.md`, and the frozen `CONTRACT.md`. Code seeded under `robot/` (desktop) and `training/` (off-robot).


**Team:** East End Effectors (3, 2 on site) · **Window:** ~40h from 2026-06-24 · **Goal:** top score.

**Hardware:** Our OWN Franka Emika **Panda** + Franka Hand, dedicated for the full 40h · our 2× RealSense D405 (wrist + external, via RealSense SDK) · Meta Quest 3 (teleop) · local RTX 5090 (32 GB) · Intel **Pantherlake** workstation (OpenVINO bonus) · Black workstation (RT kernel, drives arm).

## Control-stack decision: OUR libfranka stack (primary)
Organizer confirmed using our own control stack is **score-neutral**, and we have a **dedicated arm for 40h** (removes the "sim saves scarce robot time" argument). So we run our proven **`vla-teleop-franka-v2`** libfranka 0.9.x bridge as primary:
- Already built/tested; Quest→teleop, record→LeRobot, train, deploy all wired.
- **Proven on this exact task**: prior MSD-plug insertion = contact-rich peg-in-hole, via "controlled output jitter" for mm precision.
- Uses our own D405s via RealSense SDK → no dependency on track's ROS2 camera topics.
- Control mode: `franka::ControllerMode::kJointImpedance`, commands JointPositions/CartesianPose. Stiff position control (joint stiffness 1000–1500 Nm/rad).

**multipanda_ros2 = optional sim sandbox only** (MuJoCo) — used solely to de-risk any new/aggressive controller (impedance gains, fast C2 trajectories) before the real arm. NOT a runtime dependency. NOT ROS1 Noetic (irrelevant, EOL, Ubuntu 20.04-only).

### Compliance gap (the one weakness of stiff control)
- C1 floor: VLA + jitter (proven) and/or classical search with loosened collision thresholds.
- C1 enhancement if needed: add ~100-line Cartesian-impedance **torque** controller (`tau = Jᵀ(Kp·x_err + Kd·ẋ_err) + nullspace + coriolis`). ⚠️ Tune gains in MuJoCo sim FIRST — unstable gains on our own arm risk reflex trip / stress.

## The two challenges
- **C1 — Insertion (peg-in-hole, 3D-printed).** Our domain. Floor = classical/VLA on stiff control + jitter; ceiling = SmolVLA→Pi0 via OpenVINO. Watch `O_F_ext` / external wrench.
- **C2 — Ball-balance on TCP plate.** Classical tracker → PD/LQR → Cartesian pose (plate tilt) command to bridge. Bonus: hold across 4 TCP poses. Self-contained.
- **Intel bonus:** run inference through **OpenVINO** on Pantherlake — a VLA (C1) and/or the C2 ball tracker.

## What's already built (reuse, in ee26_refs/ and seeded here)
- `vla-teleop-franka-v2`: libfranka bridge + IK + safety; Quest `xrobotics_source`; `record_data_collection_session.py` + D405 recorders; `run_vla_policy.py` deploy (UDP policy port 28082 = joint_position_absolute).
- `SmolVLA-Testing`: clean/annotate/convert/train (LeRobot v3). Branches: main, feat/qwen-thomas, labeler, qwen-prompting, xav-qwen.
- Report lessons: output jitter → mm-precision insertion; VLAs very prompt-phrasing sensitive; behavioral diversity > volume in small sets.

## Actual NEW work (short list)
1. **Quest:** XRoboToolkit PC Service setup → existing `xrobotics_source` → bridge.
2. **5090:** cu128 PyTorch (Blackwell sm_120) — validate `torch.cuda` + GPU matmul before training.
3. **Intel bonus:** OpenVINO export via `physical-ai-studio`; run inference on Pantherlake → UDP joint actions to bridge on Black workstation.
4. **C2 controller:** tracker (color/Hough, OpenVINO optional for bonus) → PD → Cartesian pose command.
5. **Optional:** Cartesian-impedance torque controller for robust C1 insertion (tune in sim first).

## ⚠️ Risk gates (Phase 0)
1. RTX 5090 = Blackwell sm_120 → PyTorch cu128 + driver ≥570.
2. RT kernel for 1 kHz libfranka — use Black workstation; verify via communication test.
3. FCI single-client: Desk OR external control, not both. Unlock joints + activate FCI in Desk (franka/frankaRSI).
4. libfranka 0.9.x ↔ Panda firmware 4.2.x compatibility.

## Status snapshot (updated 2026-06-24)
**Off-robot (this Mac) is built out and waiting on data.** Desktop is still setting up hardware.
- ✅ Repo scaffolded, two-machine split, frozen `CONTRACT.md`, pushed to `origin/main`.
- ✅ Off-robot pipeline green: 110 tests (training 105 + challenge2 5) in the py3.12 venv (`~/Downloads/ee26_venv`).
- ✅ **C1 training turn-key** (`training/TASK_C1.md`) + peg-in-hole annotate vocab + Pi0 single-5090 config.
- ✅ **Intel-bonus runner** (`training/src/inference/openvino_runner.py`, `--self-test` PASS).
- ✅ **C2 ball-balance stack** (`challenge2/`, PD-stabilizes-in-sim test) — Desktop wires `CommandSink` to the bridge.
- ⏳ Blocked on Desktop: real teleop, demo collection, SmolVLA/Pi0 training, all deploy/eval.
- Fix-on-arrival (flagged in code): `InferenceModel` obs/action shapes on Intel box; C2 tilt sign vs camera mount.

## Phases (40h) — parallelized across 2 on-site
- **0. Bring-up (0–3h):** Black workstation; build bridge (`cmake … Release`) + franka-sanity-checks; FCI unlock; communication/RT check; both D405s enumerate; 5090 cu128 matmul. Gate: safe home + GPU green. — ⏳ Desktop in progress.
- **1a. Teleop (2–6h):** Quest 3 → XRoboToolkit PC Service → `xrobotics_source` → bridge. Keyboard fallback as insurance. — ⏳ Desktop.
- **1b. C2 (3–10h):** ✅ tracker + PD + plate-tilt logic drafted & tested off-robot (`challenge2/`); ⏳ Desktop wires `CommandSink`, sign-calibrates, runs on hardware.
- **2. C1 demos (6–18h):** Quest-collect diverse insertion demos → `record_data_collection_session` → LeRobot. Diversity > volume. — ⏳ Desktop (gates off-robot O2).
- **3. SmolVLA loop (14–28h):** ✅ recipe + configs ready (`TASK_C1.md`); ⏳ runs once demos land (clean→convert→train→deploy+jitter→eval).
- **4. OpenVINO + Pi0 (24–36h):** ✅ runner + `pi0_c1.yaml` ready; ⏳ export to Pantherlake (Intel bonus); ball tracker → OpenVINO; Pi0 if data good.
- **5. Hardening (36–40h):** reliability runs, error-recovery rehearsal, scoring rehearsal, submission. — ⏳.

## C1 — Classical CV insertion pipeline (hybrid floor)
Shape-sorter peg-in-hole: white 3D-printed peg → matching shaped socket in a white cube. Shapes seen:
triangle, circle, pentagon (likely square/hexagon too). Reliable classical solution = the C1 floor
(VLA/OpenVINO is the ceiling). Stand the cube **hole-up** → vertical insertion = **4-DOF (x, y, z, yaw)**.

### Three hard parts
1. **Yaw alignment** (not just XY): non-circular pegs must be rotated to the socket angle. n-gon = n-fold
   symmetric → align within ±180/n°. Circle needs no rotation.
2. **White-on-white/silver contrast:** colour thresholding can't split white peg from white cube or the
   silver 8020. **Use the RED PLATE (or a matte dark mat)** — white pops off red; 5× more reliable CV.
3. **Calibration + compliance:** accurate camera→base extrinsics; stiff bridge → rely on chamfered hole
   entries + small search, or add the Cartesian-impedance torque controller. Monitor `O_F_ext`.

### Build split (clean seam)
- **Perception (OFF-ROBOT, ~80% — buildable + testable here now):** OpenCV on RGB(+depth) arrays →
  `detect_socket()/detect_peg()` returning `{shape, center_px, yaw_deg, polygon, quality}`; plus a
  pure `backproject(center_px, depth, K, T_cam_base)` helper. Camera-agnostic; tested against the
  example photos (and real D405 stills when shared). New package `robot/c1_vision/` + tests.
- **Integration + control (DESKTOP, robot-bound):** camera→base extrinsics calibration (AprilTag/hand-eye),
  D405 intrinsics + live depth, and the pick→align→insert→verify loop on the bridge + wrench feedback.
  Needs the robot (currently in use by team → some wait). Minimal once perception is done/tested.

### Pipeline stages
0. **Calibrate** camera→robot extrinsics (AprilTag/hand-eye). Use **D405 depth** for metric Z (socket rim, peg top).
1. **Socket pose:** segment bright white top face → find dark polygonal hole → `approxPolyDP` → vertex
   count = shape; centroid = (x,y); minAreaRect/PCA = yaw; depth = Z → back-project to base.
2. **Peg pose:** separate white blob → top cross-section polygon → yaw + grasp center + top height. In-hand
   yaw at grasp = peg yaw (no slip).
3. **Align + insert:** pick → above socket → rotate EE so peg yaw matches socket yaw (skip for circle) →
   descend to ~3 mm above rim → compliant descent + 1–3 mm spiral/Lissajous search watching `O_F_ext`;
   stop on seating force or target depth. Stiff control → loosen collision thresholds, lean on chamfer+search.
4. **Verify:** target Z reached + force in band.

### Tips
- Wrist D405 = last-cm visual servo (look down insertion axis); external D405 = coarse pickup.
- Ensure the bridge exposes `O_F_ext_hat` in the obs (contact force = robustness).
- Pure classical does NOT earn the Intel/OpenVINO bonus — it's the reliable floor; C2's tracker is the cheap bonus.
- Phone photos ≠ D405 frames → re-tune perception on real D405 stills (grab a few; minimal robot time).

## Open items
- [ ] **(Off-robot)** Build + test `robot/c1_vision/` perception (shape ID + yaw) on the example photos; refine on D405 stills.
- [ ] **(Desktop)** C1 extrinsics calibration + depth back-projection + pick→align→insert→verify on the bridge.
- [ ] **(Desktop)** Bring-up gate: bridge build, FCI unlock, RT/communication check, both D405s, 5090 cu128 matmul.
- [ ] **(Desktop)** First teleop + a small diverse demo set → unblocks off-robot O2 (SmolVLA training).
- [x] FCI IP of the robot — `192.168.1.11` (in `robot/franka_xr_teleop/configs/robot.yaml`).
- [x] Control-stack decision — our libfranka bridge (score-neutral, dedicated arm). multipanda = sim sandbox only.
- [ ] C1 peg/hole geometry (chamfered entries help — control the print if possible); refine annotate vocab with the real shape colour/name.
- [ ] Intel bonus scope: does the C2 OpenVINO tracker count, or policy only? (ask organizer)
- [ ] On arrival: verify `InferenceModel` obs/action shapes (Intel box) and C2 tilt sign (`tilt_to_pose(signs=…)`).
