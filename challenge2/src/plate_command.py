"""Map desired plate tilt → a target TCP pose for the bridge.

The controller emits (tilt_x, tilt_y). We apply that as a small rotation on top of a base TCP
orientation (the plate-level pose, or one of the 4 challenge poses). Position is unchanged — only
orientation tilts. `signs` flips the axis directions to match the real camera/plate mounting
(calibrate on hardware: nudge tilt_x and check the ball rolls the expected way).
"""
from __future__ import annotations

import numpy as np

Pose = tuple[np.ndarray, np.ndarray]  # (position[3], quat xyzw[4])


def quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array([
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ])


def quat_from_axis_angle(axis: np.ndarray, angle: float) -> np.ndarray:
    axis = np.asarray(axis, dtype=np.float64)
    axis = axis / max(np.linalg.norm(axis), 1e-12)
    s = np.sin(angle / 2.0)
    return np.array([axis[0] * s, axis[1] * s, axis[2] * s, np.cos(angle / 2.0)])


def quat_rotate(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    qv = np.array([v[0], v[1], v[2], 0.0])
    q_conj = np.array([-q[0], -q[1], -q[2], q[3]])
    return quat_mul(quat_mul(q, qv), q_conj)[:3]


def tilt_to_pose(base: Pose, tilt_x: float, tilt_y: float,
                 signs: tuple[float, float] = (1.0, 1.0)) -> Pose:
    """base pose + (tilt_x about y-axis, tilt_y about x-axis) → target pose (orientation tilted)."""
    base_pos, base_quat = base
    sx, sy = signs
    # tilt_x lowers the +x edge → rotation about +y; tilt_y lowers the +y edge → rotation about -x.
    delta = quat_mul(quat_from_axis_angle([0, 1, 0], sx * tilt_x),
                     quat_from_axis_angle([1, 0, 0], -sy * tilt_y))
    target_quat = quat_mul(np.asarray(base_quat, dtype=np.float64), delta)
    target_quat = target_quat / np.linalg.norm(target_quat)
    return np.asarray(base_pos, dtype=np.float64).copy(), target_quat
