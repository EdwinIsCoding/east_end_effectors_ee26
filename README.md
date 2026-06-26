# East End Effectors — EE26

> **Intel Industrial Robotics Arm Challenge**, run on our own **Franka Emika Panda** with a custom libfranka stack.

[![CI](https://github.com/EdwinIsCoding/east_end_effectors_ee26/actions/workflows/ci.yml/badge.svg)](https://github.com/EdwinIsCoding/east_end_effectors_ee26/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/EdwinIsCoding/east_end_effectors_ee26/branch/main/graph/badge.svg)](https://codecov.io/gh/EdwinIsCoding/east_end_effectors_ee26)
[![Challenge 2 coverage](https://img.shields.io/badge/challenge2%20coverage-98%25-brightgreen.svg)](challenge2/)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Tested with pytest](https://img.shields.io/badge/tested%20with-pytest-0a9edc.svg?logo=pytest&logoColor=white)](https://docs.pytest.org/)
[![PyTorch cu128](https://img.shields.io/badge/PyTorch-cu128-EE4C2C.svg?logo=pytorch&logoColor=white)](https://pytorch.org/)
[![Intel OpenVINO](https://img.shields.io/badge/Intel-OpenVINO-0071C5.svg?logo=intel&logoColor=white)](https://docs.openvino.ai/)
[![Robot: Franka Panda](https://img.shields.io/badge/robot-Franka%20Emika%20Panda-orange.svg)](https://franka.de/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![Last commit](https://img.shields.io/github/last-commit/EdwinIsCoding/east_end_effectors_ee26.svg)

Two-machine workflow. The boundary between them is **one file: `CONTRACT.md`**.

```
├── CONTRACT.md        ← FROZEN interface (dataset spec, UDP schemas, policy handoff). The only shared surface.
├── PLAN.md            ← overall strategy (ranked points-optimization plan A1–A8)
├── plans/
│   ├── PLAN_DESKTOP.md   ← Black workstation (RT, wired to Franka): bring-up, teleop, record, deploy
│   └── PLAN_OFFROBOT.md  ← this computer: training pipeline, OpenVINO, Pi0
├── robot/             ← DESKTOP-owned (libfranka bridge, sanity checks, deploy tools, operator console, Quest docs)
├── training/          ← OFF-ROBOT-owned (SmolVLA-Testing pipeline: clean/annotate/convert/train/eval + OpenVINO runner)
└── challenge2/        ← ball-balance (classical PD + learned OpenVINO ball detector)
```

## The plan in one line
Our own libfranka stack (score-neutral, dedicated 40 h arm, proven on this exact insertion task).
**C1 insertion** = classical-CV / VLA floor + SmolVLA → Pi0 (served via OpenVINO) ceiling.
**C2 ball-balance** = classical PD, with an OpenVINO learned tracker for the Intel bonus. See `PLAN.md`.

## Status — end-to-end loop is closing
| Stage | State | Where |
|---|---|---|
| Hardware bring-up (libfranka 0.9.2 ↔ FCI, motion + homing gate) | ✅ working | `robot/franka-sanity-checks/` |
| Live teleop — Quest 3 → XR bridge → robot | ✅ working | `robot/franka_xr_teleop/`, `robot/docs/QUEST3_CONNECTION.md` |
| Data collection → LeRobot v3 dataset (record → split → clean → convert) | ✅ working | `tools/start_collection_session.sh`, `DATA_COLLECTION.md` |
| C1 classical-CV insertion (socket/peg detect + `grasp_axis_deg`) | ✅ detector + tests | `robot/c1_vision/`, `training/TASK_C1.md` |
| C1 VLA ceiling — SmolVLA → Pi0, deploy runners | ⏳ awaiting bulk data / training | `training/`, `robot/franka_xr_teleop/tools/run_vla_policy.py` |
| Intel bonus — OpenVINO inference on Pantherlake | ✅ self-test PASS | `training/src/inference/openvino_runner.py` |
| **C2 — learned ball detector → ONNX/OpenVINO (newest)** | ✅ trained + exported + tests | `challenge2/` |

**Latest update:** the Challenge-2 stack gained a **learned ball detector** (`challenge2/src/ball_net.py`,
`ball_tracker_nn.py`) — a tiny CNN trained on synthetic frames, exported to ONNX and run through
**OpenVINO / onnxruntime** as a drop-in for the classical `ColorBlobTracker`. This earns the Intel
OpenVINO bonus on C2 while keeping the same `BallObservation` interface, so it slots into the PD balance
loop unchanged. Covered by `challenge2/tests/` at **98% line+branch coverage**.

## Who owns what (this is the merge strategy)
- **Desktop person** edits `robot/` + `challenge2/` runtime. **Off-robot person** edits `training/` + `CONTRACT.md` authoring.
- Different directories → conflicts are rare. Keep it that way.
- **`CONTRACT.md` is frozen**: changing a value there (image keys, dims, ports, serials, IP) requires a ping to the other person *before* committing.

## Merge workflow
1. One GitHub remote; both clone. Work on `main`, **pull before you start, push often** (small commits).
2. Stay in your owner dirs. If you must touch the other's dir, say so first.
3. Integration test (do early, with a throwaway 2-episode dataset): Desktop records → off-robot trains a dummy → exports → Desktop deploys. If that loop closes, the contract holds and the real run is just scale.

## Continuous integration
GitHub Actions (`.github/workflows/ci.yml`) runs on every push / PR to `main`:

| Job | What it does |
|---|---|
| **Lint** | `ruff check challenge2/src` |
| **Challenge 2** | full ball-balance test suite under coverage, **fails below 90%** (currently ~98%); uploads to Codecov |
| **Training** | the CI-faithful test suite — `pytest -m "not gpu"` with the lerobot-only test deselected (no GPU, no full lerobot install) |

Run the same checks locally:
```bash
# Challenge 2 — tests + coverage gate (CPU-only)
cd challenge2 && pip install -r requirements-ci.txt
pytest tests -q --cov=src --cov-branch --cov-report=term-missing --cov-fail-under=90

# Training — CI-faithful suite (no GPU, no lerobot)
cd training && pip install -r requirements-ci.txt
pytest tests -m "not gpu" --deselect tests/test_smolvla_fork.py::test_policy_class_importable -q

# Wire-format check for the Intel OpenVINO runner (no hardware)
cd training && python -m src.inference.openvino_runner --self-test
```

## Key facts / constants
| | |
|---|---|
| Robot (FCI IP) | `192.168.1.11` · libfranka `>=0.9.1,<0.10.0` (rec 0.9.2) · firmware 4.2.x |
| Control stack | OUR libfranka bridge (`robot/franka_xr_teleop`), `kJointImpedance`. NOT multipanda, NOT ROS |
| Cameras | wrist D405 → `observation.images.top`; external D405 → `observation.images.third_person_d405`; 1280×720@30 |
| Policy IO | `observation.state`=[8] (7 joints + gripper width); `action`=[8] (7 joint pos + gripper) |
| UDP | actions → bridge port **28082**; observations ← port **28081** |
| GPU | RTX 5090 (Blackwell sm_120) → **PyTorch cu128**, driver ≥570 |
| Teleop | Meta Quest 3 via XRoboToolkit PC Service; keyboard fallback |
| Intel bonus | OpenVINO inference on Pantherlake (`physical-ai-studio`) + C2 learned tracker |

See `CLAUDE.md` for the full operational runbook (bring-up, gotchas, env recipes).
