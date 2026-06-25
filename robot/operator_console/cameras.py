"""Camera feeds for the operator console: two D405s -> annotated JPEG frames.

Mirrors the per-camera-thread model of tools/live_camera_view.py (each camera owns its rs.pipeline
and a blocking wait_for_frames() thread, so a stalled camera can't starve the other). Serials come
from data_collection.yaml. With no RealSense (or --synthetic), it generates placeholder frames so
the dashboard and MJPEG endpoints work with nothing plugged in. cv2/pyrealsense2 are imported lazily.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np

# CONTRACT §1 fallbacks if data_collection.yaml can't be read.
FALLBACK_CAMERAS = [
    {"id": "wrist", "label": "wrist_d405 -> observation.images.top", "serial": "130322271109",
     "obs_key": "observation.images.top"},
    {"id": "third_person", "label": "third_person_d405 -> observation.images.third_person_d405",
     "serial": "130322273529", "obs_key": "observation.images.third_person_d405"},
]


def default_camera_config_path() -> Path:
    # robot/operator_console/cameras.py -> robot/franka_xr_teleop/configs/data_collection.yaml
    return (Path(__file__).resolve().parents[1]
            / "franka_xr_teleop" / "configs" / "data_collection.yaml")


def load_cameras(config_path: Optional[str] = None) -> list[dict[str, Any]]:
    """Enabled realsense cameras from data_collection.yaml; fall back to CONTRACT serials."""
    try:
        import yaml
    except ImportError:
        return list(FALLBACK_CAMERAS)
    path = Path(config_path) if config_path else default_camera_config_path()
    if not path.is_file():
        return list(FALLBACK_CAMERAS)
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return list(FALLBACK_CAMERAS)
    cams = []
    for cam in data.get("cameras", []):
        if cam.get("backend") != "realsense" or not cam.get("enabled", False):
            continue
        serial = str(cam.get("serial", "")).strip()
        if not serial:
            continue
        name = cam.get("camera_name", cam.get("id", "camera"))
        obs = cam.get("obs_key", "")
        cams.append({
            "id": str(cam.get("id", name)),
            "label": f"{name} -> {obs}" if obs else str(name),
            "serial": serial,
            "obs_key": obs,
            "width": int(cam.get("color_width", 1280)),
            "height": int(cam.get("color_height", 720)),
            "fps": int(cam.get("fps", 30)),
        })
    return cams or list(FALLBACK_CAMERAS)


def synthetic_frame(width: int, height: int, t: float, label: str) -> np.ndarray:
    """A moving gradient placeholder (BGR) so feeds render without hardware."""
    xs = np.linspace(0, 255, width, dtype=np.float32)
    ys = np.linspace(0, 255, height, dtype=np.float32)
    base = (xs[None, :] + ys[:, None]) / 2.0
    shift = (base + (t * 40.0)) % 255.0
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[..., 0] = shift.astype(np.uint8)             # B
    frame[..., 1] = (255 - shift).astype(np.uint8)     # G
    frame[..., 2] = ((shift * 0.5) % 255).astype(np.uint8)
    return frame


class CameraFeed:
    """One RealSense color camera on its own pipeline + reader thread; caches the latest frame."""

    def __init__(self, serial: str, width: int, height: int, fps: int) -> None:
        import pyrealsense2 as rs  # lazy
        self._rs = rs
        cfg = rs.config()
        cfg.enable_device(serial)
        cfg.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
        self._pipe = rs.pipeline()
        self._pipe.start(cfg)
        self._latest: Optional[np.ndarray] = None
        self._stamp = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                frames = self._pipe.wait_for_frames(1000)
            except Exception:
                continue
            color = frames.get_color_frame()
            if not color:
                continue
            img = np.asanyarray(color.get_data())
            with self._lock:
                self._latest = img
                self._stamp = time.monotonic()

    def latest(self) -> tuple[Optional[np.ndarray], float]:
        with self._lock:
            return (None if self._latest is None else self._latest.copy()), self._stamp

    def close(self) -> None:
        self._stop.set()
        try:
            self._pipe.stop()
        except Exception:
            pass


def annotate(frame: np.ndarray, info: dict[str, Any]) -> np.ndarray:
    """Draw camera + telemetry overlays onto a BGR frame. Best-effort; tolerates missing keys."""
    import cv2
    h, w = frame.shape[:2]
    white, dim, red, green = (245, 245, 245), (170, 170, 170), (80, 80, 235), (120, 210, 120)
    font = cv2.FONT_HERSHEY_SIMPLEX

    def text(s, org, scale=0.5, color=white, thick=1):
        cv2.putText(frame, s, org, font, scale, (0, 0, 0), thick + 2, cv2.LINE_AA)
        cv2.putText(frame, s, org, font, scale, color, thick, cv2.LINE_AA)

    text(str(info.get("label", "camera")), (12, 26), 0.6)
    if info.get("serial"):
        text(f"sn {info['serial']}  {info.get('res', '')}", (12, 46), 0.45, dim)

    live = info.get("live", False)
    badge = f"{'LIVE' if live else 'STALE'}  {info.get('fps', 0):.0f} fps"
    (tw, _), _ = cv2.getTextSize(badge, font, 0.5, 1)
    text(badge, (w - tw - 12, 26), 0.5, green if live else red)

    # bottom telemetry strip
    cv2.rectangle(frame, (0, h - 34), (w, h), (0, 0, 0), -1)
    parts = []
    if "episode" in info:
        parts.append(str(info["episode"]))
    if "control_mode" in info and info["control_mode"]:
        parts.append(str(info["control_mode"]))
    if "gripper" in info:
        parts.append(f"grip {info['gripper'] * 1000:.0f}mm")
    if parts:
        text("  |  ".join(parts), (12, h - 12), 0.5, white)
    if info.get("stamp"):
        s = str(info["stamp"])
        (tw, _), _ = cv2.getTextSize(s, font, 0.5, 1)
        text(s, (w - tw - 12, h - 12), 0.5, dim)
    return frame


class CameraManager:
    """Owns all camera feeds; serves annotated JPEG bytes per camera. Synthetic when no hardware."""

    def __init__(self, cameras: list[dict[str, Any]], synthetic: bool = False,
                 width: int = 1280, height: int = 720, fps: int = 30) -> None:
        self.cameras = cameras
        self.synthetic = synthetic
        self._w, self._h, self._fps = width, height, fps
        self._feeds: dict[str, CameraFeed] = {}

    @classmethod
    def from_config(cls, config_path: Optional[str] = None, synthetic: bool = False,
                    width: int = 1280, height: int = 720, fps: int = 30) -> "CameraManager":
        return cls(load_cameras(config_path), synthetic=synthetic, width=width, height=height, fps=fps)

    def start(self) -> None:
        if self.synthetic:
            return
        try:
            for cam in self.cameras:
                self._feeds[cam["id"]] = CameraFeed(
                    cam["serial"], cam.get("width", self._w), cam.get("height", self._h),
                    cam.get("fps", self._fps))
        except Exception as exc:  # no librealsense / no device -> fall back to synthetic
            print(f"[cameras] RealSense unavailable ({type(exc).__name__}: {exc}); using synthetic feeds")
            self.synthetic = True
            for feed in self._feeds.values():
                feed.close()
            self._feeds.clear()

    def _camera(self, camera_id: str) -> Optional[dict[str, Any]]:
        return next((c for c in self.cameras if c["id"] == camera_id), None)

    def frame_and_fps(self, camera_id: str) -> tuple[Optional[np.ndarray], float, bool]:
        cam = self._camera(camera_id)
        if cam is None:
            return None, 0.0, False
        if self.synthetic or camera_id not in self._feeds:
            w, h = cam.get("width", self._w), cam.get("height", self._h)
            return synthetic_frame(w, h, time.monotonic(), cam["label"]), float(self._fps), False
        img, stamp = self._feeds[camera_id].latest()
        live = img is not None and (time.monotonic() - stamp) < 1.0
        return img, (self._fps if live else 0.0), live

    def jpeg(self, camera_id: str, overlay: Optional[dict[str, Any]] = None, quality: int = 80
             ) -> Optional[bytes]:
        import cv2
        cam = self._camera(camera_id)
        frame, fps, live = self.frame_and_fps(camera_id)
        if frame is None:
            return None
        info = dict(overlay or {})
        info.setdefault("label", cam["label"] if cam else camera_id)
        info.setdefault("serial", cam.get("serial") if cam else "")
        info.setdefault("res", f"{frame.shape[1]}x{frame.shape[0]}")
        info["live"] = live
        info["fps"] = fps
        annotate(frame, info)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buf.tobytes() if ok else None

    def close(self) -> None:
        for feed in self._feeds.values():
            feed.close()
        self._feeds.clear()
