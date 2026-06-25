"""Tests for the operator-console telemetry core (no Flask / no hardware)."""
from robot.operator_console import telemetry as tel


NESTED = {
    "timestamp_ns": 1000,
    "robot_state": {
        "q": [0, 1, 2, 3, 4, 5, 6], "dq": [0.1] * 7, "q_cmd": [0] * 7,
        "gripper_width": 0.03, "gripper_state": "open",
        "tcp_position_xyz": [0.4, 0.0, 0.45], "tcp_orientation_xyzw": [0, 1, 0, 0],
    },
    "status": {
        "control_mode": "JOINT_IMPEDANCE", "teleop_state": "ACTIVE", "teleop_active": True,
        "target_fresh": True, "episode_start": False, "episode_end": False,
        "target_manipulability": 0.08,
        "fault_flags": {k: False for k in tel.FAULT_KEYS},
    },
}


def test_parse_nested_obs():
    rec = tel.parse_observation(NESTED)
    assert rec["q"] == [0, 1, 2, 3, 4, 5, 6]
    assert rec["dq"] == [0.1] * 7
    assert rec["gripper_width"] == 0.03
    assert rec["control_mode"] == "JOINT_IMPEDANCE"
    assert rec["teleop_active"] is True
    assert rec["faults"]["packet_timeout"] is False
    assert len(rec["tcp_quat"]) == 4


def test_parse_flat_fallback_and_missing_fields():
    rec = tel.parse_observation({"q": [1] * 7})
    assert rec["q"] == [1.0] * 7
    assert rec["gripper_width"] == 0.0
    assert rec["dq"] == [0.0] * 7  # missing -> zero-filled, not a crash


def test_ring_buffer_caps_but_total_counts():
    hub = tel.TelemetryHub(buffer=10)
    for _ in range(25):
        hub.ingest(NESTED)
    snap = hub.snapshot()
    assert snap["samples"] == 25
    assert len(snap["series"]["q"][0]) <= 10


def test_episode_count_on_rising_edge_only():
    hub = tel.TelemetryHub()
    seq = [False, True, True, False, True, False]  # two rising edges
    for v in seq:
        obs = {**NESTED, "status": {**NESTED["status"], "episode_start": v}}
        hub.ingest(obs)
    assert hub.episode_count == 2


def test_velocity_finite_diff_when_dq_absent():
    hub = tel.TelemetryHub()
    a = {"timestamp_ns": 0, "robot_state": {"q": [0] * 7, "dq": [0] * 7}}
    b = {"timestamp_ns": 1_000_000_000, "robot_state": {"q": [0.1] * 7, "dq": [0] * 7}}  # +0.1 over 1 s
    hub.ingest(a)
    hub.ingest(b)
    snap = hub.snapshot()
    assert snap["latest"]["dq"][0] == 0.1


def test_rate_hz_from_arrival_times():
    hub = tel.TelemetryHub()
    for i in range(11):
        hub.ingest(NESTED, now=i * 0.1)  # 10 Hz
    assert abs(hub.snapshot(now=1.0)["rate_hz"] - 10.0) < 0.5


def test_connected_goes_stale():
    hub = tel.TelemetryHub()
    hub.ingest(NESTED, now=0.0)
    assert hub.snapshot(now=0.1)["connected"] is True
    assert hub.snapshot(now=5.0)["connected"] is False


def test_empty_snapshot_is_safe():
    snap = tel.TelemetryHub().snapshot()
    assert snap["samples"] == 0 and snap["latest"] is None
    assert len(snap["series"]["q"]) == tel.NUM_JOINTS


def test_synthetic_observations_are_parseable():
    obs = list(tel.synthetic_observations(count=5))
    assert len(obs) == 5
    rec = tel.parse_observation(obs[0])
    assert len(rec["q"]) == 7 and "episode_start" in rec


def test_iter_jsonl_skips_blank_and_garbage(tmp_path):
    p = tmp_path / "obs.jsonl"
    p.write_text('{"timestamp_ns": 1}\n\nnot json\n{"timestamp_ns": 2}\n')
    rows = list(tel.iter_jsonl_observations(str(p)))
    assert [r["timestamp_ns"] for r in rows] == [1, 2]
