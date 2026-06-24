# Off-Robot Plan — This computer (Mac · no CUDA · no robot)

## ▶ SESSION KICKOFF (read this first, fresh session)
You are the **OFF-ROBOT** Claude Code session for the EE26 hackathon. Machine: this Mac — **no GPU,
no Franka**. You are authorized to **act directly and autonomously** through this plan (the user will
not approve each step). The Desktop session (Black workstation, RTX 5090) executes training and all
robot ops; your job is to **author, validate, and hand off code** it pulls and runs.
**Do first:** `git pull origin main`, then read `README.md`, `CONTRACT.md`, and this file. You own
`training/` and `CONTRACT.md` authoring. Push **small commits often** to `origin/main`; stay in your
owner dirs; `CONTRACT.md` is frozen (ping the user before changing a contract value).

## What runs here vs on the Desktop
- **Here (Mac, CPU):** pipeline code, configs, the `training/` pytest suite, data clean/convert logic, OpenVINO export + Pantherlake runner code, C2 tracker/PD drafts, contract validation. All CPU-testable.
- **Desktop (5090):** the actual SmolVLA/Pi0 fine-tune. You prepare the exact configs + launch scripts; the Desktop runs them (or you SSH in to launch — coordinate timing vs robot use).

## O0 — Pipeline stand-up (no GPU)
- [ ] Set up `training/` env; run its pytest suite (101 tests) green to confirm the pipeline is intact.
- [ ] Validate dataset spec vs `CONTRACT.md §1`: state[8], action[8], keys `observation.images.top` (wrist) + `observation.images.third_person_d405` (external).
- [ ] Author SmolVLA baseline training config pointing at those exact keys + a fixed `task` prompt. Commit.

## O1 — Make training turn-key for the Desktop
- [ ] Write a single launch script the Desktop runs after `git pull` (paths, hyperparams, dataset location per `CONTRACT.md`).
- [ ] Document the **cu128 PyTorch / Blackwell** requirement in the script's README so the Desktop sets it up once.
- [ ] Prepare a tiny throwaway-dataset dry-run config so the Desktop can smoke-test the clean→convert→train loop before real demos.

## O2 — SmolVLA loop (once real demos land in the repo/shared path)
- [ ] Iterate clean/convert configs; tune training config from Desktop eval feedback (camera placement, episode count, prompt phrasing).
- [ ] Define the policy artifact + `policy_card.md` template per `CONTRACT.md §4`.

## O3 — OpenVINO + Pi0 (Intel bonus / ceiling)
- [x] Export path documented (`policy.export(..., backend="openvino")`, `InferenceModel.load`) — `src/inference/README.md`.
- [x] **Pantherlake inference runner** `src/inference/openvino_runner.py`: emits UDP 28082 per `CONTRACT §2`, lazy HW deps, `--mock`/`--self-test`. Self-test + 5 unit tests green. **Verify obs/action shapes on the Intel box.**
- [x] Pi0 single-5090 fine-tune config `configs/training/pi0_c1.yaml` (freeze-vision-encoder + expert-only + grad-checkpoint + bf16; no LoRA flag in CLI). Validated by test_training_configs.

## O4 — C2 ball-balance (drafted, hardware-free) ✅
- [x] `challenge2/src`: tracker (numpy colour-blob / cv2 Hough), PD controller, plate-tilt→pose, loop+CommandSink. 5 tests incl. a PD-stabilizes sim. Desktop wires CommandSink to the bridge Cartesian-pose path.

## O4 — C2 logic drafts (hand to Desktop)
- [ ] Draft the ball tracker (colour-blob / Hough) + PD/LQR controller as pure Python; test on a synthetic/recorded clip. Desktop wires it to the arm. See `challenge2/`.

## Reuse notes
- `training/` = SmolVLA-Testing `main`. Other branches in `~/Downloads/ee26_refs/SmolVLA-Testing` (feat/qwen-thomas, labeler, qwen-prompting, xav-qwen) — cherry-pick annotation/labeler tooling.
- Keep every robot-facing assumption in `CONTRACT.md`, never hard-coded here.
