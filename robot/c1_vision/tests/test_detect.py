"""Deterministic tests for the C1 vision module (synthetic shapes; no robot, no photos)."""
import math
import os
import sys

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from c1_vision import detect as d  # noqa: E402

RED_BGR = (0, 0, 255)
WHITE = (255, 255, 255)
GRAY = (130, 130, 130)   # non-white (V<150) -> reads as a hole void


def reg_poly(cx, cy, r, n, rot_deg=0.0):
    return np.array([[cx + r * math.cos(math.radians(rot_deg) + 2 * math.pi * i / n),
                      cy + r * math.sin(math.radians(rot_deg) + 2 * math.pi * i / n)]
                     for i in range(n)], dtype=np.int32)


def _contour_of(points, size=(400, 400)):
    m = np.zeros(size, np.uint8)
    cv2.fillPoly(m, [points.astype(np.int32)], 255)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return cnts[0]


@pytest.mark.parametrize("n,shape", [(3, "triangle"), (4, "square"), (5, "pentagon"), (6, "hexagon")])
def test_classify_polygon_shapes(n, shape):
    s, ns, _ = d.classify_polygon(_contour_of(reg_poly(200, 200, 120, n)))
    assert s == shape and ns == n


def test_classify_circle():
    m = np.zeros((400, 400), np.uint8)
    cv2.circle(m, (200, 200), 120, 255, -1)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    s, ns, _ = d.classify_polygon(cnts[0])
    assert s == "circle" and ns == 0


def test_yaw_is_rotation_equivariant_mod_symmetry():
    period = 360.0 / 5
    yaws = []
    for theta in (0.0, 30.0):
        _, n, poly = d.classify_polygon(_contour_of(reg_poly(200, 200, 120, 5, theta)))
        yaws.append(d.estimate_yaw(poly, n))
    diff = (yaws[1] - yaws[0]) % period
    assert abs(diff - 30.0) < 5.0                   # recovered yaw tracks the 30 deg rotation (mod 72)


def test_circle_has_no_yaw():
    m = np.zeros((400, 400), np.uint8)
    cv2.circle(m, (200, 200), 120, 255, -1)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    _, n, poly = d.classify_polygon(cnts[0])
    assert d.estimate_yaw(poly, n) is None


def test_detect_scene_finds_socket_and_peg():
    img = np.full((640, 640, 3), RED_BGR, np.uint8)
    cv2.rectangle(img, (120, 120), (360, 360), WHITE, -1)              # cube (white face)
    cv2.fillPoly(img, [reg_poly(240, 240, 70, 5, 10.0)], GRAY)         # pentagon hole (void)
    cv2.fillPoly(img, [reg_poly(480, 480, 60, 5, 0.0)], WHITE)         # pentagon peg (solid white)
    scene = d.detect_scene(img)
    assert scene["socket"] is not None and scene["socket"].shape == "pentagon"
    assert abs(scene["socket"].center_px[0] - 240) < 12
    assert abs(scene["socket"].center_px[1] - 240) < 12
    assert scene["peg"] is not None and scene["peg"].shape == "pentagon"
    assert scene["socket"].yaw_deg is not None


def test_peg_has_grasp_axis_for_pickup():
    img = np.full((640, 640, 3), RED_BGR, np.uint8)
    cv2.fillPoly(img, [reg_poly(320, 320, 80, 5, 0.0)], WHITE)   # solid white peg
    scene = d.detect_scene(img)
    assert scene["peg"] is not None
    assert scene["peg"].grasp_axis_deg is not None               # gripper orientation available regardless of shape


def test_backproject_identity_and_offset():
    K = np.array([[600.0, 0, 320.0], [0, 600.0, 240.0], [0, 0, 1.0]])
    T = np.eye(4)
    # principal point at 0.5 m depth -> straight ahead
    np.testing.assert_allclose(d.backproject((320, 240), 0.5, K, T), [0, 0, 0.5], atol=1e-9)
    # +60 px right at 0.5 m -> +0.05 m x
    np.testing.assert_allclose(d.backproject((380, 240), 0.5, K, T), [0.05, 0, 0.5], atol=1e-9)
    # extrinsic translation adds in base frame
    T2 = np.eye(4); T2[:3, 3] = [1.0, 2.0, 3.0]
    np.testing.assert_allclose(d.backproject((320, 240), 0.5, K, T2), [1.0, 2.0, 3.5], atol=1e-9)
