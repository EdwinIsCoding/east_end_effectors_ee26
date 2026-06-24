"""PD balance controller: ball position/velocity on the plate → desired plate tilt.

Sign convention (validate/flip on hardware via PlateCommand signs): tilt_x is the angle by which
the plate's +x edge is LOWERED, which accelerates the ball toward +x. So to recover a ball at +x we
command tilt_x < 0 (raise the +x edge). With negative feedback `tilt = -Kp*pos - Kd*vel`, this holds.
"""
from __future__ import annotations

from typing import Optional

import numpy as np


class PDBalanceController:
    def __init__(self, kp: float = 3.0, kd: float = 2.0, max_tilt_rad: float = 0.30,
                 deadband_m: float = 0.003) -> None:
        self.kp = kp
        self.kd = kd
        self.max_tilt = max_tilt_rad
        self.deadband = deadband_m
        self._prev: Optional[np.ndarray] = None

    def reset(self) -> None:
        self._prev = None

    def update(self, pos_xy, dt: float) -> tuple[float, float]:
        pos = np.asarray(pos_xy, dtype=np.float64).reshape(2)
        if np.linalg.norm(pos) < self.deadband:
            pos = np.zeros(2)
        if self._prev is None or dt <= 0.0:
            vel = np.zeros(2)
        else:
            vel = (pos - self._prev) / dt
        self._prev = pos
        tilt = -self.kp * pos - self.kd * vel
        tilt = np.clip(tilt, -self.max_tilt, self.max_tilt)
        return float(tilt[0]), float(tilt[1])
