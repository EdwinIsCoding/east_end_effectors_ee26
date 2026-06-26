"""Tests for the C2 learned ball detector + ONNX/OpenVINO inference path (Intel bonus)."""
import numpy as np
import pytest

torch = pytest.importorskip("torch")
from src import ball_net as bn  # noqa: E402
from src.tracker import BallObservation  # noqa: E402


def test_synth_batch_shapes_and_targets():
    x, y = bn.synth_batch(8, seed=1)
    assert x.shape == (8, 3, bn.INPUT_SIZE, bn.INPUT_SIZE) and x.dtype == np.float32
    assert y.shape == (8, 3)
    assert ((y[:, :2] >= 0) & (y[:, :2] <= 1)).all()
    assert set(np.unique(y[:, 2])).issubset({0.0, 1.0})


def test_net_forward_shape_and_range():
    net = bn.build_net()
    out = net(torch.from_numpy(bn.synth_batch(4, seed=2)[0]))
    assert out.shape == (4, 3)
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0   # sigmoid


def test_trained_model_localizes_on_synthetic():
    import torch
    net, _ = bn.train(steps=400, batch=48)
    x, y = bn.synth_batch(64, seed=99)
    with torch.no_grad():
        pred = net(torch.from_numpy(x)).numpy()
    present = y[:, 2] == 1.0
    err_px = np.hypot(pred[present, 0] - y[present, 0], pred[present, 1] - y[present, 1]) * bn.INPUT_SIZE
    assert err_px.mean() < 10                                   # soft-argmax localizes (center-baseline ~24px)


def test_onnx_export_and_openvino_match(tmp_path):
    net, _ = bn.train(steps=40, batch=32)
    onnx_path = str(tmp_path / "ball.onnx")
    bn.export_onnx(net, onnx_path)
    x = bn.synth_batch(2, seed=9)[0].astype(np.float32)
    net.eval()
    torch_out = net(torch.from_numpy(x)).detach().numpy()

    ort = pytest.importorskip("onnxruntime")
    s = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
    ort_out = s.run(None, {s.get_inputs()[0].name: x})[0]
    np.testing.assert_allclose(ort_out, torch_out, atol=1e-4)

    ov = pytest.importorskip("openvino")
    core = ov.Core()
    cm = core.compile_model(core.read_model(onnx_path), "CPU")
    ov_out = np.asarray(cm(x)[cm.outputs[0]])
    np.testing.assert_allclose(ov_out, torch_out, atol=1e-3)     # OV ↔ torch agree


def test_nn_tracker_is_dropin_for_colorblob(tmp_path):
    pytest.importorskip("onnxruntime")
    net, _ = bn.train(steps=400, batch=48)
    onnx_path = str(tmp_path / "ball.onnx")
    bn.export_onnx(net, onnx_path)
    from src.ball_tracker_nn import NNBallTracker

    x, y = bn.synth_batch(1, seed=123)
    frame = (np.transpose(x[0], (1, 2, 0)) * 255).astype(np.uint8)   # HWC uint8 RGB
    obs = NNBallTracker(onnx_path).detect(frame)
    assert isinstance(obs, BallObservation)
    if y[0, 2] == 1.0:
        assert obs.found
        err = np.hypot(obs.u - y[0, 0] * bn.INPUT_SIZE, obs.v - y[0, 1] * bn.INPUT_SIZE)
        assert err < 15                                              # synthetic model; fine-tune on real frames
