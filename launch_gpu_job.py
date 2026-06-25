#!/usr/bin/env python3
"""
launch_gpu_job.py — Control-center launcher for an ephemeral SageMaker GPU job.

WHY THIS EXISTS
---------------
Our AWS account blocks interactive GPU Studio apps (quota 0). So we run this
script FROM the cheap CPU workspace (ml.t3.medium). It does NOT train anything
locally. It packages `training/`, uploads it to S3, and asks SageMaker to spin
up a dedicated `ml.g6e.xlarge` (1x NVIDIA L40S, 48 GB), run our pipeline on that
GPU, copy the checkpoints back to S3, and tear the instance down automatically.

   [ml.t3.medium control plane]  --(boto3/sagemaker)-->  [ephemeral ml.g6e.xlarge]
        this script                                         runs training/main.py

HOW SAGEMAKER SHIFTS PATHS INSIDE THE CONTAINER (read this before debugging)
----------------------------------------------------------------------------
SageMaker does NOT run in our repo layout. It rewrites everything into a fixed
`/opt/ml` tree inside the Docker container. The important mounts:

  /opt/ml/code/                     <- contents of `source_dir` (our training/ dir)
                                       The entry_point (main.py) is run from HERE,
                                       so `cwd` == /opt/ml/code and relative paths
                                       like `configs/training/pi0_c1.yaml` resolve.
  /opt/ml/input/data/<channel>/     <- each S3 channel from `inputs=` is mounted
                                       here. Env var SM_CHANNEL_<NAME> points to it
                                       (e.g. SM_CHANNEL_TRAINING=/opt/ml/input/data/training).
  /opt/ml/model/                    <- SM_MODEL_DIR. ANYTHING written here is tarred
                                       to {output_path}/model.tar.gz on success. This
                                       is the ONLY guaranteed-persisted artifact path.
  /opt/ml/output/                   <- SM_OUTPUT_DATA_DIR. Misc output; also archived.
  /opt/ml/checkpoints/              <- optional live-sync to S3 mid-run (checkpoint_s3_uri).

Hyperparameters become CLI flags: SageMaker invokes roughly
    python main.py --<key> <value> ...
for every {key: value} in `hyperparameters`. There is NO way to inject a leading
positional subcommand here — see the IMPORTANT main.py note at the bottom.
"""

from __future__ import annotations

import argparse
import sys
import time

import boto3
import sagemaker
from sagemaker.pytorch import PyTorch


def build_estimator(
    *,
    instance_type: str = "ml.g6e.xlarge",
    instance_count: int = 1,
    config: str = "configs/training/pi0_c1.yaml",
    dataset_name: str = "c1_insertion",
    output_path: str | None = None,
    max_run_seconds: int = 24 * 60 * 60,
    use_spot: bool = False,
    job_name: str | None = None,
) -> PyTorch:
    """Construct the SageMaker PyTorch estimator for our Pi0 fine-tune."""

    # A SageMaker session bound to the current region/credentials of this
    # CPU workspace. We use its default bucket unless `output_path` is given.
    sm_session = sagemaker.Session()

    # The execution role. On a SageMaker workspace this resolves to the attached
    # role automatically. If you run this from somewhere WITHOUT an attached
    # SageMaker role (e.g. a laptop), get_execution_role() raises — fall back to
    # an explicit role ARN via the SAGEMAKER_ROLE env var / --role flag.
    try:
        role = sagemaker.get_execution_role()
    except Exception as exc:  # noqa: BLE001 - we want any failure to surface a clear hint
        raise RuntimeError(
            "Could not resolve a SageMaker execution role automatically. "
            "Run this from the SageMaker workspace, or pass an explicit role ARN "
            "(e.g. set SAGEMAKER_ROLE and wire it into build_estimator)."
        ) from exc

    # Default artifact bucket: s3://<default-bucket>/ee26-pi0/
    if output_path is None:
        output_path = f"s3://{sm_session.default_bucket()}/ee26-pi0/"

    # ----------------------------------------------------------------------
    # Hyperparameters → CLI flags.
    #
    # SageMaker turns this dict into `--config <v> --dataset-name <v> ...` and
    # appends it to the entry-point invocation. We keep dashes in the KEY so the
    # produced flag is exactly `--dataset-name` (SageMaker does NOT translate
    # underscores↔dashes — the key string is used verbatim with a `--` prefix).
    #
    # NOTE: `training/main.py` is normally a SUBCOMMAND CLI. We added a top-level
    # shim to it (Option A) so that when `--config` is present it delegates to
    # the train_phase flow — so these flat flags Just Work via entry_point=main.py.
    # ----------------------------------------------------------------------
    hyperparameters = {
        "config": config,             # -> --config configs/training/pi0_c1.yaml
        "dataset-name": dataset_name,  # -> --dataset-name c1_insertion
    }

    # Environment for the GPU container.
    environment = {
        # Pi0 base weights + dataset metadata may be pulled from the Hub.
        # Set to "1" instead if you pre-stage everything via an input channel.
        "HF_HUB_OFFLINE": "0",
        # lerobot's train() reads this; "wandb"/"tensorboard"/"none".
        "WANDB_MODE": "offline",
        # Make tracebacks point at our code, not buried in the launcher.
        "PYTHONUNBUFFERED": "1",
    }

    estimator = PyTorch(
        entry_point="main.py",          # run /opt/ml/code/main.py
        source_dir="training",          # upload the WHOLE training/ dir as the code bundle;
                                        #   its requirements.txt is auto pip-installed first.
        role=role,
        instance_count=instance_count,
        instance_type=instance_type,    # ml.g6e.xlarge = 1x L40S 48 GB

        # Modern PyTorch DLC. py310 matches our local 3.10/3.13-ish CPython usage
        # closely enough for the pure-python training code.
        framework_version="2.1.0",
        py_version="py310",

        hyperparameters=hyperparameters,
        environment=environment,

        # All checkpoints written to SM_MODEL_DIR (/opt/ml/model) land here as
        # model.tar.gz when the job completes. This is the safe backup the task
        # asks for. (output_path is the PARENT; SageMaker appends <job>/output/.)
        output_path=output_path,
        sagemaker_session=sm_session,

        # Stop run-away spend: cap wall-clock. g6e.xlarge bills per second.
        max_run=max_run_seconds,

        # Optional managed-spot to cut cost ~70% on a hackathon budget.
        use_spot_instances=use_spot,
        max_wait=max_run_seconds + 3600 if use_spot else None,

        base_job_name=job_name or "ee26-pi0-c1",

        # Roomier root volume for the Pi0 (~3B) weights + dataset cache + ckpts.
        volume_size=200,

        # Stream container logs back to this CPU workspace's console.
        disable_profiler=True,
    )
    return estimator


def main() -> None:
    ap = argparse.ArgumentParser(description="Launch an ephemeral SageMaker GPU training job.")
    ap.add_argument("--config", default="configs/training/pi0_c1.yaml",
                    help="Config path RELATIVE to training/ (becomes --config in the container).")
    ap.add_argument("--dataset-name", default="c1_insertion",
                    help="LeRobot dataset name (becomes --dataset-name in the container).")
    ap.add_argument("--dataset-s3-uri", default=None,
                    help="S3 URI of the exported LeRobotDataset v3. Mounted at "
                         "/opt/ml/input/data/training (SM_CHANNEL_TRAINING) if set.")
    ap.add_argument("--instance-type", default="ml.g6e.xlarge")
    ap.add_argument("--output-path", default=None,
                    help="S3 prefix for artifacts. Defaults to the session's default bucket.")
    ap.add_argument("--use-spot", action="store_true", help="Use managed spot instances.")
    ap.add_argument("--wait", action="store_true",
                    help="Block and stream logs until the job finishes.")
    args = ap.parse_args()

    print(f"[launch] sagemaker SDK v{sagemaker.__version__}  |  boto3 region "
          f"{boto3.session.Session().region_name}")

    estimator = build_estimator(
        instance_type=args.instance_type,
        config=args.config,
        dataset_name=args.dataset_name,
        output_path=args.output_path,
        use_spot=args.use_spot,
    )

    # ----------------------------------------------------------------------
    # Input channels (the DATA).
    #
    # We have NO data locally and the GPU box starts empty. If you give an S3
    # URI, SageMaker downloads it to /opt/ml/input/data/training before training
    # and exports SM_CHANNEL_TRAINING=/opt/ml/input/data/training. The training
    # code must then read the dataset from THAT path (see the data note below),
    # not from the repo-relative `lerobot_datasets/...`.
    # ----------------------------------------------------------------------
    inputs = {"training": args.dataset_s3_uri} if args.dataset_s3_uri else None
    if inputs is None:
        print("[launch] WARNING: no --dataset-s3-uri given. The job will only succeed if the "
              "dataset is fetched another way (HF Hub / baked into source_dir). Pi0 needs "
              "lerobot_datasets/<name> with observation.state[8], action[8], and the two image "
              "keys — see CONTRACT.md.")

    job_name = f"ee26-pi0-c1-{int(time.time())}"
    print(f"[launch] starting training job: {job_name}")

    # wait=True streams CloudWatch logs to this console; wait=False fires and
    # returns immediately (poll later in the SageMaker console / describe API).
    estimator.fit(inputs=inputs, job_name=job_name, wait=args.wait)

    print("[launch] submitted.")
    if args.wait:
        print(f"[launch] artifacts: {estimator.model_data}")
    else:
        print(f"[launch] track it: aws sagemaker describe-training-job "
              f"--training-job-name {job_name}")


if __name__ == "__main__":
    sys.exit(main())
