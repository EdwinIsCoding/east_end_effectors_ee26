"""Tests for the operator-console camera layer. JPEG/annotate paths are gated on cv2."""
import pytest

from robot.operator_console import cameras as cam


def test_load_cameras_from_real_config_matches_contract():
    cams = cam.load_cameras()
    by_serial = {c["serial"] for c in cams}
    # CONTRACT §1 serials (or the same-valued fallbacks).
    assert "130322271109" in by_serial      # wrist -> observation.images.top
    assert "130322273529" in by_serial      # external -> third_person_d405
    assert all("obs_key" in c and "label" in c for c in cams)


def test_load_cameras_falls_back_when_config_missing():
    cams = cam.load_cameras("/nonexistent/data_collection.yaml")
    assert cams == cam.FALLBACK_CAMERAS


def test_synthetic_frame_shape():
    f = cam.synthetic_frame(320, 180, t=1.0, label="x")
    assert f.shape == (180, 320, 3) and f.dtype.name == "uint8"


def test_jpeg_synthetic_encodes_with_overlay():
    pytest.importorskip("cv2")
    mgr = cam.CameraManager(cam.FALLBACK_CAMERAS, synthetic=True, width=320, height=180)
    mgr.start()
    data = mgr.jpeg("wrist", overlay={"episode": "REC 02", "gripper": 0.03,
                                      "control_mode": "JOINT_IMPEDANCE", "stamp": "12:00:00"})
    assert data is not None and data[:2] == b"\xff\xd8"  # JPEG SOI marker
    assert mgr.jpeg("nonexistent-camera") is None
    mgr.close()
