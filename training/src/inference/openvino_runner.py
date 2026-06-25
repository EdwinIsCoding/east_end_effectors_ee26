"""OpenVINO policy runner for the Intel Pantherlake bonus (EE26 Challenge 1).

Deploy topology
---------------
Runs on the Intel Pantherlake workstation. The 2x RealSense D405 cameras are plugged into THIS
machine at deploy time. Robot state arrives over UDP from the Franka bridge (running on the Black
workstation) on the observation port; joint actions are sent back to the bridge on the action port.

    [D405 wrist + external] --> Pantherlake (OpenVINO inference) --(UDP 28082)--> Franka bridge
                       robot state  <--(UDP 28081)-- Franka bridge

It mirrors the wire contract of `robot/franka_xr_teleop/tools/run_vla_policy.py` exactly
(see ../../../CONTRACT.md §2/§3) but swaps the Torch/LeRobot policy for a physical-ai-studio
`InferenceModel` exported to OpenVINO. Heavy deps (pyrealsense2, physicalai, openvino) are imported
lazily so this module imports — and `--self-test` runs — on any machine.

Usage
-----
    # local wire-format check, no hardware / no model:
    python -m src.inference.openvino_runner --self-test

    # real deploy on Pantherlake:
    python -m src.inference.openvino_runner --model ./exports/c1_smolvla_ov \
        --bridge-ip <black-workstation-ip> --prompt "Insert the peg into the hole." --device GPU
"""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from typing import Any, Optional

import numpy as np

# --- Contract constants (mirror run_vla_policy.py / CONTRACT.md) -------------
OBS_STATE_KEY = "observation.state"
TASK_KEY = "task"
WRIST_IMAGE_KEY = "observation.images.top"
EXTERNAL_IMAGE_KEY = "observation.images.third_person_d405"
POLICY_STATE_DIM = 8
POLICY_ACTION_DIM = 8
JOINT_ACTION_DIM = 7
ACTION_SPACE = "joint_position_absolute"
DEFAULT_ACTION_PORT = 28082
DEFAULT_OBS_PORT = 28081
# Camera serials: the canonical source is robot/.../configs/data_collection.yaml (CONTRACT §1).
# These are CONTRACT-current fallbacks used only when the config can't be read. Keep in sync.
WRIST_SERIAL = "130322271109"      # wrist D405 -> observation.images.top
EXTERNAL_SERIAL = "130322273529"   # external D405 -> observation.images.third_person_d405

JOINT_LIMIT_MARGIN_RAD = 0.02
PANDA_JOINT_LOWER_LIMITS_RAD = np.asarray(
    [-2.8973, -1.7628, -2.8973, -3.0718, -2.8973, -0.0175, -2.8973], dtype=np.float64)
PANDA_JOINT_UPPER_LIMITS_RAD = np.asarray(
    [2.8973, 1.7628, 2.8973, -0.0698, 2.8973, 3.7525, 2.8973], dtype=np.float64)


# --- Camera serial resolution (canonical source: data_collection.yaml, CONTRACT §1) ---------
def _default_camera_config_path():
    # repo layout: training/src/inference/openvino_runner.py -> robot/.../configs/data_collection.yaml
    from pathlib import Path
    return (Path(__file__).resolve().parents[3]
            / "robot" / "franka_xr_teleop" / "configs" / "data_collection.yaml")


def load_camera_serials(config_path=None):
    """Resolve {WRIST_IMAGE_KEY: serial, EXTERNAL_IMAGE_KEY: serial} from data_collection.yaml.

    data_collection.yaml is the canonical camera->serial mapping (CONTRACT §1); match enabled
    realsense cameras by their obs_key. Falls back to the CONTRACT-current constants when the
    config or PyYAML is unavailable, so --self-test / --mock still run anywhere.
    """
    serials = {WRIST_IMAGE_KEY: WRIST_SERIAL, EXTERNAL_IMAGE_KEY: EXTERNAL_SERIAL}
    try:
        import yaml
    except ImportError:
        return serials
    from pathlib import Path
    path = Path(config_path) if config_path else _default_camera_config_path()
    if not path.is_file():
        return serials
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return serials
    for cam in data.get("cameras", []):
        if cam.get("backend") != "realsense" or not cam.get("enabled", False):
            continue
        serial = str(cam.get("serial", "")).strip()
        obs_key = cam.get("obs_key", "")
        if serial and obs_key in serials:
            serials[obs_key] = serial
    return serials


# --- Action wire format (CONTRACT §2) ---------------------------------------
def clamp_action(action: np.ndarray) -> tuple[np.ndarray, float]:
    """Clamp the [8] joint+gripper action to Panda limits; binarize gripper. Mirrors run_vla_policy."""
    action = np.asarray(action, dtype=np.float64).reshape(-1)
    if action.shape[0] != POLICY_ACTION_DIM:
        raise ValueError(f"Expected {POLICY_ACTION_DIM}D joint+gripper action, got {action.shape}")
    if not np.isfinite(action).all():
        raise ValueError(f"Policy action contains non-finite values: {action.tolist()}")
    lower = PANDA_JOINT_LOWER_LIMITS_RAD + JOINT_LIMIT_MARGIN_RAD
    upper = PANDA_JOINT_UPPER_LIMITS_RAD - JOINT_LIMIT_MARGIN_RAD
    joints = np.clip(action[:JOINT_ACTION_DIM], lower, upper)
    gripper = 1.0 if float(action[JOINT_ACTION_DIM]) >= 0.5 else 0.0
    return joints, gripper


def build_action_message(sequence_id: int, joints: np.ndarray, gripper: float,
                         enabled: bool = True, operator_request_id: int = 0) -> dict[str, Any]:
    """The exact JSON the bridge's policy action source accepts on the action port (CONTRACT §2)."""
    msg = {
        "timestamp_ns": time.monotonic_ns(),
        "sequence_id": int(sequence_id),
        "enabled": bool(enabled),
        "action_space": ACTION_SPACE,
        "joint_positions_rad": [float(v) for v in np.asarray(joints).reshape(-1)],
        "gripper_command": float(np.clip(gripper, 0.0, 1.0)),
    }
    if operator_request_id > 0:
        msg["operator_request_id"] = int(operator_request_id)
    return msg


class ActionSender:
    def __init__(self, bridge_ip: str, action_port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._dst = (bridge_ip, action_port)

    def send(self, message: dict[str, Any]) -> None:
        self._sock.sendto(json.dumps(message, separators=(",", ":")).encode("utf-8"), self._dst)

    def close(self) -> None:
        self._sock.close()


# --- Observation sources ----------------------------------------------------
def state_vector(robot_state: dict[str, Any]) -> np.ndarray:
    """[q0..q6, gripper_width] — mirrors run_vla_policy._robot_state_vector."""
    q = robot_state.get("q", [])
    if len(q) != 7:
        raise ValueError("robot_state.q must contain 7 joints")
    return np.asarray([*map(float, q), float(robot_state.get("gripper_width", 0.0))], dtype=np.float32)


class UdpStateListener:
    """Background UDP listener for the bridge's observation stream (CONTRACT §3)."""

    def __init__(self, bind_ip: str, obs_port: int) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((bind_ip, obs_port))
        self._sock.settimeout(0.1)
        self._latest: Optional[dict[str, Any]] = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                payload, _ = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            try:
                obs = json.loads(payload.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            with self._lock:
                self._latest = obs

    def latest(self) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._latest

    def close(self) -> None:
        self._stop.set()
        self._sock.close()


class RealSenseCameras:
    """Reads the two D405s by serial → {image_key: HWC uint8 RGB}. Lazy pyrealsense2 import."""

    def __init__(self, wrist_serial: str, external_serial: str, width: int = 1280, height: int = 720,
                 fps: int = 30) -> None:
        import pyrealsense2 as rs  # lazy
        self._rs = rs
        self._pipes: dict[str, Any] = {}
        for key, serial in ((WRIST_IMAGE_KEY, wrist_serial), (EXTERNAL_IMAGE_KEY, external_serial)):
            cfg = rs.config()
            cfg.enable_device(serial)
            cfg.enable_stream(rs.stream.color, width, height, rs.format.rgb8, fps)
            pipe = rs.pipeline()
            pipe.start(cfg)
            self._pipes[key] = pipe

    def read(self) -> dict[str, np.ndarray]:
        images = {}
        for key, pipe in self._pipes.items():
            frames = pipe.wait_for_frames()
            color = frames.get_color_frame()
            images[key] = np.asanyarray(color.get_data())
        return images

    def close(self) -> None:
        for pipe in self._pipes.values():
            pipe.stop()


# --- Policy backends --------------------------------------------------------
def build_policy_obs(state: np.ndarray, images: dict[str, np.ndarray], prompt: str) -> dict[str, Any]:
    return {OBS_STATE_KEY: state, TASK_KEY: prompt, **images}


class PhysicalAIPolicy:
    """Wraps physical-ai-studio InferenceModel (OpenVINO). Verify obs/action shapes on the Intel box."""

    def __init__(self, model_path: str, device: str = "GPU") -> None:
        from physicalai.inference import InferenceModel  # lazy
        self._model = InferenceModel.load(model_path, backend="openvino", device=device)

    def reset(self) -> None:
        self._model.reset()

    def select_action(self, obs: dict[str, Any]) -> np.ndarray:
        return np.asarray(self._model.select_action(obs), dtype=np.float64).reshape(-1)


class MockPolicy:
    """Returns a HOLD action (current joints + current gripper) so the arm stays put. For loop tests."""

    def reset(self) -> None:
        pass

    def select_action(self, obs: dict[str, Any]) -> np.ndarray:
        state = np.asarray(obs[OBS_STATE_KEY], dtype=np.float64).reshape(-1)
        return np.concatenate([state[:JOINT_ACTION_DIM], [0.0]])  # hold joints, gripper open


# --- Profiling --------------------------------------------------------------
class StageProfiler:
    """Accumulates per-stage wall-times (ms) so we can confirm the loop fits its rate budget.

    Stages: state (obs fetch), image (camera read), infer (policy), send (UDP). The dominant cost
    on Pantherlake is `infer`; this is how we verify it (camera+infer+send) holds the control rate.
    """

    def __init__(self) -> None:
        self.stages: dict[str, list[float]] = {}

    def add(self, name: str, ms: float) -> None:
        self.stages.setdefault(name, []).append(ms)

    def summary(self, sent: int, wall_s: float) -> str:
        lines = ["[profile] per-stage latency (ms):"]
        for name in ("state", "image", "infer", "send"):
            vals = self.stages.get(name)
            if not vals:
                continue
            a = np.asarray(vals)
            lines.append(f"  {name:<6} p50={np.percentile(a, 50):6.2f} p95={np.percentile(a, 95):6.2f} "
                         f"p99={np.percentile(a, 99):6.2f}  (n={a.size})")
        hz = sent / wall_s if wall_s > 0 else 0.0
        lines.append(f"  loop:  {sent} steps in {wall_s:.2f}s -> {hz:.1f} Hz effective")
        return "\n".join(lines)


# --- Main loop --------------------------------------------------------------
def run_loop(state_source, image_source, policy, sender: ActionSender, prompt: str,
             rate_hz: float, jitter_std: float, max_steps: Optional[int] = None,
             profile: bool = False, report_every: int = 0) -> int:
    """Assemble obs → select_action → clamp → (optional jitter) → send, at rate_hz. Returns steps sent.

    With profile=True, per-stage latencies are collected and a summary is printed on exit (and every
    `report_every` steps if >0). Profiling overhead is a few perf_counter calls — negligible at 30 Hz.
    """
    period = 1.0 / rate_hz
    rng = np.random.default_rng(0)
    policy.reset()
    prof = StageProfiler() if profile else None
    clock = time.perf_counter
    seq, sent = 0, 0
    wall_start = clock()
    while max_steps is None or sent < max_steps:
        loop_start = time.monotonic()
        t = clock()
        robot_state = state_source()
        if robot_state is None:
            time.sleep(period)
            continue
        if prof is not None:
            prof.add("state", (clock() - t) * 1e3)
        state = state_vector(robot_state)
        t = clock()
        images = image_source()
        if prof is not None:
            prof.add("image", (clock() - t) * 1e3)
        t = clock()
        action = policy.select_action(build_policy_obs(state, images, prompt))
        if prof is not None:
            prof.add("infer", (clock() - t) * 1e3)
        joints, gripper = clamp_action(action)
        if jitter_std > 0.0:  # report's controlled-jitter trick for mm-precision insertion
            joints = joints + rng.normal(0.0, jitter_std, size=JOINT_ACTION_DIM)
            joints, _ = clamp_action(np.concatenate([joints, [gripper]]))
        t = clock()
        sender.send(build_action_message(seq, joints, gripper))
        if prof is not None:
            prof.add("send", (clock() - t) * 1e3)
        seq += 1
        sent += 1
        if prof is not None and report_every > 0 and sent % report_every == 0:
            print(prof.summary(sent, clock() - wall_start))
        time.sleep(max(0.0, period - (time.monotonic() - loop_start)))
    if prof is not None:
        print(prof.summary(sent, clock() - wall_start))
    return sent


def _self_test() -> int:
    """Validate the action wire format end-to-end against a local UDP receiver — no HW, no model."""
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rx.bind(("127.0.0.1", 0))
    rx.settimeout(1.0)
    port = rx.getsockname()[1]

    sender = ActionSender("127.0.0.1", port)
    policy = MockPolicy()
    hold_state = {"q": [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785], "gripper_width": 0.04}
    fake_img = np.zeros((720, 1280, 3), dtype=np.uint8)
    n = run_loop(lambda: hold_state, lambda: {WRIST_IMAGE_KEY: fake_img, EXTERNAL_IMAGE_KEY: fake_img},
                 policy, sender, "Insert the peg into the hole.", rate_hz=200.0, jitter_std=0.0,
                 max_steps=5)

    received = 0
    for _ in range(n):
        try:
            payload, _ = rx.recvfrom(65535)
        except socket.timeout:
            break
        msg = json.loads(payload.decode("utf-8"))
        assert msg["action_space"] == ACTION_SPACE, msg
        assert len(msg["joint_positions_rad"]) == JOINT_ACTION_DIM, msg
        assert all(np.isfinite(msg["joint_positions_rad"])), msg
        assert msg["gripper_command"] in (0.0, 1.0), msg
        assert msg["enabled"] is True and "sequence_id" in msg and "timestamp_ns" in msg, msg
        received += 1
    sender.close()
    rx.close()
    ok = received == n == 5
    print(f"[self-test] sent={n} received={received} schema=OK -> {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--self-test", action="store_true", help="Validate wire format locally, then exit.")
    p.add_argument("--mock", action="store_true", help="Run loop with mock policy + synthetic cameras.")
    p.add_argument("--model", type=str, default=None, help="Path to the OpenVINO export dir.")
    p.add_argument("--device", type=str, default="GPU", help="OpenVINO device (GPU=Pantherlake iGPU, CPU, NPU).")
    p.add_argument("--prompt", type=str, default="Insert the peg into the hole.")
    p.add_argument("--bridge-ip", type=str, default="127.0.0.1",
                   help="IP of the Black workstation running the bridge, reachable from this host. "
                        "NOT the robot FCI IP. Use 127.0.0.1 only if the runner is co-located with the bridge.")
    p.add_argument("--action-port", type=int, default=DEFAULT_ACTION_PORT)
    p.add_argument("--obs-bind-ip", type=str, default="0.0.0.0")
    p.add_argument("--obs-port", type=int, default=DEFAULT_OBS_PORT)
    p.add_argument("--rate", type=float, default=30.0, help="Control rate (Hz).")
    p.add_argument("--profile", action="store_true",
                   help="Collect per-stage latency (state/image/infer/send) and print a summary on exit.")
    p.add_argument("--report-every", type=int, default=0,
                   help="With --profile, also print a rolling summary every N steps (0=only at exit).")
    p.add_argument("--jitter-std", type=float, default=0.0, help="Gaussian joint jitter (rad) for mm precision.")
    p.add_argument("--camera-config", type=str, default=None,
                   help="Path to data_collection.yaml for camera serials "
                        "(default: repo robot/franka_xr_teleop/configs/data_collection.yaml).")
    p.add_argument("--wrist-serial", type=str, default=None,
                   help="Override wrist D405 serial (default: resolved from --camera-config).")
    p.add_argument("--external-serial", type=str, default=None,
                   help="Override external D405 serial (default: resolved from --camera-config).")
    p.add_argument("--max-steps", type=int, default=None)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_test:
        return _self_test()

    policy = MockPolicy() if args.mock else PhysicalAIPolicy(args.model, device=args.device)

    if args.mock:
        hold = {"q": [0.0, -0.785, 0.0, -2.356, 0.0, 1.571, 0.785], "gripper_width": 0.04}
        fake = np.zeros((720, 1280, 3), dtype=np.uint8)
        state_source = lambda: hold
        image_source = lambda: {WRIST_IMAGE_KEY: fake, EXTERNAL_IMAGE_KEY: fake}
        sender = ActionSender(args.bridge_ip, args.action_port)
        cameras = None
        listener = None
    else:
        if not args.model:
            raise SystemExit("--model is required unless --mock/--self-test")
        listener = UdpStateListener(args.obs_bind_ip, args.obs_port)
        listener.start()
        serials = load_camera_serials(args.camera_config)
        wrist_serial = args.wrist_serial or serials[WRIST_IMAGE_KEY]
        external_serial = args.external_serial or serials[EXTERNAL_IMAGE_KEY]
        print(f"[cameras] wrist={wrist_serial} external={external_serial}")
        cameras = RealSenseCameras(wrist_serial, external_serial)
        sender = ActionSender(args.bridge_ip, args.action_port)
        state_source = listener.latest
        image_source = cameras.read

    try:
        sent = run_loop(state_source, image_source, policy, sender, args.prompt,
                        args.rate, args.jitter_std, args.max_steps,
                        profile=args.profile, report_every=args.report_every)
        print(f"sent {sent} actions")
    finally:
        sender.close()
        if cameras is not None:
            cameras.close()
        if listener is not None:
            listener.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
