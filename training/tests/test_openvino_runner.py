"""Wire-format + safety tests for the OpenVINO Pantherlake runner (CONTRACT §2)."""
import numpy as np
import pytest

from src.inference import openvino_runner as ovr


def test_action_message_matches_contract():
    joints = np.zeros(7)
    msg = ovr.build_action_message(3, joints, gripper=1.0)
    assert msg["action_space"] == "joint_position_absolute"
    assert msg["sequence_id"] == 3
    assert msg["enabled"] is True
    assert len(msg["joint_positions_rad"]) == 7
    assert msg["gripper_command"] == 1.0
    assert "timestamp_ns" in msg
    # operator_request_id only present when > 0
    assert "operator_request_id" not in msg
    assert "operator_request_id" in ovr.build_action_message(0, joints, 0.0, operator_request_id=5)


def test_clamp_respects_panda_limits_and_binarizes_gripper():
    over = np.full(8, 10.0)  # way past joint limits, gripper 10 -> close
    joints, gripper = ovr.clamp_action(over)
    assert np.all(joints <= ovr.PANDA_JOINT_UPPER_LIMITS_RAD - ovr.JOINT_LIMIT_MARGIN_RAD + 1e-9)
    assert np.all(joints >= ovr.PANDA_JOINT_LOWER_LIMITS_RAD + ovr.JOINT_LIMIT_MARGIN_RAD - 1e-9)
    assert gripper == 1.0
    assert ovr.clamp_action(np.concatenate([np.zeros(7), [0.4]]))[1] == 0.0  # < 0.5 -> open


def test_clamp_rejects_bad_shape_and_nan():
    with pytest.raises(ValueError):
        ovr.clamp_action(np.zeros(7))
    with pytest.raises(ValueError):
        ovr.clamp_action(np.array([np.nan, *np.zeros(7)]))


def test_state_vector_is_q_plus_gripper_width():
    state = ovr.state_vector({"q": [0, 1, 2, 3, 4, 5, 6], "gripper_width": 0.076})
    np.testing.assert_allclose(state, np.array([0, 1, 2, 3, 4, 5, 6, 0.076], dtype=np.float32))
    assert state.shape == (ovr.POLICY_STATE_DIM,)


def test_mock_policy_holds_current_joints():
    obs = {ovr.OBS_STATE_KEY: np.array([0.1] * 7 + [0.04]), ovr.TASK_KEY: "x"}
    action = ovr.MockPolicy().select_action(obs)
    assert action.shape == (8,)
    np.testing.assert_allclose(action[:7], [0.1] * 7)


def test_fallback_serials_match_contract():
    # CONTRACT §1: wrist 130322271109 -> top, external 130322273529 -> third_person_d405.
    assert ovr.WRIST_SERIAL == "130322271109"
    assert ovr.EXTERNAL_SERIAL == "130322273529"


def test_load_camera_serials_reads_config(tmp_path):
    cfg = tmp_path / "data_collection.yaml"
    cfg.write_text(
        "cameras:\n"
        "  - id: third_person\n"
        "    enabled: true\n"
        "    backend: realsense\n"
        "    obs_key: observation.images.third_person_d405\n"
        "    serial: \"111111111111\"\n"
        "  - id: wrist\n"
        "    enabled: true\n"
        "    backend: realsense\n"
        "    obs_key: observation.images.top\n"
        "    serial: \"222222222222\"\n"
        "  - id: zed_wrist\n"            # non-realsense + disabled: must be ignored
        "    enabled: false\n"
        "    backend: zed\n"
        "    obs_key: observation.images.top\n"
        "    serial: \"999999999999\"\n"
    )
    pytest.importorskip("yaml")
    serials = ovr.load_camera_serials(str(cfg))
    assert serials[ovr.WRIST_IMAGE_KEY] == "222222222222"
    assert serials[ovr.EXTERNAL_IMAGE_KEY] == "111111111111"


def test_load_camera_serials_falls_back_when_missing():
    serials = ovr.load_camera_serials("/nonexistent/data_collection.yaml")
    assert serials[ovr.WRIST_IMAGE_KEY] == ovr.WRIST_SERIAL
    assert serials[ovr.EXTERNAL_IMAGE_KEY] == ovr.EXTERNAL_SERIAL


def test_stage_profiler_reports_stages_and_hz():
    prof = ovr.StageProfiler()
    for _ in range(5):
        prof.add("infer", 4.0)
        prof.add("image", 1.0)
    out = prof.summary(sent=5, wall_s=1.0)
    assert "infer" in out and "image" in out
    assert "5.0 Hz effective" in out  # 5 steps / 1.0 s


def test_run_loop_profile_does_not_change_step_count(capsys):
    import socket
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    sender = ovr.ActionSender("127.0.0.1", rx.getsockname()[1])
    hold = {"q": [0.0] * 7, "gripper_width": 0.04}
    img = {ovr.WRIST_IMAGE_KEY: None, ovr.EXTERNAL_IMAGE_KEY: None}
    sent = ovr.run_loop(lambda: hold, lambda: img, ovr.MockPolicy(), sender,
                        "x", rate_hz=500.0, jitter_std=0.0, max_steps=4, profile=True)
    sender.close()
    rx.close()
    assert sent == 4
    assert "[profile] per-stage latency" in capsys.readouterr().out
