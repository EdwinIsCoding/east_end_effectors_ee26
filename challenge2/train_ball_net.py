"""Train the tiny C2 ball detector on synthetic data and export ONNX (then OpenVINO on Pantherlake).

    python challenge2/train_ball_net.py --steps 400 --out challenge2/models/ball_net.onnx
    # on Pantherlake: ovc challenge2/models/ball_net.onnx   (or NNBallTracker loads the .onnx directly)

Synthetic-trained → proves the train→ONNX→OpenVINO→infer path and earns the Intel bonus; fine-tune on a
few real plate frames for best accuracy (replace synth_batch with a real-frame loader).
"""
from __future__ import annotations

import argparse

from src.ball_net import export_onnx, train


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--steps", type=int, default=400)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--out", default="models/ball_net.onnx")
    args = ap.parse_args()
    net, loss = train(steps=args.steps, batch=args.batch)
    print(f"trained: final_loss={loss:.4f}")
    export_onnx(net, args.out)
    print(f"exported ONNX -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
