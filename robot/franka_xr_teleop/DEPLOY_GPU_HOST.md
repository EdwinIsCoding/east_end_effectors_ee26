# DEPLOY_GPU_HOST.md — running the pi0 policy from a **separate GPU PC**

This guide is for the **GPU machine** (the box with the RTX card), *not* the RT
desktop wired to the Panda. It is the **inference host**: it runs the policy and
the cameras, and streams joint targets to the robot over the network. Follow this
to set that machine up — physically and in software — and to launch a deploy.

For the robot/bridge side and the general deploy theory, see `DEPLOY.md`. This file
is the GPU-host-specific companion.

---

## 0. Why a separate deployment (read this first)

The pi0 checkpoint (`training/outputs/pretrained_model/`, ~8.3 GB, PaliGemma ~3B)
**must run on a GPU** — on CPU a single 50-step chunk inference is ~1.5 s, far too
slow for real-time control even with RTC. But the RT desktop **cannot** be the GPU
box at the same time:

- The 1 kHz Franka control loop (the bridge) needs the **RT kernel**
  (`6.8.0-rt8-franka`).
- CUDA/torch inference needs the **generic kernel** (`6.8.0-124-generic`) — NVIDIA
  won't build on the RT kernel.
- They can't share one boot, and dual-booting means choosing robot **or** GPU.

A **second physical machine with a GPU** removes that trade-off: the desktop stays
on the RT kernel driving the robot, and this GPU PC does inference **at the same
time**, the two talking over UDP. The price is that this machine must host the
cameras and a copy of the checkpoint (details below).

```
 ┌─ THIS machine: GPU PC (generic) ──┐                 ┌─ RT desktop (robot box) ─────┐
 │ run_vla_policy.py  --device cuda  │   actions UDP   │ franka_xr_teleop_bridge      │  FCI / Ethernet
 │  • reads 2× D405 over USB  ◄─USB  │ ──:28082──────► │  --control-source policy     │ ──(physical)──► Panda
 │  • binds :28081 for obs    ◄──────┼──:28081 obs─────┤  --obs-ip <this-GPU-PC>      │     192.168.1.11
 └───────────────────────────────────┘                 └──────────────────────────────┘
   cameras live HERE                                      only THIS box wires to the robot
```

**Key fact:** images never cross the wire. The bridge sends only the robot **joint
state** (`robot_state.q[7]` + gripper width → `observation.state`). This GPU PC
opens the two D405 cameras **itself** over USB to build `observation.images.*`. So
the cameras must be physically plugged into **this** machine.

---

## 1. Physical setup (this machine)

1. **Network link to the RT desktop.** Put both machines on one subnet — a lab
   switch, or a direct Ethernet cable between a free NIC on each with static IPs.
   This machine does **not** need to be on the robot LAN (`192.168.1.0/24`); keep
   that isolated on the desktop's `enp8s0f2`. You only need: each box can ping the
   other, and UDP **28081**/**28082** are not firewalled.
   - Note the desktop's IP **on the shared subnet** (call it `<desktop-ip>`), which
     is *not* the robot's `192.168.1.11`.
   - Note this machine's IP on that subnet (call it `<gpu-host-ip>`).

2. **Move the two Intel RealSense D405s to this machine.** Unplug them from the
   desktop and plug both into this PC's **USB3** ports. The wrist camera is mounted
   on the arm, so you'll need its USB cable to reach here (an **active USB3
   extender** if it won't stretch).
   - Put **each D405 on its own USB3 controller** if possible — two 1280×720@30
     streams saturate a shared controller and the wrist unit is flaky on a bridged
     port (see memory `ee26-wrist-d405-flaky`).
   - The serials must match `configs/data_collection.yaml` (the runner selects
     cameras by serial):

     | role | serial | obs key |
     |---|---|---|
     | third_person | `130322273529` | `observation.images.third_person_d405` |
     | wrist | `130322271109` | `observation.images.top` |

     Confirm what's connected: `lerobot/.venv/bin/python tools/run_vla_policy.py
     --list-cameras` (or `rs-enumerate-devices`). If a serial differs, fix it in
     `configs/data_collection.yaml` before deploying.

3. **GPU.** An NVIDIA card with a driver that supports your torch build (for a 5090
   = Blackwell **sm_120**, driver ≥570 + **CUDA 12.8 / cu128**). `nvidia-smi` must
   list the card.

---

## 2. Software setup (this machine)

### 2a. Repo + lerobot venv (cu128)
Clone the repo and stand up the lerobot venv exactly like the desktop
(`LEROBOT_VENV_SETUP.md`), but **pin the same lerobot commit** and install the
**`pi`** extra (pi0 needs it; it also pulls scipy for the runner's Butterworth):

```bash
# the repo
git clone <repo-url> east_end_effectors_ee26 && cd east_end_effectors_ee26

# lerobot @ the SAME commit the model was trained/served with
git clone https://github.com/huggingface/lerobot lerobot
git -C lerobot checkout 05a52238

cd lerobot
uv python install 3.13
uv venv --python 3.13 --seed .venv
source .venv/bin/activate
uv pip install -e ".[smolvla,pi,intelrealsense,dataset]"

# 5090/Blackwell: torch MUST be cu128 (sm_120). Force it explicitly.
uv pip install --index-url https://download.pytorch.org/whl/cu128 torch
```
Also needs system **ffmpeg** (torchcodec video backend) and **pyrealsense2** (comes
via the `intelrealsense` extra).

Validate the GPU stack before anything else:
```bash
nvidia-smi                         # lists the card
python -c "import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# want: 2.x+cu128  True  NVIDIA ...
```

### 2b. The pi0 checkpoint (copy it over — it is NOT in git)
The checkpoint is gitignored (~8.3 GB). Copy the whole directory from the desktop:
```bash
# from the desktop, or pull from here:
rsync -av --progress \
  <desktop-ip>:east_end_effectors_ee26/training/outputs/pretrained_model/ \
  training/outputs/pretrained_model/
```
It must contain `config.json`, `model.safetensors`, and the
`policy_pre/postprocessor*` files.

### 2c. Hugging Face login (gated tokenizer)
pi0's text preprocessor loads the **gated** `google/paligemma-3b-pt-224` tokenizer.
The HF token cache is **per machine**, so log in here too, with an account that has
been granted access to that repo and a token that can **read gated repos**:
```bash
lerobot/.venv/bin/hf auth login        # paste a token with "read gated repos" scope
lerobot/.venv/bin/hf auth whoami       # confirm your user
# sanity: this must NOT 401/403
python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('google/paligemma-3b-pt-224'); print('tokenizer OK')"
```
(If you hit 403: accept the license at https://huggingface.co/google/paligemma-3b-pt-224
**and** ensure the token has the "Read access to public gated repos" permission.)

---

## 3. Pre-flight benchmark (no robot, no network)
Before touching the robot, prove the checkpoint loads and time the real GPU
inference (this also tells you the RTC `inference_delay` to expect):
```bash
cd robot/franka_xr_teleop
../../lerobot/.venv/bin/python tools/test_deploy_paths.py \
  --policy-type pi0 \
  --policy-path ../../training/outputs/pretrained_model \
  --lerobot-root ../../lerobot --device cuda
```
Expect `sync: PASS` + `rtc: PASS`. Note the `Measured: median=…ms inference_delay=…
steps` line — if the delay is large at 30 Hz, lower `--rate-hz` at deploy time for
RTC headroom (same logic the launcher uses for CPU). For reference, on CPU this
checkpoint measured ~1513 ms/chunk; GPU is far faster.

---

## 4. Deploy

**Order matters: bridge first (on the desktop), then the runner here.**

### 4a. On the RT desktop — start the policy-mode bridge
Arm at the data-collection home and contact-free; FCI active, user-stop released,
brakes unlocked. Point `--obs-ip` at **this GPU PC**:
```bash
cd robot/franka_xr_teleop
./build/cpp/teleop_bridge/franka_xr_teleop_bridge \
  --robot-ip 192.168.1.11 \
  --control-source policy \
  --policy-action-port 28082 \
  --obs-port 28081 \
  --obs-ip <gpu-host-ip>
```
Healthy: `control_command_success_rate≈1`, holding home.

### 4b. On THIS GPU PC — start the runner
```bash
cd robot/franka_xr_teleop
./tools/start_policy_deploy.sh \
  --policy-type pi0 \
  --policy-path "$(pwd)/../../training/outputs/pretrained_model" \
  --bridge-ip <desktop-ip> \
  --device cuda
```
The launcher kills stray camera consumers, **resets the D405s**, verifies obs are
arriving on `:28081`, then runs the policy. RTC is on by default; rate auto-picks
30 Hz on cuda. Default task is the canonical
`Insert the white cylindrical block into the white socket.` (override with
`--task`). Add `--rate-hz N` if the pre-flight `inference_delay` was high.

**Operator keys (in this terminal):** `p`=pause  `h`=pause+rehome  `r`=resume
`q`=quit. **Keep a finger on `p`.**

---

## 5. Gotchas (GPU-host specific)
- **No obs on :28081** → the bridge isn't running, isn't in `--control-source
  policy`, or its `--obs-ip` isn't this machine's address / a port is firewalled.
- **One camera consumer at a time.** The runner opens both D405s; kill any
  `live_camera_view`/recorder first and check `fuser /dev/video*` is empty. On the
  bridged-USB flakiness, a D405 only delivers frames on the **first** pipeline open
  after a USB reset — the launcher's reset handles this, so don't open the cameras
  between reset and runner.
- **cu128 torch** — verify `torch.cuda.is_available()` is `True` and the matmul
  test passes; the wrong wheel silently falls back to CPU (slow, looks like a hang).
- **First action jumps → `cartesian_reflex`** on the robot: start from the home
  pose; recover on the desktop with `panda_libfranka_sanity --mode recover-only`
  then `move_to_home` (see `DEPLOY.md` / CLAUDE.md).
- **Camera read rate.** Two 1280×720@30 streams on one USB controller throttle to
  ~7.8 fps. Harmless under RTC (obs sampled once per chunk) but split the cameras
  across controllers for the cleanest real-time behaviour.
