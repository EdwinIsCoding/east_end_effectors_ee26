# Interface Contract (FROZEN — change only by mutual agreement)

This is the *only* thing the two workstreams share. Code to this and you can develop fully
independently. Any change here = ping the other person before committing.

`robot/` (Desktop) **produces** datasets + consumes policies. `training/` (off-robot) **consumes**
datasets + produces policies. The handoffs below are the entire boundary.

---

## 1. Dataset contract (LeRobot v3)
Desktop records → off-robot trains. Must match exactly.

| Field | Spec |
|---|---|
| `observation.state` | shape **[8]** = 7 joint angles (rad) + 1 gripper width (m) |
| `action` | shape **[8]** = 7 **commanded** joint positions `q_cmd` (rad; converter falls back to `backfilled_q_cmd`) + 1 gripper command (0=open,1=close) |
| `observation.images.top` | **wrist** D405, serial `130322270179`, RGB 1280×720 @30fps |
| `observation.images.third_person_d405` | **external** D405, serial `130322273529`, RGB 1280×720 @30fps |
| `task` | natural-language prompt string (VLAs are very phrasing-sensitive — keep it fixed per task) |
| fps | 30 |

Canonical camera→key mapping lives in `robot/franka_xr_teleop/configs/data_collection.yaml`.
Image keys must be byte-identical across recording, training, and deploy configs.

**Producing the image keys (critical):** the converter maps `--primary-camera` → `observation.images.top`
and every other camera → `observation.images.<camera_name>`. So convert with
**`--primary-camera wrist_d405`** (camera_name from `data_collection.yaml`): wrist → `observation.images.top`,
external `third_person_d405` → `observation.images.third_person_d405`. Wrong `--primary-camera` ⇒ silent
key mismatch between training and deploy. Verified by `training/tests/test_data_converter_contract.py`.

## 2. Action wire — policy runner → bridge (UDP, port **28082**, JSON)
```json
{"timestamp_ns": int, "sequence_id": int, "enabled": true,
 "action_space": "joint_position_absolute", "joint_positions_rad": [7 floats],
 "gripper_command": 0.0-1.0, "operator_request_id": int?, "request_rehome": bool?}
```
Bridge bypasses IK for this path: it servos directly to `joint_positions_rad`. So the **policy must
output absolute joint positions** (matches `action`=[8] above). Source of truth:
`robot/franka_xr_teleop/cpp/teleop_bridge/policy_action_source.cpp` + `tools/run_vla_policy.py::_send_action`.

## 3. Observation wire — bridge → recorder (UDP, port **28081**, JSON lines)
Bridge publishes robot observations at ~50 Hz. Fields the training side relies on:
`timestamp_ns`, `q` [7], `gripper_width`, `episode_start`, `episode_end`.
Full struct: `robot/franka_xr_teleop/cpp/teleop_bridge/common_types.h::RobotObservation`
(serialized in `observation_pub.cpp`). Recorder: `tools/record_robot_observations.py`.

## 4. Policy artifact handoff (off-robot → Desktop)
Off-robot delivers a directory containing:
- a LeRobot-loadable checkpoint **and/or** an OpenVINO export (`policy.export(..., backend="openvino")`)
- a `policy_card.md`: which image keys it consumes, input resolution, normalization stats,
  state/action dims (must be [8]/[8]), and the exact `task` prompt string it was trained on.
Desktop loads it via `tools/run_vla_policy.py` (Torch) or the OpenVINO runner on Pantherlake.

## 5. Canonical configs (shared, edited by whoever owns the value)
`robot/franka_xr_teleop/configs/`: `robot.yaml` (ip `192.168.1.11`, libfranka `>=0.9.1,<0.10.0`),
`teleop.yaml`, `safety.yaml`, `data_collection.yaml` (camera serials + image keys).
Treat camera serials, ports, and the image-key names as contract values.
