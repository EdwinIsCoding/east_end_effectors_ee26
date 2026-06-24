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

## Phases (40h) — parallelized across 2 on-site
- **0. Bring-up (0–3h):** Black workstation; build bridge (`cmake … Release`) + franka-sanity-checks; FCI unlock; communication/RT check; both D405s enumerate; 5090 cu128 matmul. Gate: safe home + GPU green.
- **1a. Teleop (2–6h):** Quest 3 → XRoboToolkit PC Service → `xrobotics_source` → bridge. Keyboard fallback as insurance.
- **1b. C2 (3–10h):** tracker + PD in parallel (prototype in sim if commanding new controller), then plate-tilt on hardware.
- **2. C1 demos (6–18h):** Quest-collect diverse insertion demos → `record_data_collection_session` → LeRobot. Diversity > volume; fixed prompt phrasing.
- **3. SmolVLA loop (14–28h):** clean→convert→train (SmolVLA-Testing / physical-ai-studio)→deploy (run_vla_policy + jitter)→eval. Gate: non-trivial success.
- **4. OpenVINO + Pi0 (24–36h):** export to Pantherlake (Intel bonus); ball tracker → OpenVINO; LoRA Pi0 if data good.
- **5. Hardening (36–40h):** reliability runs, error-recovery rehearsal, scoring rehearsal, submission.

## Open items
- [ ] FCI IP of the robot.
- [ ] Confirm we're on the Black (RT) workstation for the bridge build.
- [ ] C1 peg/hole geometry (chamfered entries help — control the print if possible).
- [ ] Intel bonus scope: does the C2 OpenVINO tracker count, or policy only? (ask organizer)
