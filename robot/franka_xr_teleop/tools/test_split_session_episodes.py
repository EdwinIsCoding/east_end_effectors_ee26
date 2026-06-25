#!/usr/bin/env python3
"""Tests for episode segmentation (no robot, no Quest, no cameras needed).

Covers:
- pairing A/B markers into episode windows and carving robot + camera streams,
- per-episode mp4 clip extraction when OpenCV is available,
- the full record_robot_observations.py path: synthetic UDP observations with
  status.episode_start/episode_end -> episode_events.jsonl -> split.

Run: python tools/test_split_session_episodes.py   (or via pytest)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(__file__))
import split_session_episodes as ss  # noqa: E402

ROBOT_T0 = 1_000_000_000
MS = 1_000_000
FRAME_NS = 33_333_333  # ~30 fps


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as dst:
        for row in rows:
            dst.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def build_synthetic_session(session: Path, *, with_video: bool = False) -> Dict[str, Any]:
    """Create a session with 600ms of robot data, two cameras, and two episodes."""
    robot_rows = [
        {"timestamp_ns": ROBOT_T0 + i * MS, "robot_state": {"q": [float(i)] * 7, "gripper_width": 0.04}}
        for i in range(600)
    ]
    write_jsonl(session / "robot.jsonl", robot_rows)

    # Episode 0: [+100ms, +200ms], Episode 1: [+350ms, +450ms].
    ep0 = (ROBOT_T0 + 100 * MS, ROBOT_T0 + 200 * MS)
    ep1 = (ROBOT_T0 + 350 * MS, ROBOT_T0 + 450 * MS)
    events = [
        {"event": "episode_start", "robot_timestamp_ns": ep0[0], "packet_index": 100, "receive_host_time_ns": 1},
        {"event": "episode_end", "robot_timestamp_ns": ep0[1], "packet_index": 200, "receive_host_time_ns": 2},
        {"event": "episode_start", "robot_timestamp_ns": ep1[0], "packet_index": 350, "receive_host_time_ns": 3},
        {"event": "episode_end", "robot_timestamp_ns": ep1[1], "packet_index": 450, "receive_host_time_ns": 4},
    ]
    write_jsonl(session / "episode_events.jsonl", events)

    n_frames = 18  # 600ms at 30fps
    for cam in ("third_person_d405", "wrist_d405"):
        cam_dir = session / "cameras" / cam
        frames = [
            {
                "camera": cam,
                "frame_index": i,
                "host_timestamp_ns": ROBOT_T0 + i * FRAME_NS,
                "rgb_video": "rgb.mp4",
                "rgb_video_frame": i,
                "width": 32,
                "height": 24,
            }
            for i in range(n_frames)
        ]
        write_jsonl(cam_dir / "frames.jsonl", frames)
        (cam_dir / "metadata.json").write_text(
            json.dumps({"camera_name": cam, "rgb_video": "rgb.mp4"}, indent=2) + "\n", encoding="utf-8"
        )
        if with_video:
            _write_video(cam_dir / "rgb.mp4", n_frames, 32, 24)

    return {"ep0": ep0, "ep1": ep1, "n_frames": n_frames}


def _write_video(path: Path, n_frames: int, w: int, h: int) -> bool:
    cv2 = ss.try_import_cv2()
    if cv2 is None:
        return False
    import numpy as np  # local import; only needed for the video path

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, 30.0, (w, h))
    for i in range(n_frames):
        frame = np.full((h, w, 3), i % 256, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return path.exists() and path.stat().st_size > 0


def test_pairing_and_windows():
    with tempfile.TemporaryDirectory() as tmp:
        session = Path(tmp) / "session_test"
        meta = build_synthetic_session(session, with_video=False)
        result = ss.split_session(session, extract_clips=False, warn=lambda m: None)

        assert result["episode_count"] == 2, result["episode_count"]
        ep0_dir = session / "episodes" / "episode_000"
        ep1_dir = session / "episodes" / "episode_001"
        assert ep0_dir.is_dir() and ep1_dir.is_dir()

        # Robot rows: inclusive window of 1ms samples from +100ms..+200ms -> 101 rows.
        ep0_robot = ss.read_jsonl(ep0_dir / "robot.jsonl")
        assert len(ep0_robot) == 101, len(ep0_robot)
        lo = meta["ep0"][0]
        hi = meta["ep0"][1]
        assert all(lo <= r["timestamp_ns"] <= hi for r in ep0_robot)

        # Camera frames windowed for both cameras.
        for cam in ("third_person_d405", "wrist_d405"):
            frames = ss.read_jsonl(ep0_dir / "cameras" / cam / "frames.jsonl")
            assert frames, f"no frames sliced for {cam}"
            for f in frames:
                assert lo <= f["host_timestamp_ns"] <= hi
        print(f"[ok] pairing_and_windows: 2 episodes, ep0 robot={len(ep0_robot)}")


def test_index_and_meta_written():
    with tempfile.TemporaryDirectory() as tmp:
        session = Path(tmp) / "session_meta"
        build_synthetic_session(session, with_video=False)
        ss.split_session(session, extract_clips=False, warn=lambda m: None)

        index = json.loads((session / "episodes" / "episodes_index.json").read_text())
        assert index["episode_count"] == 2
        assert set(index["cameras"]) == {"third_person_d405", "wrist_d405"}

        em = json.loads((session / "episodes" / "episode_000" / "episode_meta.json").read_text())
        assert em["episode"] == "episode_000"
        assert em["end_ns"] > em["start_ns"]
        assert em["robot_samples"] == 101
        assert set(em["cameras"]) == {"third_person_d405", "wrist_d405"}
        print("[ok] index_and_meta_written")


def test_open_end_handling():
    with tempfile.TemporaryDirectory() as tmp:
        session = Path(tmp) / "session_open"
        write_jsonl(
            session / "robot.jsonl",
            [{"timestamp_ns": ROBOT_T0 + i * MS} for i in range(300)],
        )
        # A pressed, never B.
        write_jsonl(
            session / "episode_events.jsonl",
            [{"event": "episode_start", "robot_timestamp_ns": ROBOT_T0 + 50 * MS, "packet_index": 50}],
        )
        dropped = ss.split_session(session, extract_clips=False, allow_open_end=False, warn=lambda m: None)
        assert dropped["episode_count"] == 0, "open episode must be dropped without --allow-open-end"

        kept = ss.split_session(session, extract_clips=False, allow_open_end=True, warn=lambda m: None)
        assert kept["episode_count"] == 1, "open episode must be kept with --allow-open-end"
        em = json.loads((session / "episodes" / "episode_000" / "episode_meta.json").read_text())
        assert em["synthetic_end"] is True
        assert em["end_ns"] == ROBOT_T0 + 299 * MS  # last robot sample
        print("[ok] open_end_handling")


def test_clip_extraction_when_cv2_present():
    cv2 = ss.try_import_cv2()
    if cv2 is None:
        print("[skip] clip_extraction: OpenCV not installed")
        return
    with tempfile.TemporaryDirectory() as tmp:
        session = Path(tmp) / "session_clip"
        build_synthetic_session(session, with_video=True)
        ss.split_session(session, extract_clips=True, warn=lambda m: None)

        cam_dir = session / "episodes" / "episode_000" / "cameras" / "third_person_d405"
        clip = cam_dir / "rgb.mp4"
        assert clip.exists() and clip.stat().st_size > 0, "expected a non-empty per-episode clip"

        frames = ss.read_jsonl(cam_dir / "frames.jsonl")
        # rgb_video_frame must be re-indexed 0..n-1 and source index preserved.
        assert [f["rgb_video_frame"] for f in frames] == list(range(len(frames)))
        assert all("source_rgb_video_frame" in f for f in frames)
        cap = cv2.VideoCapture(str(clip))
        written = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        assert written == len(frames), f"clip has {written} frames, frames.jsonl has {len(frames)}"
        print(f"[ok] clip_extraction: {written} frames clipped")


def test_episode_discard_drops_span():
    """A,B,A,DISCARD,A,B -> 2 episodes; the discarded middle span is dropped."""
    evs = [
        {"event": "episode_start", "robot_timestamp_ns": 1000, "packet_index": 1},
        {"event": "episode_end", "robot_timestamp_ns": 2000, "packet_index": 2},
        {"event": "episode_start", "robot_timestamp_ns": 3000, "packet_index": 3},
        {"event": "episode_discard", "robot_timestamp_ns": 3500, "packet_index": 4},
        {"event": "episode_start", "robot_timestamp_ns": 4000, "packet_index": 5},
        {"event": "episode_end", "robot_timestamp_ns": 5000, "packet_index": 6},
    ]
    eps = ss.pair_episodes(evs, fallback_end_ns=None, allow_open_end=False, warn=lambda m: None)
    wins = [(e["start"]["robot_timestamp_ns"], e["end_ns"]) for e in eps]
    assert wins == [(1000, 2000), (4000, 5000)], wins
    print("[ok] episode_discard_drops_span:", wins)


def test_udp_recorder_to_split_end_to_end():
    """Simulate the bridge: UDP observations with episode_start/end -> events -> split."""
    recorder = Path(__file__).resolve().parent / "record_robot_observations.py"
    with tempfile.TemporaryDirectory() as tmp:
        session = Path(tmp) / "session_udp"
        session.mkdir(parents=True)
        robot_out = session / "robot.jsonl"
        events_out = session / "episode_events.jsonl"

        # Bind a free UDP port for the recorder.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()

        # Build the observation stream: A pressed at packet 10, B at packet 20.
        n_packets = 30
        packets: List[Dict[str, Any]] = []
        for i in range(n_packets):
            packets.append(
                {
                    "timestamp_ns": ROBOT_T0 + i * 10 * MS,
                    "robot_state": {"q": [float(i)] * 7, "gripper_width": 0.04},
                    "status": {
                        "teleop_state": "TELEOP_ACTIVE",
                        "control_mode": "teleop",
                        "episode_start": i == 10,  # rising edge -> one episode_start event
                        "episode_end": i == 20,    # rising edge -> one episode_end event
                    },
                }
            )

        proc = subprocess.Popen(
            [
                sys.executable,
                str(recorder),
                "--bind-ip",
                "127.0.0.1",
                "--port",
                str(port),
                "--output",
                str(robot_out),
                "--episode-events-output",
                str(events_out),
                "--max-packets",
                str(n_packets),
                "--print-hz",
                "0",
                "--flush-every",
                "1",
            ],
        )
        try:
            time.sleep(1.0)  # let the recorder bind before sending
            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            for pkt in packets:
                tx.sendto(json.dumps(pkt).encode("utf-8"), ("127.0.0.1", port))
                time.sleep(0.01)
            tx.close()
            proc.wait(timeout=15)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)

        assert robot_out.exists(), "recorder wrote no robot.jsonl"
        events = ss.read_jsonl(events_out)
        names = [e["event"] for e in events]
        assert names == ["episode_start", "episode_end"], names

        result = ss.split_session(session, extract_clips=False, warn=lambda m: None)
        assert result["episode_count"] == 1, result
        em = json.loads((session / "episodes" / "episode_000" / "episode_meta.json").read_text())
        assert em["start_ns"] == ROBOT_T0 + 10 * 10 * MS
        assert em["end_ns"] == ROBOT_T0 + 20 * 10 * MS
        # robot samples 10..20 inclusive -> 11 rows.
        assert em["robot_samples"] == 11, em["robot_samples"]
        print(f"[ok] udp_recorder_to_split: events={names} robot_samples={em['robot_samples']}")


def _run_all() -> int:
    tests = [
        test_pairing_and_windows,
        test_index_and_meta_written,
        test_open_end_handling,
        test_episode_discard_drops_span,
        test_clip_extraction_when_cv2_present,
        test_udp_recorder_to_split_end_to_end,
    ]
    failures = 0
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            failures += 1
            import traceback

            print(f"[FAIL] {fn.__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
