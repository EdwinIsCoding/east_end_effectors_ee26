# Policy Card — c1_insertion SmolVLA

Conforms to `CONTRACT.md §4`. This is the handoff spec for deploying this checkpoint
with `robot/franka_xr_teleop/tools/run_vla_policy.py` (Torch) or the OpenVINO runner.

## Model
- **Architecture:** SmolVLA (fine-tuned from `lerobot/smolvla_base`)
- **Training data:** `c1_insertion_merged` — 10 teleop sessions (f/l/r/lc/rc ×001/002),
  **100 episodes / 85,448 frames**, 30 fps, robot_type `franka`. Built by merging the
  per-session LeRobot v3 datasets in `robot/franka_xr_teleop/recordings_cc/lerobot/`
  (`training/src/data/merge.py`). The `session_lerobot_01` validation recording is excluded.
- **Train config:** batch 8, 20k steps, bf16/AMP, cuda, seed 1000.
- **Checkpoints:** `checkpoints/<step>/pretrained_model/` (`last` symlinks the newest).

## Inputs the policy consumes (must match the deploy obs exactly)
| Key | Type | Shape | Source (deploy) |
|---|---|---|---|
| `observation.images.top` | image | `[3,720,1280]` | **wrist D405** (`data_collection.yaml` id `wrist`, `obs_key: observation.images.top`) |
| `observation.images.third_person_d405` | image | `[3,720,1280]` | **external D405** (id `third_person`) |
| `observation.state` | float32 | `[8]` | 7 joint angles (rad) + gripper width (m) |

Images are internally resized to **512×512 with padding** (`resize_imgs_with_padding`).
`n_obs_steps=1`. Both cameras are required (`empty_cameras=0`).

## Output
| Key | Shape | Meaning |
|---|---|---|
| `action` | `[8]` | 7 absolute joint positions `q_cmd` (rad) + gripper command |

`chunk_size = n_action_steps = 50` (supports Real-Time Chunking at deploy).

## Normalization
`VISUAL: IDENTITY`, `STATE: MEAN_STD`, `ACTION: MEAN_STD` (stats baked into the
checkpoint's `policy_preprocessor` / `policy_postprocessor`). For reference, the dataset stats:
- `observation.state` mean ≈ `[-0.141, 0.447, 0.033, -1.935, -0.042, 2.743, 0.627, 0.038]`
- `action`           mean ≈ `[-0.141, 0.446, 0.033, -1.933, -0.042, 2.743, 0.627, 0.544]`

## Task prompt (VLA is phrasing-sensitive — deploy with ONE canonical phrasing)
Trained on 4 paraphrases (one per episode, cycled across sessions):
1. `Put the white peg into the white socket.`
2. `Fit the white cylindrical block into the white hole.`
3. **`Insert the white cylindrical block into the white socket.`  ← canonical deploy prompt**
4. `Place the white cylindrical block into the matching white hole.`

Pass the canonical string via `--task`. Validate variants 1/2/4 at deploy if #3 underperforms.
> Note: TASK_C1.md's old placeholder `"Insert the peg into the hole."` was **not** in the
> training vocabulary (no "white", different nouns) — do not deploy with it.

## Wire format (CONTRACT §2/§3)
- Observations in ← UDP **28081** (`observation.state` from bridge `robot_state.{q,gripper_width}`).
- Actions out → UDP **28082**, JSON `action_space: "joint_position_absolute"`,
  `joint_positions_rad[7]` + `gripper_command∈[0,1]`.

## Verification done
`tools/test_deploy_paths.py` against this checkpoint **PASSES** sync + RTC paths on cuda:
action shape `(8,)`, finite, joint ranges sane; RTC chunk `(50,8)`, measured inference
delay ≈ 146 ms (5 steps). No robot/cameras needed for that test.
