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
python -m src.inference.openvino_runner --mock --bridge-ip <black-workstation-ip> --max-steps 100

# real deploy on Pantherlake:
python -m src.inference.openvino_runner --model ./exports/c1_smolvla_ov \
    --bridge-ip <black-workstation-ip> --device GPU --prompt "Insert the peg into the hole." --rate 30
```
> `--bridge-ip` is the **Black workstation** running the bridge (reachable from Pantherlake) — **not** the
> robot FCI IP (`192.168.1.11`). The robot only talks to the bridge over libfranka; the runner talks to the bridge over UDP.

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

## Optimization & benchmarking (`bench_openvino.py`)

The deploy must hold the control rate (30 Hz), so single-inference latency has to fit the loop budget
(camera read + inference + UDP send). Two tools measure and improve that:

**1. Loop-level latency — on the runner itself**
```bash
python -m src.inference.openvino_runner --model ./exports/c1_smolvla_ov \
    --bridge-ip <black-ws> --device GPU --profile --report-every 100
```
Prints p50/p95/p99 for each stage (state/image/**infer**/send) and the effective Hz. `infer` is the
term OpenVINO optimization moves; this is the on-hardware proof that the loop holds rate.

**2. Model-level sweep — `bench_openvino.py`**
```bash
# real export on Pantherlake: sweep iGPU + NPU + CPU, require 30 Hz, write a report
python -m src.inference.bench_openvino --model ./exports/c1_smolvla_ov/openvino_model.xml \
    --devices GPU,NPU,CPU --target-hz 30 --fold-preprocess --cache-dir .ovcache --json bench.json

# no model yet? a synthetic vision+state proxy validates the whole pipeline anywhere:
python -m src.inference.bench_openvino --self-test
python -m src.inference.bench_openvino --devices CPU --iters 300
```
Optimization knobs it applies/compares:
- **`PERFORMANCE_HINT=LATENCY`** — tune for one-shot latency, not batch throughput (always on).
- **`CACHE_DIR`** (`--cache-dir`) — cache the compiled model; cuts cold-start compile on relaunch.
- **NNCF INT8 PTQ** (`--int8`, default on) — quantize the graph; usually the biggest CPU/NPU win.
- **Preprocess folding** (`--fold-preprocess`) — push `u8 NHWC → f32 NCHW + /255` *into* the graph, so
  the deploy loop hands the model the raw D405 frame and does zero per-step numpy.

Pick the lowest-latency device/precision whose **p95** still clears `--target-hz`, then deploy the
runner with that `--device` (and quantized export if INT8 wins).

> The synthetic proxy produces real numbers but is **not** the policy — use it to validate the
> optimize→quantize→measure pipeline. Re-measure on Pantherlake with the real export before trusting
> latency figures. Requires `pip install openvino nncf` (lazy-imported, so `--self-test`/imports work
> without them; CI skips the heavy tests).
