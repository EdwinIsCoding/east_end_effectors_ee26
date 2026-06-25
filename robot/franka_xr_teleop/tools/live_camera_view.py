#!/usr/bin/env python3
"""Live side-by-side viewer for the two D405 cameras (wrist/top + third person).

Replicates the camera-reading model used by the deploy runner
(tools/run_vla_policy.py RealSenseColorCamera): each camera gets its OWN
rs.pipeline and is driven by a blocking wait_for_frames() in its OWN thread, so
a slow/stalled camera can never starve or block the other. The main thread just
composites the latest cached frame from each camera into one OpenCV window.

Serials/resolution come from configs/data_collection.yaml so the view stays in
sync with the recorder; falls back to the known serials if PyYAML/config is
missing.

Press 'q' or Esc to quit.

The wrist D405 (serial 130322271109) is flaky: it only streams on the FIRST
pipeline session after a USB reset, then delivers no frames until reset again.
So the viewer hardware-resets the cameras on startup BY DEFAULT and waits for
re-enumeration (~3-4s). Pass --no-reset to skip that if every camera is known-good.
third_person is unaffected by the reset behavior.

Examples:
    python tools/live_camera_view.py                 # reset both, then show both feeds
    python tools/live_camera_view.py --no-reset      # skip startup reset (faster; wrist may be dead)
    python tools/live_camera_view.py --list-devices  # enumerate, then exit
    python tools/live_camera_view.py --probe 8       # headless: reset, then per-camera FPS
    python tools/live_camera_view.py --fps 15 --color-width 640 --color-height 480
"""

import argparse
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Authoritative serials (configs/data_collection.yaml). Fallback only if the
# config cannot be read. Keep in sync with CONTRACT.md / data_collection.yaml.
FALLBACK_CAMERAS = [
    {"id": "wrist", "label": "wrist_d405 -> observation.images.top", "serial": "130322271109"},
    {"id": "third_person", "label": "third_person_d405", "serial": "130322273529"},
]

# A camera with no fresh frame for this long is drawn as STALE.
STALE_AFTER_S = 1.0


def config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "configs" / "data_collection.yaml"


def load_cameras_from_config() -> Optional[List[Dict[str, str]]]:
    try:
        import yaml
    except ImportError:
        return None
    path = config_path()
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return None
    cameras = []
    for cam in data.get("cameras", []):
        if cam.get("backend") != "realsense" or not cam.get("enabled", False):
            continue
        serial = str(cam.get("serial", "")).strip()
        if not serial:
            continue
        name = cam.get("camera_name", cam.get("id", "camera"))
        obs = cam.get("obs_key", "")
        label = f"{name} -> {obs}" if obs else str(name)
        cameras.append({"id": str(cam.get("id", name)), "label": label, "serial": serial})
    return cameras or None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list-devices", action="store_true", help="List connected RealSense devices and exit.")
    parser.add_argument(
        "--probe",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Headless: stream for SECONDS, print per-camera FPS, then exit (no window).",
    )
    parser.add_argument(
        "--no-reset",
        dest="reset",
        action="store_false",
        help=(
            "Skip the startup hardware reset. Reset is ON by default because the flaky "
            "wrist D405 (serial 130322271109) only streams on the first pipeline session "
            "after a USB reset; use this only if every camera is known-good."
        ),
    )
    parser.set_defaults(reset=True)
    parser.add_argument(
        "--reset-timeout",
        type=float,
        default=12.0,
        help="Max seconds to wait for cameras to drop and re-enumerate after reset (default 12).",
    )
    parser.add_argument(
        "--reset-settle",
        type=float,
        default=1.5,
        help="Seconds to wait after re-enumeration before opening pipelines (default 1.5).",
    )
    parser.add_argument("--color-width", type=int, default=1280)
    parser.add_argument("--color-height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument(
        "--display-height",
        type=int,
        default=540,
        help="Scale each feed to this height for the window (0 = native).",
    )
    parser.add_argument("--window-name", default="EE26 camera feeds")
    return parser.parse_args()


def import_deps():
    try:
        import cv2
        import numpy as np
        import pyrealsense2 as rs
    except ImportError as exc:
        print(f"Missing dependency: {exc.name}. Need opencv-python, numpy, pyrealsense2.", file=sys.stderr)
        raise
    return cv2, np, rs


def list_devices(rs: Any) -> int:
    ctx = rs.context()
    devices = list(ctx.query_devices())
    print(f"librealsense_device_count={len(devices)}")
    for i, dev in enumerate(devices):
        def info(field):
            try:
                return dev.get_info(field) if dev.supports(field) else "unknown"
            except RuntimeError:
                return "unknown"
        print(
            f"[{i}] name={info(rs.camera_info.name)} "
            f"serial={info(rs.camera_info.serial_number)} "
            f"usb={info(rs.camera_info.usb_type_descriptor)}"
        )
    if not devices:
        print("hint: no devices. Try `sudo chmod a+rw /dev/video*` then retry.", file=sys.stderr)
    return 0


class CameraThread(threading.Thread):
    """One RealSense camera: own pipeline, blocking wait_for_frames in its own thread.

    Mirrors RealSenseColorCamera in tools/run_vla_policy.py. Threading decouples
    the cameras so a stalled unit cannot block the other or the UI.
    """

    def __init__(self, cam: Dict[str, str], args: argparse.Namespace, rs: Any, np: Any) -> None:
        super().__init__(daemon=True, name=f"cam-{cam['id']}")
        self.cam = cam
        self.args = args
        self._rs = rs
        self._np = np
        self._lock = threading.Lock()
        self._frame = None  # latest BGR ndarray
        self._frame_count = 0
        self._last_frame_t = 0.0  # monotonic time of last frame
        self._status = "starting"  # starting | streaming | stalled | error
        self._error: Optional[str] = None
        self._stop_event = threading.Event()

    def start_pipeline(self) -> None:
        """Open the pipeline on the calling thread so start errors surface early."""
        rs = self._rs
        self._pipeline = rs.pipeline()
        cfg = rs.config()
        cfg.enable_device(self.cam["serial"])
        cfg.enable_stream(
            rs.stream.color, self.args.color_width, self.args.color_height, rs.format.bgr8, self.args.fps
        )
        self._pipeline.start(cfg)

    def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    frames = self._pipeline.wait_for_frames(1500)
                except RuntimeError:
                    # timeout: leave last frame in place; UI marks it stale
                    continue
                color = frames.get_color_frame()
                if not color:
                    continue
                image = self._np.asanyarray(color.get_data())
                with self._lock:
                    self._frame = image
                    self._frame_count += 1
                    self._last_frame_t = time.monotonic()
                    self._status = "streaming"
        except Exception as exc:  # noqa: BLE001 - surface any backend error to the UI
            with self._lock:
                self._status = "error"
                self._error = str(exc)
        finally:
            try:
                self._pipeline.stop()
            except Exception:
                pass

    def snapshot(self):
        """Return (frame_or_None, status_str, age_seconds, frame_count)."""
        with self._lock:
            frame = None if self._frame is None else self._frame
            count = self._frame_count
            status = self._status
            last_t = self._last_frame_t
            error = self._error
        age = (time.monotonic() - last_t) if last_t else float("inf")
        if status == "streaming" and age > STALE_AFTER_S:
            status = "stalled"
        if status == "error" and error:
            status = f"error: {error}"
        return frame, status, age, count

    def stop(self) -> None:
        self._stop_event.set()


def _present_serials(rs) -> Optional[set]:
    """Serials currently enumerated, or None if librealsense is mid-teardown."""
    try:
        return {d.get_info(rs.camera_info.serial_number) for d in rs.context().query_devices()}
    except RuntimeError:
        return None


def reset_cameras(cameras, rs, timeout_s: float, settle_s: float) -> None:
    """Hardware-reset the configured cameras and wait until they re-enumerate.

    The flaky wrist D405 only streams on the FIRST pipeline open after a USB reset,
    so we must NOT test-open any camera here (that would consume the good session).
    Instead we watch each serial drop (teardown) then reappear, then wait a short
    settle before returning so the caller's pipeline.start() is the first open.
    """
    wanted = {c["serial"] for c in cameras}
    reset_any = False
    for dev in rs.context().query_devices():
        try:
            serial = dev.get_info(rs.camera_info.serial_number)
        except RuntimeError:
            continue
        if serial in wanted:
            print(f"hardware_reset {serial} ...")
            dev.hardware_reset()
            reset_any = True
    if not reset_any:
        print("warning: no configured cameras found to reset.", file=sys.stderr)
        return

    t0 = time.monotonic()
    dropped = False
    while time.monotonic() - t0 < timeout_s:
        present = _present_serials(rs)
        if present is None or not wanted <= present:
            dropped = True  # teardown observed (or query failed mid-reset)
        elif dropped and wanted <= present:
            elapsed = time.monotonic() - t0
            print(f"cameras re-enumerated in {elapsed:.1f}s; settling {settle_s:.1f}s")
            time.sleep(settle_s)
            return
        time.sleep(0.1)

    print(
        f"warning: cameras did not cleanly re-enumerate within {timeout_s:.0f}s; "
        "opening anyway.",
        file=sys.stderr,
    )
    time.sleep(settle_s)


def start_cameras(cameras, args, rs, np):
    threads = []
    for cam in cameras:
        t = CameraThread(cam, args, rs, np)
        try:
            t.start_pipeline()
        except RuntimeError as exc:
            print(f"warning: failed to start {cam['id']} (serial {cam['serial']}): {exc}", file=sys.stderr)
            continue
        t.start()
        threads.append(t)
        print(f"started {cam['id']} (serial {cam['serial']})")
    return threads


def run_probe(threads, seconds: float) -> int:
    print(f"probing for {seconds:.1f}s ...")
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        time.sleep(0.1)
    ok = True
    for t in threads:
        _frame, status, _age, count = t.snapshot()
        fps = count / seconds
        if count == 0:
            ok = False
        print(f"{t.cam['id']:13s} {t.cam['serial']}: {count} frames ({fps:4.1f} fps)  status={status}")
    return 0 if ok else 1


def render_panel(cv2, np, frame, label, status, age, height, panel_w):
    if frame is not None:
        img = frame
        if height and img.shape[0] != height:
            scale = height / img.shape[0]
            img = cv2.resize(img, (int(img.shape[1] * scale), height))
        img = img.copy()
    else:
        img = np.zeros((height, panel_w, 3), dtype=np.uint8)

    streaming = status == "streaming"
    color = (0, 255, 0) if streaming else (0, 165, 255) if status == "stalled" else (0, 0, 255)
    if frame is not None and not streaming:
        # dim a stale frame so it's visually obvious it's not live
        img = (img * 0.45).astype(np.uint8)

    if frame is None:
        msg = f"{label}: {status}"
        cv2.putText(img, msg, (12, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
    else:
        suffix = "" if streaming else f"  [{status.upper()} {age:.1f}s]"
        text = f"{label}{suffix}"
        cv2.rectangle(img, (0, 0), (img.shape[1], 28), (0, 0, 0), -1)
        cv2.putText(img, text, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 1, cv2.LINE_AA)
    return img


def main() -> int:
    args = parse_args()
    cv2, np, rs = import_deps()

    if args.list_devices:
        return list_devices(rs)

    cameras = load_cameras_from_config()
    if cameras is None:
        print("warning: using fallback serials (could not read data_collection.yaml).", file=sys.stderr)
        cameras = FALLBACK_CAMERAS

    if args.reset:
        reset_cameras(cameras, rs, args.reset_timeout, args.reset_settle)

    threads = start_cameras(cameras, args, rs, np)
    if not threads:
        print("error: no cameras started. Run with --list-devices to debug.", file=sys.stderr)
        return 1

    if args.probe > 0:
        rc = run_probe(threads, args.probe)
        for t in threads:
            t.stop()
        for t in threads:
            t.join(timeout=2.0)
        return rc

    disp_h = args.display_height or args.color_height
    panel_w = int(disp_h * args.color_width / args.color_height)
    window = args.window_name
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    try:
        while True:
            panels = []
            for t in threads:
                frame, status, age, _count = t.snapshot()
                panels.append(render_panel(cv2, np, frame, t.cam["label"], status, age, disp_h, panel_w))
            combined = np.hstack(panels) if len(panels) > 1 else panels[0]
            cv2.imshow(window, combined)

            key = cv2.waitKey(15) & 0xFF
            if key in (ord("q"), 27):  # q or Esc
                break
            if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break
    except KeyboardInterrupt:
        pass
    finally:
        for t in threads:
            t.stop()
        for t in threads:
            t.join(timeout=2.0)
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
