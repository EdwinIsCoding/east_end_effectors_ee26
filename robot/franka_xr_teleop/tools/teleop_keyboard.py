"""Desktop keyboard/mouse teleop driver for the Franka bridge — Quest-free.

Streams synthetic XRCommand packets (controller pose + triggers + buttons) over UDP to the bridge's
UdpXrSource, so it reuses the EXACT teleop mapper + damped-least-squares IK + safety path that the
Quest uses. Run the bridge with `xr_input_source: udp` (configs/teleop_keyboard.yaml).

Controls (hold the deadman to move — like flying a camera):
  SPACE (hold)  deadman / enable motion     |  G        toggle gripper (open/close)
  W / S         robot +x / -x  (fwd / back) |  ENTER    episode start (button A)
  A / D         robot +y / -y  (left/right) |  BACKSPACE episode end   (button B)
  R / F         robot +z / -z  (up / down)  |  ESC      quit
  U/O J/L I/K   roll / yaw / pitch (with --rotate)

The virtual-controller deltas are pre-mapped through the bridge's DEFAULT xr_to_robot_rotation so the
keys above move the robot's base frame intuitively. If an axis is swapped/inverted on your rig (the
bridge frame config was changed), flip it via --invert or the AXIS map — same idea as calibrating signs.

Hardware-free check:  python teleop_keyboard.py --print --selftest
"""
from __future__ import annotations

import argparse
import json
import math
import socket
import time
from dataclasses import dataclass, field

import numpy as np

DEFAULT_PORT = 28083

# Per-key virtual-controller translation deltas in the XR frame, chosen so that — after the bridge's
# DEFAULT xr_to_robot_rotation R = [[0,0,-1],[-1,0,0],[0,1,0]] (robot = R·xr) — each key moves the
# named ROBOT axis: robot_x=-xr_z, robot_y=-xr_x, robot_z=xr_y.
TRANSLATION_XR = {
    "w": np.array([0.0, 0.0, -1.0]),   # robot +x (forward)
    "s": np.array([0.0, 0.0, 1.0]),    # robot -x
    "a": np.array([-1.0, 0.0, 0.0]),   # robot +y (left)
    "d": np.array([1.0, 0.0, 0.0]),    # robot -y
    "r": np.array([0.0, 1.0, 0.0]),    # robot +z (up)
    "f": np.array([0.0, -1.0, 0.0]),   # robot -z
}
# Rotation keys → (xr axis, sign). Signs typically need a one-time flip on hardware.
ROTATION_XR = {
    "u": (np.array([0.0, 0.0, 1.0]), 1.0), "o": (np.array([0.0, 0.0, 1.0]), -1.0),  # roll
    "j": (np.array([0.0, 1.0, 0.0]), 1.0), "l": (np.array([0.0, 1.0, 0.0]), -1.0),  # yaw
    "i": (np.array([1.0, 0.0, 0.0]), 1.0), "k": (np.array([1.0, 0.0, 0.0]), -1.0),  # pitch
}


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ])


def quat_axis_angle(axis, angle):
    axis = np.asarray(axis, dtype=float)
    n = np.linalg.norm(axis)
    if n < 1e-12:
        return np.array([0.0, 0.0, 0.0, 1.0])
    axis = axis / n
    s = math.sin(angle / 2.0)
    return np.array([axis[0] * s, axis[1] * s, axis[2] * s, math.cos(angle / 2.0)])


@dataclass
class VirtualController:
    pos: np.ndarray = field(default_factory=lambda: np.zeros(3))
    quat: np.ndarray = field(default_factory=lambda: np.array([0.0, 0.0, 0.0, 1.0]))
    gripper_closed: bool = False


def integrate(vc: VirtualController, held: set, dt: float, trans_speed: float,
              rot_speed: float, enable_rotation: bool, invert=(1.0, 1.0, 1.0)) -> VirtualController:
    """Advance the virtual controller pose from currently-held movement keys."""
    inv = np.asarray(invert, dtype=float)
    delta = np.zeros(3)
    for key, vec in TRANSLATION_XR.items():
        if key in held:
            delta += vec
    vc.pos = vc.pos + delta * inv * (trans_speed * dt)
    if enable_rotation:
        for key, (axis, sign) in ROTATION_XR.items():
            if key in held:
                vc.quat = quat_mul(quat_axis_angle(axis, sign * rot_speed * dt), vc.quat)
        vc.quat = vc.quat / np.linalg.norm(vc.quat)
    return vc


def build_packet(seq: int, vc: VirtualController, deadman: bool,
                 button_a: bool = False, button_b: bool = False) -> dict:
    return {
        "timestamp_ns": time.monotonic_ns(),
        "sequence_id": int(seq),
        "position": [float(v) for v in vc.pos],
        "orientation": [float(v) for v in vc.quat],
        "control_trigger": 1.0 if deadman else 0.0,
        "gripper_trigger": 1.0 if vc.gripper_closed else 0.0,
        "button_a": bool(button_a),
        "button_b": bool(button_b),
        "axis_click": False,
    }


def _selftest() -> int:
    vc = VirtualController()
    integrate(vc, {"w"}, dt=1.0, trans_speed=0.1, rot_speed=0.0, enable_rotation=False)
    assert abs(vc.pos[2] + 0.1) < 1e-9, vc.pos          # 'w' → xr -z (robot +x)
    integrate(vc, {"a"}, dt=1.0, trans_speed=0.1, rot_speed=0.0, enable_rotation=False)
    assert abs(vc.pos[0] + 0.1) < 1e-9, vc.pos          # 'a' → xr -x (robot +y)
    vc2 = VirtualController()
    integrate(vc2, {"j"}, dt=1.0, trans_speed=0.0, rot_speed=0.5, enable_rotation=True)
    assert abs(np.linalg.norm(vc2.quat) - 1.0) < 1e-9   # quat stays unit
    pkt = build_packet(3, vc, deadman=True)
    assert pkt["sequence_id"] == 3 and pkt["control_trigger"] == 1.0
    assert len(pkt["position"]) == 3 and len(pkt["orientation"]) == 4
    assert set(pkt) >= {"position", "orientation", "control_trigger", "gripper_trigger",
                        "button_a", "button_b", "axis_click", "timestamp_ns", "sequence_id"}
    print("[selftest] integrate + packet schema OK -> PASS")
    return 0


def run_live(args) -> int:
    from pynput import keyboard  # lazy

    held: set = set()
    pressed: set = set()
    state = {"button_a": False, "button_b": False, "quit": False}
    vc = VirtualController()

    def norm(key):
        try:
            return key.char.lower()
        except AttributeError:
            return key

    def on_press(key):
        k = norm(key)
        if k == keyboard.Key.esc:
            state["quit"] = True
            return False
        if k == keyboard.Key.space:
            held.add("__deadman__"); return
        if k == keyboard.Key.enter:
            state["button_a"] = True; return
        if k == keyboard.Key.backspace:
            state["button_b"] = True; return
        if isinstance(k, str):
            if k == "g" and k not in pressed:
                vc.gripper_closed = not vc.gripper_closed   # edge-triggered toggle
            held.add(k); pressed.add(k)

    def on_release(key):
        k = norm(key)
        if k == keyboard.Key.space:
            held.discard("__deadman__"); return
        if k == keyboard.Key.enter:
            state["button_a"] = False; return
        if k == keyboard.Key.backspace:
            state["button_b"] = False; return
        if isinstance(k, str):
            held.discard(k); pressed.discard(k)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dst = (args.bridge_ip, args.port)
    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()
    print(f"keyboard teleop → udp://{args.bridge_ip}:{args.port}  (hold SPACE=deadman, WASD/RF move, "
          f"G gripper, ESC quit). rotation={'on' if args.rotate else 'off'}")

    period = 1.0 / args.rate
    seq = 0
    last_log = 0.0
    try:
        while not state["quit"]:
            t0 = time.monotonic()
            deadman = "__deadman__" in held
            integrate(vc, held, period, args.trans_speed, args.rot_speed, args.rotate,
                      invert=args.invert)
            pkt = build_packet(seq, vc, deadman, state["button_a"], state["button_b"])
            sock.sendto(json.dumps(pkt, separators=(",", ":")).encode(), dst)
            seq += 1
            if args.print and t0 - last_log > 0.25:
                last_log = t0
                print(f"seq={seq} deadman={int(deadman)} grip={int(vc.gripper_closed)} "
                      f"pos={np.round(vc.pos,3).tolist()}")
            time.sleep(max(0.0, period - (time.monotonic() - t0)))
    finally:
        listener.stop()
        sock.close()
    return 0


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--bridge-ip", default="127.0.0.1", help="Host running the bridge (UdpXrSource).")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--rate", type=float, default=100.0, help="Send rate (Hz).")
    p.add_argument("--trans-speed", type=float, default=0.10, help="Virtual translation speed (m/s).")
    p.add_argument("--rot-speed", type=float, default=0.5, help="Virtual rotation speed (rad/s).")
    p.add_argument("--rotate", action="store_true", help="Enable wrist rotation keys (U/O/J/L/I/K).")
    p.add_argument("--invert", type=float, nargs=3, default=[1.0, 1.0, 1.0],
                   metavar=("X", "Y", "Z"), help="Per-axis sign flip if a robot axis is reversed.")
    p.add_argument("--print", action="store_true", help="Print pose/state periodically.")
    p.add_argument("--selftest", action="store_true", help="Run logic self-test and exit (no input).")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.selftest:
        return _selftest()
    return run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
