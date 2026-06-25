#!/usr/bin/env python3
"""
launch_gpu_check.py — fire a tiny, cheap ephemeral GPU smoke-test job.

Run this FROM the ml.t3.medium workspace BEFORE the real training job, to confirm:
  (a) your account actually has training-job quota for ml.g6e.xlarge (separate
      from the blocked interactive/Studio GPU quota), and
  (b) CUDA / torch / bf16 work inside the DLC container.

It spins up one ml.g6e.xlarge, runs sagemaker_gpu_check/gpu_check.py, streams the
logs back here, then tears the instance down. Costs ~a few minutes of g6e time.

    python launch_gpu_check.py
"""

from __future__ import annotations

import time

import boto3
import sagemaker
from sagemaker.pytorch import PyTorch


def main() -> None:
    sm_session = sagemaker.Session()
    try:
        role = sagemaker.get_execution_role()
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Could not resolve a SageMaker execution role. Run from the SageMaker "
            "workspace, or wire in an explicit role ARN."
        ) from exc

    print(f"[check] sagemaker v{sagemaker.__version__} | region "
          f"{boto3.session.Session().region_name} | role {role.split('/')[-1]}")

    estimator = PyTorch(
        entry_point="gpu_check.py",
        # Tiny dedicated dir with NO requirements.txt -> the check skips the heavy
        # lerobot install and runs in seconds on the DLC's prebuilt torch.
        source_dir="sagemaker_gpu_check",
        role=role,
        instance_count=1,
        instance_type="ml.g6e.xlarge",     # 1x L40S, 48 GB
        framework_version="2.1.0",
        py_version="py310",
        sagemaker_session=sm_session,
        base_job_name="ee26-gpu-check",
        max_run=20 * 60,                    # hard cap: 20 min, this should take <5
        volume_size=50,
        disable_profiler=True,
    )

    job_name = f"ee26-gpu-check-{int(time.time())}"
    print(f"[check] launching {job_name} on ml.g6e.xlarge ...")
    # wait=True streams the container's stdout (nvidia-smi, CUDA results) to this
    # console. If the job ends "Completed" -> GPU works. "Failed" with our exit 2
    # -> the instance had no usable GPU. A ResourceLimitExceeded at submit time
    # -> you have no g6e.xlarge TRAINING quota (request an increase in Service Quotas).
    estimator.fit(job_name=job_name, wait=True)
    print(f"[check] done. proof artifact (gpu_check_ok.txt) at: {estimator.model_data}")


if __name__ == "__main__":
    main()
