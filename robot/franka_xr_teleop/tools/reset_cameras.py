#!/usr/bin/env python3
"""Hardware-reset the configured RealSense cameras and wait for re-enumeration.

Run this immediately BEFORE any camera session (data collection, teleop, deploy)
when a camera sits on a flaky/bridged USB controller. On such a controller the
D405 only delivers frames on the FIRST pipeline open after a USB reset; resetting
here and then opening each camera exactly once (e.g. the recorder's per-camera
subprocesses) gives clean, drop-free streams. Verified: after a reset, a 60s dual
1280x720@30 capture held 29.8 fps on both cameras with 0 frame drops.

The reset deliberately does NOT test-open any camera (that would consume the one
good post-reset session). It watches each serial drop (teardown) then reappear,
then waits a short settle and returns so the caller's open is the first-after-reset.

Serials come from configs/data_collection.yaml (enabled realsense cameras).
See memory ee26-wrist-d405-flaky for the underlying USB-port story.

Examples:
    python tools/reset_cameras.py            # reset all enabled realsense cams, wait
    python tools/reset_cameras.py --serial 130322273529 --serial 130322271109
"""

import argparse
import sys
import time
from pathlib import Path
from typing import List, Optional


def config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "configs" / "data_collection.yaml"


def serials_from_config() -> List[str]:
    import yaml
    path = config_path()
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text()) or {}
    serials: List[str] = []
    for cam in data.get("cameras", []):
        if cam.get("backend") != "realsense" or not cam.get("enabled", False):
            continue
        serial = str(cam.get("serial", "")).strip()
        if serial:
            serials.append(serial)
    return serials


def present_serials(rs) -> Optional[set]:
    """Serials currently enumerated, or None if librealsense is mid-teardown."""
    try:
        return {d.get_info(rs.camera_info.serial_number) for d in rs.context().query_devices()}
    except RuntimeError:
        return None


def reset_and_wait(serials: List[str], rs, timeout_s: float = 12.0, settle_s: float = 1.5) -> Optional[float]:
    """Hardware-reset the given serials and block until they re-enumerate.

    Returns seconds waited for re-enumeration, or None if it timed out (callers
    may still proceed; pipeline.start will surface a hard failure if truly gone).
    """
    wanted = set(serials)
    reset_any = False
    for dev in rs.context().query_devices():
        try:
            serial = dev.get_info(rs.camera_info.serial_number)
        except RuntimeError:
            continue
        if serial in wanted:
            print(f"hardware_reset {serial} ...", flush=True)
            dev.hardware_reset()
            reset_any = True
    if not reset_any:
        print("warning: none of the requested cameras were found to reset.", file=sys.stderr)
        return None

    t0 = time.monotonic()
    dropped = False
    while time.monotonic() - t0 < timeout_s:
        present = present_serials(rs)
        if present is None or not wanted <= present:
            dropped = True  # teardown observed (or query failed mid-reset)
        elif dropped and wanted <= present:
            elapsed = time.monotonic() - t0
            print(f"cameras re-enumerated in {elapsed:.1f}s; settling {settle_s:.1f}s", flush=True)
            time.sleep(settle_s)
            return elapsed
        time.sleep(0.1)

    print(
        f"warning: cameras did not cleanly re-enumerate within {timeout_s:.0f}s; proceeding anyway.",
        file=sys.stderr,
    )
    time.sleep(settle_s)
    return None


def reset_configured_cameras(serials: Optional[List[str]] = None,
                             timeout_s: float = 12.0, settle_s: float = 1.5) -> bool:
    """Import pyrealsense2, reset the given (or configured) serials, wait. True if reset issued."""
    try:
        import pyrealsense2 as rs
    except ImportError:
        print("Missing dependency: pyrealsense2.", file=sys.stderr)
        raise
    if serials is None:
        serials = serials_from_config()
    if not serials:
        print("warning: no realsense serials to reset (check data_collection.yaml).", file=sys.stderr)
        return False
    reset_and_wait(serials, rs, timeout_s, settle_s)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serial", action="append", default=[], help="Serial to reset; repeatable. Default: all enabled realsense cams in config.")
    parser.add_argument("--timeout", type=float, default=12.0, help="Max seconds to wait for re-enumeration (default 12).")
    parser.add_argument("--settle", type=float, default=1.5, help="Seconds to wait after re-enumeration (default 1.5).")
    args = parser.parse_args()
    serials = args.serial or serials_from_config()
    if not serials:
        print("error: no serials given and none found in config.", file=sys.stderr)
        return 2
    print(f"resetting: {', '.join(serials)}", flush=True)
    reset_configured_cameras(serials, args.timeout, args.settle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
