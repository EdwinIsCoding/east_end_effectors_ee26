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
