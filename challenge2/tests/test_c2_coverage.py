"""Branch/edge coverage for the C2 stack: calibration helpers, controller reset,
wrap-around hue + Hough fallback tracker, the NN tracker's onnxruntime path, and the
full `loop.run` tick loop. Hardware-free (orange/red synthetic disks, mock sink)."""
import numpy as np
import pytest

from src.calibration import PlateCalibration
from src.controller import PDBalanceController
from src.tracker import ColorBlobTracker, HoughTracker, BallObservation
from src.loop import run


def _disk(color, h=200, w=200, cu=130, cv=70, r=12):
    img = np.full((h, w, 3), 128, dtype=np.uint8)  # gray bg (sat 0 -> excluded)
    yy, xx = np.ogrid[:h, :w]
    img[(xx - cu) ** 2 + (yy - cv) ** 2 <= r ** 2] = color
    return img


def _identity_pose():
    return np.zeros(3), np.array([0.0, 0.0, 0.0, 1.0])


def test_calibration_normalized_at_rim():
    cal = PlateCalibration(center_px=(100, 100), radius_px=80, plate_radius_m=0.12)
    nx, ny = cal.normalized(180, 100)   # +radius right -> +1, image-up flips v
    assert np.isclose(nx, 1.0) and np.isclose(ny, 0.0)
    _, ny_up = cal.normalized(100, 20)
    assert ny_up > 0


def test_controller_reset_clears_velocity_state():
    ctrl = PDBalanceController()
    ctrl.update((0.05, 0.0), dt=0.02)          # seed _prev
    assert ctrl._prev is not None
    ctrl.reset()
    assert ctrl._prev is None                   # first post-reset tick sees zero velocity
    tilt = ctrl.update((0.05, 0.0), dt=0.02)
    assert len(tilt) == 2


def test_colorblob_wraparound_hue_finds_red():
    tracker = ColorBlobTracker(hue_range=(350, 10))   # wrap-around (red)
    obs = tracker.detect(_disk((255, 0, 0), cu=120, cv=80, r=14))
    assert obs.found and abs(obs.u - 120) < 2 and abs(obs.v - 80) < 2


def test_hough_tracker_detects_and_misses():
    cv2 = pytest.importorskip("cv2")
    img = np.zeros((200, 200, 3), dtype=np.uint8)
    cv2.circle(img, (100, 100), 30, (255, 255, 255), -1)
    obs = HoughTracker().detect(img)
    assert obs.found and abs(obs.u - 100) < 8 and abs(obs.v - 100) < 8 and obs.radius_px > 0
    blank = HoughTracker().detect(np.zeros((200, 200, 3), dtype=np.uint8))
    assert blank.found is False


def test_nn_tracker_onnxruntime_backend_and_threshold(tmp_path):
    pytest.importorskip("torch")
    pytest.importorskip("onnxruntime")
    from src import ball_net as bn
    from src.ball_tracker_nn import NNBallTracker

    net, _ = bn.train(steps=40, batch=32)
    onnx_path = str(tmp_path / "ball.onnx")
    bn.export_onnx(net, onnx_path)

    # Force the onnxruntime fallback path explicitly (skip OpenVINO).
    tracker = NNBallTracker(onnx_path, backend="onnxruntime")
    assert tracker.backend == "onnxruntime"
    frame = (np.transpose(bn.synth_batch(1, seed=7)[0][0], (1, 2, 0)) * 255).astype(np.uint8)
    assert isinstance(tracker.detect(frame), BallObservation)

    # present_thresh above any sigmoid output -> always "not found".
    strict = NNBallTracker(onnx_path, backend="onnxruntime", present_thresh=1.1)
    assert strict.detect(frame).found is False


class _RecordingSink:
    def __init__(self):
        self.poses = []

    def send(self, pose):
        self.poses.append(pose)


def test_loop_run_sends_poses_and_holds_when_lost():
    cal = PlateCalibration(center_px=(130, 70), radius_px=90, plate_radius_m=0.12)
    ctrl = PDBalanceController()
    base = _identity_pose()

    # Ball visible -> a pose is sent every tick.
    sink = _RecordingSink()
    sent = run(lambda: _disk((255, 80, 0)), ColorBlobTracker(), cal, ctrl,
               lambda: base, sink, rate_hz=1000.0, max_steps=3)
    assert sent == 3 and len(sink.poses) == 3

    # Ball absent (gray frame) -> balance_step returns None -> sink.send never called.
    sink2 = _RecordingSink()
    sent2 = run(lambda: np.full((200, 200, 3), 128, np.uint8), ColorBlobTracker(), cal,
                ctrl, lambda: base, sink2, rate_hz=1000.0, max_steps=2)
    assert sent2 == 2 and sink2.poses == []
