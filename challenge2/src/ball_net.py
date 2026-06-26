"""Tiny CNN ball detector for C2 — trained on synthetic data, exported to ONNX/OpenVINO.

Purpose: run ball tracking as NN inference on the Intel Pantherlake (OpenVINO) → earns the Intel bonus
while feeding the same PD loop (drop-in for ColorBlobTracker via `ball_tracker_nn.NNBallTracker`).

Output is [x, y, present] in [0,1]: (x,y) = ball centre normalized to the frame, present = confidence.
Trained on domain-randomized synthetic frames (random plate colour, ball colour/size, lighting, clutter)
so it transfers without real labels; **fine-tune on a few real frames for best accuracy**.
"""
from __future__ import annotations

import numpy as np

INPUT_SIZE = 96


def build_net():
    """Tiny conv backbone + soft-argmax heatmap head → directly regresses (x,y); GAP head → presence.

    Soft-argmax preserves spatial location (a global-average head collapses to the image centre and
    can't localize). Output [x, y, present] in [0,1]. torch is imported lazily so inference-only
    consumers (OpenVINO/onnxruntime) don't need torch.
    """
    import torch
    import torch.nn as nn

    class BallNet(nn.Module):
        def __init__(self, hw: int = 12):
            super().__init__()
            self.feat = nn.Sequential(
                nn.Conv2d(3, 16, 3, 2, 1), nn.ReLU(),    # 96->48
                nn.Conv2d(16, 32, 3, 2, 1), nn.ReLU(),   # 48->24
                nn.Conv2d(32, 32, 3, 2, 1), nn.ReLU(),   # 24->12
            )
            self.heat = nn.Conv2d(32, 1, 1)
            self.presence = nn.Linear(32, 1)
            grid = (torch.arange(hw).float() + 0.5) / hw
            self.register_buffer("grid", grid.view(1, 1, hw))

        def forward(self, x):
            f = self.feat(x)                                  # [N,32,H,W]
            n, _, h, w = f.shape
            p = torch.softmax(self.heat(f).view(n, -1), dim=1).view(n, 1, h, w)
            xc = (p.sum(dim=2) * self.grid).sum(dim=2)        # [N,1] soft-argmax x
            yc = (p.sum(dim=3) * self.grid).sum(dim=2)        # [N,1] soft-argmax y
            present = torch.sigmoid(self.presence(f.mean(dim=(2, 3))))
            return torch.cat([xc, yc, present], dim=1)

    return BallNet()


def _rng(seed):
    return np.random.default_rng(seed)


def synth_batch(n: int, size: int = INPUT_SIZE, seed: int = 0, neg_frac: float = 0.15):
    """Domain-randomized (image[n,3,H,W] float32 [0,1], target[n,3]=[x,y,present])."""
    import cv2  # lazy
    imgs = np.zeros((n, size, size, 3), np.float32)
    tgts = np.zeros((n, 3), np.float32)
    g = _rng(seed)
    def col(arr):
        return tuple(int(c) for c in arr)
    for i in range(n):
        # darker workspace bg + a mid-tone plate, so the bright ball stays the salient blob (learnable)
        img = np.full((size, size, 3), col(g.integers(0, 110, 3)), np.uint8)
        pc = (int(g.integers(size * 0.3, size * 0.7)), int(g.integers(size * 0.3, size * 0.7)))
        pr = int(g.integers(size * 0.3, size * 0.5))
        cv2.circle(img, pc, pr, col(g.integers(50, 170, 3)), -1)
        has_ball = g.random() > neg_frac
        if has_ball:
            bx = int(g.integers(pc[0] - pr * 0.7, pc[0] + pr * 0.7))
            by = int(g.integers(pc[1] - pr * 0.7, pc[1] + pr * 0.7))
            br = int(g.integers(size * 0.05, size * 0.11))
            w_val = int(g.integers(200, 256))
            ball_col = (w_val, w_val, w_val) if g.random() > 0.5 else (255, int(g.integers(120, 180)), 0)
            cv2.circle(img, (bx, by), br, ball_col, -1)
            cv2.circle(img, (int(bx - br * 0.3), int(by - br * 0.3)), max(1, br // 3), (255, 255, 255), -1)
            tgts[i] = [bx / size, by / size, 1.0]
        img = img.astype(np.float32) + g.normal(0, 8, img.shape).astype(np.float32)  # noise
        imgs[i] = np.clip(img, 0, 255) / 255.0
    return np.transpose(imgs, (0, 3, 1, 2)), tgts


def train(steps: int = 400, batch: int = 64, lr: float = 2e-3, seed: int = 0):
    """Train on synthetic data; returns (net, final_loss). CPU-fast for this tiny net."""
    import torch
    net = build_net()
    opt = torch.optim.Adam(net.parameters(), lr=lr)
    mse, bce = torch.nn.MSELoss(), torch.nn.BCELoss()
    last = float("inf")
    for s in range(steps):
        x, y = synth_batch(batch, seed=seed + s)
        xb, yb = torch.from_numpy(x), torch.from_numpy(y)
        pred = net(xb)
        present = yb[:, 2:3]
        loss = bce(pred[:, 2:3], present) + 3.0 * mse(pred[:, :2] * present, yb[:, :2] * present)
        opt.zero_grad(); loss.backward(); opt.step()
        last = float(loss.item())
    return net, last


def export_onnx(net, path: str, size: int = INPUT_SIZE):
    import torch
    net.eval()
    dummy = torch.zeros(1, 3, size, size)
    torch.onnx.export(net, dummy, path, input_names=["image"], output_names=["xyp"],
                      dynamic_axes={"image": {0: "batch"}, "xyp": {0: "batch"}}, opset_version=17,
                      dynamo=False)   # legacy exporter: no onnxscript dependency
    return path
