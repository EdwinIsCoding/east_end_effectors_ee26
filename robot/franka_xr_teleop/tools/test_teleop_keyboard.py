"""Logic tests for the keyboard teleop driver (no pynput / no robot)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import teleop_keyboard as tk  # noqa: E402


def test_translation_keys_map_to_robot_axes():
    vc = tk.VirtualController()
    tk.integrate(vc, {"w"}, 1.0, 0.1, 0.0, False)
    assert np.isclose(vc.pos[2], -0.1)   # 'w' → xr -z (robot +x forward)
    tk.integrate(vc, {"a"}, 1.0, 0.1, 0.0, False)
    assert np.isclose(vc.pos[0], -0.1)   # 'a' → xr -x (robot +y left)
    tk.integrate(vc, {"r"}, 1.0, 0.1, 0.0, False)
    assert np.isclose(vc.pos[1], 0.1)    # 'r' → xr +y (robot +z up)


def test_opposing_keys_cancel():
    vc = tk.VirtualController()
    tk.integrate(vc, {"w", "s"}, 1.0, 0.1, 0.0, False)
    assert np.allclose(vc.pos, 0.0)


def test_invert_flips_axis():
    vc = tk.VirtualController()
    tk.integrate(vc, {"w"}, 1.0, 0.1, 0.0, False, invert=(1.0, 1.0, -1.0))
    assert np.isclose(vc.pos[2], 0.1)    # z inverted


def test_rotation_keeps_quat_unit_and_off_by_default():
    vc = tk.VirtualController()
    tk.integrate(vc, {"j"}, 1.0, 0.0, 0.5, enable_rotation=False)
    assert np.allclose(vc.quat, [0, 0, 0, 1])      # rotation ignored when disabled
    tk.integrate(vc, {"j"}, 1.0, 0.0, 0.5, enable_rotation=True)
    assert abs(np.linalg.norm(vc.quat) - 1.0) < 1e-9
    assert not np.allclose(vc.quat, [0, 0, 0, 1])


def test_packet_matches_udp_xr_source_fields():
    vc = tk.VirtualController(gripper_closed=True)
    pkt = tk.build_packet(7, vc, deadman=True, button_a=True)
    assert pkt["sequence_id"] == 7
    assert pkt["control_trigger"] == 1.0 and pkt["gripper_trigger"] == 1.0
    assert pkt["button_a"] is True and pkt["button_b"] is False
    assert len(pkt["position"]) == 3 and len(pkt["orientation"]) == 4
    # fields the C++ ParseXrCommand reads:
    for k in ("position", "orientation", "control_trigger", "gripper_trigger",
              "button_a", "button_b", "axis_click", "timestamp_ns", "sequence_id"):
        assert k in pkt


def test_selftest_passes():
    assert tk._selftest() == 0
