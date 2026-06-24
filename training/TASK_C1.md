# C1 Insertion — turn-key training pipeline (authored off-robot, run on Desktop)

End-to-end recipe to turn recorded peg-in-hole demos into a deployable SmolVLA policy.
Conforms to `../CONTRACT.md`. Run on the Desktop (has the lerobot env + 5090). Commands use the repo's
lerobot project per `training/CLAUDE.md`.

## Prompt strategy (matches the team's proven approach + the report)
- **Train on DIVERSE paraphrases** (one per episode) — `main.py annotate` generates them from
  `src/cli/generate_annotations.py`. Vocab is now peg-in-hole (verbs Insert/Push/Slide/Fit/Seat/Place/Lower
  × objects {peg, cylinder, block, …} × receptacles {hole, slot, socket, …}). Behavioural/linguistic
  diversity > volume on small datasets (Industrial report).
- **Deploy with ONE canonical phrasing.** Single-task VLAs are phrasing-sensitive, so pick a canonical
  prompt and validate 2–3 variants at deploy. Canonical: **`Insert the peg into the hole.`**
- ⚠️ Before collecting, refine the vocab in `generate_annotations.py` with the **actual** printed shape's
  colour/name (e.g. "the blue square peg", "the square hole").

## Constants (from CONTRACT)
- Dataset name `c1_insertion` · primary camera `wrist_d405` · state[8]=q+gripper_width · action[8]=q_cmd+gripper
- Image keys produced: `observation.images.top` (wrist) + `observation.images.third_person_d405` (external)

## Pipeline (Desktop)
```bash
# 0) record on the Desktop → raw_datasets/c1_insertion  (plans/PLAN_DESKTOP.md D2)

# 1) clean (drop static frames, sync cameras)
uv --project ../lerobot run python main.py clean c1_insertion

# 2) annotate — diverse per-episode prompts into cleaned_datasets/c1_insertion/annotations.jsonl
uv --project ../lerobot run python main.py annotate c1_insertion

# 3) convert → LeRobot v3 with the contract image keys (primary-camera is contract-critical)
uv --project ../lerobot run python main.py convert c1_insertion --primary-camera wrist_d405

# 4) train SmolVLA baseline (config configs/training/smolvla_baseline.yaml; base = 20k steps, bf16, cuda)
uv --project ../lerobot run python main.py train --model-type smolvla \
    --dataset-root lerobot_datasets/c1_insertion --steps 20000
#    output → outputs/c1_insertion_smolvla
```

## Smoke test BEFORE real demos (de-risk the loop)
Run steps 1–4 on a 2-episode throwaway recording with `--steps 50`. Confirm convert emits exactly
`observation.images.top` + `observation.images.third_person_d405`, state/action dims are [8]/[8], and a
loss curve starts. Then scale.

## Handoff to Desktop deploy (CONTRACT §4)
Deliver `outputs/c1_insertion_smolvla/` + `policy_card.md`: the two image keys, input resolution,
dims [8]/[8], normalization stats, and the canonical deploy prompt. Desktop deploys via
`robot/franka_xr_teleop/tools/run_vla_policy.py` (apply output jitter for mm precision).

## Ceiling (later)
- Pi0: `--model-type pi0` (LoRA) once SmolVLA data quality is proven.
- OpenVINO export for the Pantherlake Intel bonus (PLAN_OFFROBOT O3).
