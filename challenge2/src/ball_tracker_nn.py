"""NN ball tracker — OpenVINO (Pantherlake / Intel bonus) or onnxruntime, drop-in for ColorBlobTracker.

Same `detect(frame_rgb) -> BallObservation` interface as `tracker.ColorBlobTracker`, so it slots into
`loop.run(...)` unchanged. Loads the exported model (`ball_net.onnx`); prefers OpenVINO (auto-detects
the device on Pantherlake), falls back to onnxruntime.
"""
from __future__ import annotations

import numpy as np

from .ball_net import INPUT_SIZE
from .tracker import BallObservation


class NNBallTracker:
    def __init__(self, model_path: str, backend: str = "auto", device: str = "AUTO",
                 present_thresh: float = 0.5, input_size: int = INPUT_SIZE):
        self.present_thresh = present_thresh
        self.input_size = input_size
        self.backend = backend
        self._infer = self._load(model_path, backend, device)

    def _load(self, model_path, backend, device):
        if backend in ("auto", "openvino"):
            try:
                import openvino as ov  # lazy
                core = ov.Core()
                model = core.read_model(model_path)
                compiled = core.compile_model(model, device if device != "AUTO" else "AUTO")
                out = compiled.outputs[0]
                self.backend = "openvino"
                return lambda x: compiled(x)[out]
            except Exception:
                if backend == "openvino":
                    raise
        import onnxruntime as ort  # fallback
        sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        iname = sess.get_inputs()[0].name
        self.backend = "onnxruntime"
        return lambda x: sess.run(None, {iname: x})[0]

    def _preprocess(self, frame_rgb: np.ndarray) -> np.ndarray:
        import cv2  # lazy
        img = cv2.resize(frame_rgb, (self.input_size, self.input_size)).astype(np.float32) / 255.0
        return np.transpose(img, (2, 0, 1))[None].astype(np.float32)

    def detect(self, frame_rgb: np.ndarray) -> BallObservation:
        h, w = frame_rgb.shape[:2]
        xyp = np.asarray(self._infer(self._preprocess(frame_rgb))).reshape(-1)
        x, y, present = float(xyp[0]), float(xyp[1]), float(xyp[2])
        if present < self.present_thresh:
            return BallObservation(found=False)
        return BallObservation(found=True, u=x * w, v=y * h, radius_px=0.0)
