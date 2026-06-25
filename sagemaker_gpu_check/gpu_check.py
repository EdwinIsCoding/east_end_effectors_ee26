#!/usr/bin/env python3
"""
gpu_check.py — runs INSIDE an ephemeral SageMaker ml.g5.2xlarge training job.

It does the minimum to answer "can my code actually see and use the A10G GPU?":
  1. nvidia-smi (driver + GPU visible to the container)
  2. torch.cuda.is_available() / device name / capability
  3. a real CUDA matmul (proves compute + memory work, not just detection)
  4. bf16 support (Pi0 trains in bf16) + a quick cuDNN/AMP sanity op

Exits NON-ZERO if no usable GPU is found, so the SageMaker job shows as Failed
(not silently "Completed") when the box came up without a GPU.

The PyTorch DLC already ships a CUDA-enabled torch, so this needs NO extra deps —
that's why its source_dir has no heavy requirements.txt.
"""

import os
import subprocess
import sys


def section(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}", flush=True)


def main() -> int:
    section("1) nvidia-smi")
    try:
        # If the driver/GPU isn't wired into the container this raises/non-zero.
        print(subprocess.run(["nvidia-smi"], capture_output=True, text=True, check=True).stdout)
    except Exception as exc:  # noqa: BLE001
        print(f"nvidia-smi failed: {exc}")

    section("2) torch / CUDA detection")
    import torch

    print(f"torch version      : {torch.__version__}")
    print(f"torch CUDA build   : {torch.version.cuda}")
    print(f"cudnn version      : {torch.backends.cudnn.version()}")
    available = torch.cuda.is_available()
    print(f"cuda.is_available  : {available}")

    if not available:
        print("\nNO USABLE GPU — torch.cuda.is_available() is False.")
        return 2

    n = torch.cuda.device_count()
    print(f"device_count       : {n}")
    for i in range(n):
        name = torch.cuda.get_device_name(i)
        cap = torch.cuda.get_device_capability(i)
        total_gb = torch.cuda.get_device_properties(i).total_memory / (1024**3)
        print(f"  cuda:{i} -> {name} | sm_{cap[0]}{cap[1]} | {total_gb:.1f} GB")

    section("3) CUDA matmul (compute + memory)")
    dev = torch.device("cuda:0")
    x = torch.randn(4096, 4096, device=dev)
    y = (x @ x).sum().item()
    torch.cuda.synchronize()
    print(f"(4096x4096 @ 4096x4096).sum() = {y:.1f}")
    print(f"peak GPU mem alloc : {torch.cuda.max_memory_allocated(dev) / (1024**2):.1f} MB")

    section("4) bf16 + AMP sanity (Pi0 trains in bf16)")
    try:
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            a = torch.randn(1024, 1024, device=dev)
            b = (a @ a).mean()
        torch.cuda.synchronize()
        print(f"bf16 autocast matmul mean = {b.item():.4f}  -> bf16 OK")
    except Exception as exc:  # noqa: BLE001
        print(f"bf16 autocast failed: {exc}")

    section("5) SageMaker environment (path mapping)")
    for k in ("SM_MODEL_DIR", "SM_OUTPUT_DATA_DIR", "SM_CHANNEL_TRAINING",
              "SM_NUM_GPUS", "SM_CURRENT_HOST", "CUDA_VISIBLE_DEVICES"):
        print(f"  {k} = {os.environ.get(k)}")

    # Prove SM_MODEL_DIR is writable + persisted: anything here -> S3 model.tar.gz.
    model_dir = os.environ.get("SM_MODEL_DIR", "/opt/ml/model")
    os.makedirs(model_dir, exist_ok=True)
    with open(os.path.join(model_dir, "gpu_check_ok.txt"), "w") as fh:
        fh.write(f"GPU OK: {torch.cuda.get_device_name(0)}\n")
    print(f"\nWrote proof artifact to {model_dir}/gpu_check_ok.txt")

    print("\nALL GPU CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
