"""Live D405 capture for the C1 desktop pipeline (color + aligned depth + intrinsics K).

The off-robot `detect.py` works on a BGR frame; this is the robot-side half that produces that
frame from a real D405, plus the metric depth + intrinsics that `backproject` needs. Mirrors the
data-collection camera config (1280x720 color, 640x480 depth aligned to color, fixed exposure) so
perception thresholds and image geometry transfer between collection and C1.

Serials/roles come from `robot/franka_xr_teleop/configs/data_collection.yaml`:
  130322273529 -> third_person_d405 (overhead, the C1 perception camera)
  130322271109 -> wrist_d405        (observation.images.top, the in-hand servo camera)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

# Role -> serial, from data_collection.yaml (config is authoritative; verified live 2026-06-26).
CAMERA_SERIALS = {
    "third_person_d405": "130322273529",   # overhead / external — full-plate view for socket+peg
    "wrist_d405": "130322271109",           # in-hand — close-range align/servo
}


@dataclass
class Frame:
    bgr: np.ndarray          # HxWx3 uint8 (BGR, ready for detect.detect_scene)
    depth_m: np.ndarray      # HxW float32 metres, aligned to color (0 = no return)
    K: np.ndarray            # 3x3 color intrinsics (fx,fy,cx,cy)
    serial: str


def reset_device(serial: str, timeout_s: float = 15.0) -> None:
    """Hardware-reset one D405 and block until it re-enumerates (needed before each fresh feed)."""
    import time
    import pyrealsense2 as rs
    ctx = rs.context()
    for d in ctx.query_devices():
        if d.get_info(rs.camera_info.serial_number) == serial:
            d.hardware_reset()
            break
    else:
        return  # not currently present; enable_device will surface the error
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(0.5)
        try:  # query races with the device disappearing/reappearing mid-reset
            present = any(d.get_info(rs.camera_info.serial_number) == serial
                          for d in rs.context().query_devices())
        except RuntimeError:
            continue
        if present:
            time.sleep(0.8)  # settle after re-enumeration
            return


class D405:
    """Context-managed RealSense D405 pipeline: color + depth aligned to color."""

    def __init__(self, serial: str, color_wh=(1280, 720), depth_wh=None,
                 fps: int = 30, exposure: Optional[int] = 12000, reset: bool = True):
        # D405 is a SINGLE "Stereo Module" sensor producing both color and depth, so the two
        # streams must share a resolution — default depth to match color.
        # reset=True hardware-resets the device before opening: combined color+depth at 30fps only
        # streams reliably from a freshly-reset device (it degrades across repeated open/close).
        # Mirror data-collection/deploy: reset once, open once, then read many — do NOT churn opens.
        self.serial = serial
        self.color_wh = color_wh
        self.depth_wh = depth_wh or color_wh
        self.fps = fps
        self.exposure = exposure
        self.reset = reset
        self._pipe = None
        self._align = None
        self._depth_scale = None

    def __enter__(self) -> "D405":
        import pyrealsense2 as rs
        if self.reset:
            reset_device(self.serial)
        cfg = rs.config()
        cfg.enable_device(self.serial)
        cfg.enable_stream(rs.stream.color, *self.color_wh, rs.format.bgr8, self.fps)
        cfg.enable_stream(rs.stream.depth, *self.depth_wh, rs.format.z16, self.fps)
        self._pipe = rs.pipeline()
        profile = self._pipe.start(cfg)
        dev = profile.get_device()
        depth_sensor = dev.first_depth_sensor()
        self._depth_scale = depth_sensor.get_depth_scale()
        if self.exposure is not None:
            # On the D405 the color controls live on the same Stereo Module sensor; set exposure
            # on whichever sensor actually exposes the option.
            for s in dev.query_sensors():
                if s.supports(rs.option.exposure):
                    if s.supports(rs.option.enable_auto_exposure):
                        s.set_option(rs.option.enable_auto_exposure, 0)
                    s.set_option(rs.option.exposure, float(self.exposure))
                    break
        self._align = rs.align(rs.stream.color)
        return self

    def __exit__(self, *exc):
        if self._pipe is not None:
            self._pipe.stop()
            self._pipe = None

    def read(self, warmup: int = 5) -> Frame:
        """Grab one synchronized color+depth frame (after a few warmup frames for auto-settle)."""
        import pyrealsense2 as rs
        for _ in range(max(1, warmup)):
            frames = self._pipe.wait_for_frames()
        frames = self._align.process(frames)
        color = frames.get_color_frame()
        depth = frames.get_depth_frame()
        bgr = np.asanyarray(color.get_data())
        depth_m = np.asanyarray(depth.get_data()).astype(np.float32) * self._depth_scale
        intr = color.profile.as_video_stream_profile().get_intrinsics()
        K = np.array([[intr.fx, 0.0, intr.ppx],
                      [0.0, intr.fy, intr.ppy],
                      [0.0, 0.0, 1.0]])
        return Frame(bgr=bgr, depth_m=depth_m, K=K, serial=self.serial)


def depth_at(frame: Frame, center_px, win: int = 5) -> float:
    """Median valid depth (m) in a small window around a pixel — robust to dropouts at the center."""
    u, v = int(round(center_px[0])), int(round(center_px[1]))
    h, w = frame.depth_m.shape
    u0, u1 = max(0, u - win), min(w, u + win + 1)
    v0, v1 = max(0, v - win), min(h, v + win + 1)
    patch = frame.depth_m[v0:v1, u0:u1]
    valid = patch[patch > 0]
    return float(np.median(valid)) if valid.size else 0.0


def _main() -> int:
    import argparse
    import json
    import os
    import cv2

    ap = argparse.ArgumentParser(description="Snapshot D405 color+depth+intrinsics for C1.")
    ap.add_argument("--role", choices=list(CAMERA_SERIALS), default=None,
                    help="capture one named camera (default: all configured)")
    ap.add_argument("--serial", default=None, help="explicit serial (overrides --role)")
    ap.add_argument("--out-dir", default="robot/c1_vision/capture_out", help="where to write artifacts")
    ap.add_argument("--exposure", type=int, default=12000)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    if args.serial:
        targets = [("custom", args.serial)]
    elif args.role:
        targets = [(args.role, CAMERA_SERIALS[args.role])]
    else:
        targets = list(CAMERA_SERIALS.items())

    for name, serial in targets:
        with D405(serial, exposure=args.exposure) as cam:
            f = cam.read()
        color_path = os.path.join(args.out_dir, f"{name}_color.png")
        depth_path = os.path.join(args.out_dir, f"{name}_depth.npy")
        k_path = os.path.join(args.out_dir, f"{name}_K.json")
        cv2.imwrite(color_path, f.bgr)
        np.save(depth_path, f.depth_m)
        valid = f.depth_m[f.depth_m > 0]
        with open(k_path, "w") as fh:
            json.dump({"serial": serial, "K": f.K.tolist(),
                       "color_wh": list(f.bgr.shape[1::-1])}, fh, indent=2)
        med = float(np.median(valid)) if valid.size else 0.0
        print(f"[{name}] serial={serial} color={f.bgr.shape[1::-1]} "
              f"K=fx{f.K[0,0]:.1f} fy{f.K[1,1]:.1f} cx{f.K[0,2]:.1f} cy{f.K[1,2]:.1f} "
              f"depth_valid={100*valid.size/f.depth_m.size:.0f}% median_depth={med:.3f}m")
        print(f"  wrote {color_path}, {depth_path}, {k_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
