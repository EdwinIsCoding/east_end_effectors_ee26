"""Tie tracker → calibration → PD → plate-tilt pose together, and stream poses to a sink.

The DESKTOP implements `CommandSink.send(pose)` to forward the target TCP pose to the bridge
(e.g. via the Cartesian-pose teleop path). Everything here is hardware-free and testable with a
mock frame source + mock sink.

Bonus task (4 poses): feed `base_pose` from a slow trajectory between the 4 challenge poses; the PD
loop rejects ball drift on top of it. Keep base-pose moves smooth and low-acceleration.
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Protocol

import numpy as np

from .calibration import PlateCalibration
from .controller import PDBalanceController
from .plate_command import Pose, tilt_to_pose


class CommandSink(Protocol):
    def send(self, pose: Pose) -> None: ...


def balance_step(frame_rgb: np.ndarray, tracker, calib: PlateCalibration,
                 controller: PDBalanceController, base_pose: Pose, dt: float,
                 signs=(1.0, 1.0)) -> Optional[Pose]:
    """One control tick. Returns the target pose, or None if the ball wasn't found (hold)."""
    obs = tracker.detect(frame_rgb)
    if not obs.found or not calib.on_plate(obs.u, obs.v):
        return None
    pos = calib.pixel_to_plate(obs.u, obs.v)
    tilt_x, tilt_y = controller.update(pos, dt)
    return tilt_to_pose(base_pose, tilt_x, tilt_y, signs)


def run(frame_source: Callable[[], np.ndarray], tracker, calib: PlateCalibration,
        controller: PDBalanceController, base_pose_source: Callable[[], Pose],
        sink: CommandSink, rate_hz: float = 60.0, signs=(1.0, 1.0),
        max_steps: Optional[int] = None) -> int:
    period = 1.0 / rate_hz
    controller.reset()
    sent = 0
    while max_steps is None or sent < max_steps:
        start = time.monotonic()
        pose = balance_step(frame_source(), tracker, calib, controller,
                            base_pose_source(), period, signs)
        if pose is not None:
            sink.send(pose)
        sent += 1
        time.sleep(max(0.0, period - (time.monotonic() - start)))
    return sent
