# DEPLOY.md — run a trained SmolVLA policy on the Panda

Operator playbook for closed-loop policy control. The training side is in
`training/`; the model spec is in `training/outputs/<run>/policy_card.md`
(CONTRACT §4). Wire format is frozen in `CONTRACT.md §2/§3`.

## Topology (read this first — dual-boot constraint)
The 1 kHz **bridge needs the RT kernel** (`6.8.0-rt8-franka`); **CUDA torch inference
needs the generic kernel** (`6.8.0-124-generic`) — NVIDIA won't build on RT. They
**cannot share the desktop**. So inference and control are split over UDP:

```
 ┌─ inference host ─────────────┐         ┌─ RT desktop ───────────────┐
 │ run_vla_policy.py            │  28082  │ franka_xr_teleop_bridge     │
 │  (OpenVINO@Pantherlake, or   │ ──────► │  --control-source policy    │ ──► Panda
 │   torch-cuda on a GPU box)   │         │  (kJointImpedance, 1 kHz)   │
 │            binds :28081  ◄───┼─ 28081 ─┤  --obs-ip <inference host>  │
 └──────────────────────────────┘         └─────────────────────────────┘
```
- **Primary path (D3 plan):** OpenVINO on Pantherlake → `training/src/inference/openvino_runner.py`.
- **Alt path:** torch-cuda `run_vla_policy.py` on any GPU host pointing `--bridge-ip` at the desktop.
- **Single-box wire test only:** torch `--device cpu` on the RT desktop (too slow for real control).

## 1. Bridge (RT desktop)
Arm prerequisites: FCI active, user-stop released, brakes unlocked, **arm at the
data-collection home and contact-free** (the in-distribution start; avoids a
`cartesian_reflex` on the first action).

```bash
cd robot/franka_xr_teleop
./build/cpp/teleop_bridge/franka_xr_teleop_bridge \
  --robot-ip 192.168.1.11 \
  --control-source policy \
  --policy-action-port 28082 \
  --obs-port 28081 \
  --obs-ip <inference-host-ip>      # omit / 127.0.0.1 if runner is on this box
```
Healthy: `control_command_success_rate≈1`, holding home. (Same reflex-recovery
gotchas as live teleop — see CLAUDE.md "Live teleop" if it trips on start.)

## 2. Runner (inference host)
One command (wraps `run_vla_policy.py` with the contract config + canonical prompt):
```bash
cd robot/franka_xr_teleop
./tools/start_policy_deploy.sh --bridge-ip <RT-desktop-ip> --device cuda
```
It checks the cameras are free, confirms obs are arriving on :28081, then runs the
policy. **Operator keys (in that terminal): `p`=pause `h`=pause+rehome `r`=resume `q`=quit.**
Keep a finger on `p`.

Manual equivalent:
```bash
../../lerobot/.venv/bin/python tools/run_vla_policy.py \
  --policy-type smolvla \
  --policy-path ../../training/outputs/c1_insertion_smolvla \
  --lerobot-root ../../lerobot \
  --config configs/data_collection.yaml \
  --device cuda \
  --task "Insert the white cylindrical block into the white socket." \
  --bridge-ip <RT-desktop-ip> --obs-port 28081 --action-port 28082 --rate-hz 30
```
`--policy-path` accepts the run dir (auto-resolves `checkpoints/last/pretrained_model/`)
or a specific checkpoint export.

## 3. Canonical prompt
`Insert the white cylindrical block into the white socket.` — a trained phrasing.
SmolVLA is phrasing-sensitive; the other 3 trained paraphrases are in `policy_card.md`,
validate them if the canonical underperforms. Do **not** use TASK_C1's old
`"Insert the peg into the hole."` placeholder (never trained).

## 4. Precision / smoothing knobs (run_vla_policy.py)
- `--use-rtc` — Real-Time Chunking: background inference into an ActionQueue, smooth
  30 Hz output despite ~146 ms inference. Recommended for closed-loop.
- `--ema-alpha 0.x` — EMA on joint targets (1.0 = off).
- `--butter-lowpass --butter-lowpass-cutoff 1.0` — order-5 lowpass per joint.
- Joints auto-clamped to Panda limits (0.02 rad margin); gripper binarized with a
  7-step latch to kill chatter.

## 5. Pre-flight smoke test (no robot, no cameras)
Proves a checkpoint loads + infers through the real deploy paths:
```bash
../../lerobot/.venv/bin/python tools/test_deploy_paths.py \
  --policy-path ../../training/outputs/c1_insertion_smolvla/checkpoints/last/pretrained_model \
  --lerobot-root ../../lerobot --device cuda
```
Expect `sync: PASS` + `rtc: PASS`.

## Gotchas
- **One camera consumer at a time** — kill `live_camera_view` / any recorder first
  (`fuser /dev/video*` must be empty); the runner opens both D405s itself.
- **Camera serials** in `configs/data_collection.yaml` must match the physically
  connected D405s (`wrist`→`observation.images.top`, `third_person`→
  `observation.images.third_person_d405`); the image **keys** are contract values.
- **scipy** is required by the runner (Butterworth import) — it's in the lerobot venv
  (`LEROBOT_VENV_SETUP.md`).
- First action jumps → `cartesian_reflex`: start from the home pose; recover with
  `panda_libfranka_sanity --mode recover-only` then `move_to_home`.
