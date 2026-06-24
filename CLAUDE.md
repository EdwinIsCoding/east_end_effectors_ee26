# CLAUDE.md — East End Effectors / EE26

Hackathon repo for the **Intel Industrial Robotics Arm Challenge** on our own **Franka Emika Panda**.
Strategy: `PLAN.md`. Two-machine workflow + merge model: `README.md`. Frozen interface: `CONTRACT.md`.

## Which session am I?
- **DESKTOP** (Black workstation · RT kernel · RTX 5090 · wired to the Panda): owns `robot/`, `challenge2/` runtime, and **training execution**. Plan: `plans/PLAN_DESKTOP.md`.
- **OFF-ROBOT** (a Mac · no GPU · no robot): owns `training/` + `CONTRACT.md` authoring; produces code the Desktop runs. Plan: `plans/PLAN_OFFROBOT.md`.

Work only in your owner dirs. If unsure which machine you're on: GPU + robot present ⇒ Desktop.

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
| Robot (FCI IP) | `192.168.2.200` · libfranka `>=0.9.1,<0.10.0` (rec 0.9.2) · firmware 4.2.x |
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

# OFF-ROBOT — training pipeline tests (CPU)
cd training && python -m pytest        # see training/CLAUDE.md for that subproject's specifics
```

## Gotchas (have burned teams before)
- **cu128 PyTorch** for the 5090 — validate `torch.cuda.is_available()` + a matmul before training.
- **FCI single-client:** Desk OR the bridge, never both.
- **RT kernel** needed for the 1 kHz loop (Black workstation has it).
- Compliance: stiff position control can fight the hole on insertion — fallback is VLA+jitter (proven) or a Cartesian-impedance torque controller (tune gains in sim first).
- MuJoCo sim sandbox (optional multipanda): Eigen **3.3.9**, not 3.4.0.

## Reference clones (OFF-ROBOT / this Mac only — not in git)
`~/Downloads/ee26_refs/`: `vla-teleop-franka-v2` (source of `robot/`), `SmolVLA-Testing` (source of `training/`; extra branches: feat/qwen-thomas, labeler, qwen-prompting, xav-qwen).
