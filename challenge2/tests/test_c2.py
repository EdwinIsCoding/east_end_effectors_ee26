"""Hardware-free tests for the Challenge-2 ball-balance stack."""
import numpy as np
import pytest

from src.calibration import PlateCalibration
from src.controller import PDBalanceController
from src.tracker import ColorBlobTracker
from src.plate_command import tilt_to_pose, quat_rotate
from src.loop import balance_step


def _orange_disk(h=200, w=200, cu=130, cv=70, r=12):
    img = np.full((h, w, 3), 128, dtype=np.uint8)  # gray bg (sat 0 -> excluded)
    yy, xx = np.ogrid[:h, :w]
    mask = (xx - cu) ** 2 + (yy - cv) ** 2 <= r ** 2
    img[mask] = (255, 80, 0)  # hue ~19 deg, in default (5,25) range
    return img


def test_calibration_center_and_edge():
    cal = PlateCalibration(center_px=(100, 100), radius_px=80, plate_radius_m=0.12)
    assert cal.pixel_to_plate(100, 100) == (0.0, 0.0)
    x, y = cal.pixel_to_plate(180, 100)  # +80px right == +radius
    assert np.isclose(x, 0.12) and np.isclose(y, 0.0)
    _, y_up = cal.pixel_to_plate(100, 20)  # up in image == +y
    assert y_up > 0
    assert not cal.on_plate(100, 100, margin=1.0) is False  # centre is on plate
    assert not cal.on_plate(300, 300)


def test_colorblob_finds_orange_ball():
    obs = ColorBlobTracker().detect(_orange_disk(cu=130, cv=70, r=12))
    assert obs.found
    assert abs(obs.u - 130) < 2 and abs(obs.v - 70) < 2
    assert ColorBlobTracker().detect(np.full((50, 50, 3), 128, np.uint8)).found is False


def test_pd_stabilizes_ball_in_sim():
    """Self-consistent sim: accel = G*tilt with the controller's sign convention -> must converge."""
    G = (5.0 / 7.0) * 9.81
    ctrl = PDBalanceController(kp=3.0, kd=2.0, max_tilt_rad=0.30)
    x = np.array([0.08, -0.05])
    v = np.zeros(2)
    dt = 0.02
    for _ in range(400):  # 8 s
        tilt = np.array(ctrl.update(x, dt))
        accel = G * tilt
        v = v + accel * dt
        x = x + v * dt
    assert np.linalg.norm(x) < 0.01, x


def test_tilt_to_pose_rotates_plate_normal():
    base = (np.array([0.5, 0.0, 0.3]), np.array([0.0, 0.0, 0.0, 1.0]))
    pos0, q0 = tilt_to_pose(base, 0.0, 0.0)
    assert np.allclose(pos0, [0.5, 0.0, 0.3])
    z_axis = quat_rotate(q0, np.array([0, 0, 1.0]))
    assert np.arccos(np.clip(z_axis @ [0, 0, 1], -1, 1)) < 1e-9  # zero tilt -> upright
    _, q1 = tilt_to_pose(base, 0.1, 0.0)
    z1 = quat_rotate(q1, np.array([0, 0, 1.0]))
    assert abs(np.arccos(np.clip(z1 @ [0, 0, 1], -1, 1)) - 0.1) < 1e-6


def test_balance_step_holds_when_no_ball():
    cal = PlateCalibration(center_px=(100, 100), radius_px=80, plate_radius_m=0.12)
    ctrl = PDBalanceController()
    base = (np.array([0.5, 0.0, 0.3]), np.array([0.0, 0.0, 0.0, 1.0]))
    blank = np.full((200, 200, 3), 128, np.uint8)
    assert balance_step(blank, ColorBlobTracker(), cal, ctrl, base, 0.02) is None
    pose = balance_step(_orange_disk(cu=140, cv=100, r=12), ColorBlobTracker(), cal, ctrl, base, 0.02)
    assert pose is not None and np.allclose(pose[0], [0.5, 0.0, 0.3])  # position held, only tilt
