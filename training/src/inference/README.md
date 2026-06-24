# OpenVINO deploy on Pantherlake (Intel bonus) — `openvino_runner.py`

Runs a trained C1 policy through **OpenVINO on the Intel Pantherlake** box and drives the Franka via
the existing bridge. Wire-compatible with `run_vla_policy.py` (see `../../../CONTRACT.md`).

## Topology
```
[D405 wrist + external on Pantherlake] -> OpenVINO inference -(UDP 28082 actions)-> Franka bridge (Black ws)
                              robot state <-(UDP 28081)- Franka bridge
```
At deploy time the two D405s plug into **Pantherlake** (not the Desktop). Robot state and actions cross
the LAN between Pantherlake and the bridge.

## 1. Export (off-robot or Desktop, after training)
```python
from physicalai.inference import InferenceModel  # or physicalai.policies.<ACT|SmolVLA|Pi0>
policy = SmolVLA.load_from_checkpoint("outputs/c1_insertion_smolvla/.../last.ckpt")
policy.export("./exports/c1_smolvla_ov", backend="openvino")
```
`pip install physicalai-train` to export; `pip install physicalai` on Pantherlake to run.

## 2. Run
```bash
# wire-format check anywhere (no HW, no model):
python -m src.inference.openvino_runner --self-test

# loop with mock policy + synthetic cameras (validates UDP path to a bridge):
python -m src.inference.openvino_runner --mock --bridge-ip 192.168.2.200 --max-steps 100

# real deploy on Pantherlake:
python -m src.inference.openvino_runner --model ./exports/c1_smolvla_ov \
    --bridge-ip 192.168.2.200 --device GPU --prompt "Insert the peg into the hole." --rate 30
```

## Verify on the Intel box (assumptions to confirm — docs don't pin these)
- `InferenceModel.select_action(obs)` accepts the LeRobot-style dict we build:
  `{observation.state:[8], observation.images.top, observation.images.third_person_d405, task}`
  and returns an `[8]` action (7 joint pos + gripper). Adjust `build_policy_obs` if its schema differs.
- Image layout/dtype: we pass **HWC uint8 RGB**. If the model expects CHW/float/normalized, preprocess here.
- `--device`: `GPU` = Pantherlake iGPU; try `NPU`/`CPU` and keep the lowest-latency that holds rate.
- Latency: confirm the loop holds `--rate` (30 Hz). Mind the camera→inference→UDP budget.

## Notes
- `--jitter-std` adds controlled Gaussian joint jitter (report's mm-precision insertion trick); default 0.
- Safety mirrors the bridge: joints clamped to Panda limits ±0.02 rad, gripper binarized at 0.5.
- Same runner shape works for the C2 ball tracker if you export that model to OpenVINO (also earns the bonus).
