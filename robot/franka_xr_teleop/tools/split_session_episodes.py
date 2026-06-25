#!/usr/bin/env python3
"""Split a continuous data-collection session into per-episode folders.

A session recorded by ``record_data_collection_session.py`` is one continuous
stream: ``robot.jsonl`` (joints + state at ~1 kHz), one ``cameras/<name>/rgb.mp4``
+ ``frames.jsonl`` per camera, and ``episode_events.jsonl`` with the A/B markers
(``episode_start`` on the right-controller A button, ``episode_end`` on B, which
also rehomes the arm).

This tool reads those markers and carves the session into self-contained
episode folders, each holding the joints and BOTH camera clips for one A->B span::

    <session>/episodes/
      episode_000/
        robot.jsonl              # robot rows with start_ns <= timestamp_ns <= end_ns
        episode_meta.json        # window, counts, source session/markers
        cameras/
          third_person_d405/
            rgb.mp4              # clipped to the window (when OpenCV is available)
            frames.jsonl         # frames in the window, rgb_video_frame re-indexed to the clip
            metadata.json        # copied from the source camera, with a split note
          wrist_d405/
            ...
      episodes_index.json        # summary of every episode

Clock note: ``robot.jsonl`` ``timestamp_ns`` and each camera ``frames.jsonl``
``host_timestamp_ns`` are stamped on the SAME host monotonic clock (see
DATA_COLLECTION.md), so the episode window in robot time bounds the camera frames
directly -- no cross-clock alignment needed here.

Runs standalone on any past session, and is called automatically at the end of a
recording by the launcher (unless ``--no-auto-split``).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as src:
        for line in src:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as dst:
        for row in rows:
            dst.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def event_sort_key(event: Dict[str, Any]) -> Tuple[int, int]:
    # packet_index is monotonic within a session; fall back to receive time.
    packet_index = event.get("packet_index")
    if isinstance(packet_index, int):
        return (0, packet_index)
    return (1, int(event.get("receive_host_time_ns", 0) or 0))


def pair_episodes(
    events: List[Dict[str, Any]],
    *,
    fallback_end_ns: Optional[int],
    allow_open_end: bool,
    warn: Any,
) -> List[Dict[str, Any]]:
    """Pair episode_start/episode_end markers into [start_ns, end_ns] windows."""
    ordered = sorted(events, key=event_sort_key)
    episodes: List[Dict[str, Any]] = []
    open_start: Optional[Dict[str, Any]] = None

    def start_ns(ev: Dict[str, Any]) -> Optional[int]:
        ts = ev.get("robot_timestamp_ns")
        return ts if isinstance(ts, int) else None

    for ev in ordered:
        name = ev.get("event")
        if name == "episode_start":
            if open_start is not None:
                prev_ns = start_ns(open_start)
                this_ns = start_ns(ev)
                if prev_ns is not None and this_ns is not None and this_ns > prev_ns:
                    warn(
                        "episode_start with no episode_end before it; closing the "
                        f"previous episode at the new start ({this_ns})."
                    )
                    episodes.append({"start": open_start, "end_ns": this_ns, "synthetic_end": True})
                else:
                    warn("dropping an episode_start that has no usable timestamp / ordering.")
            open_start = ev
        elif name == "episode_end":
            if open_start is None:
                warn("episode_end with no preceding episode_start; skipping it.")
                continue
            end = start_ns(ev)
            if end is None:
                warn("episode_end has no robot_timestamp_ns; skipping it.")
                continue
            episodes.append({"start": open_start, "end_ns": end, "end": ev, "synthetic_end": False})
            open_start = None
        elif name == "episode_discard":
            # Operator discarded the current episode (left-controller X): drop the
            # open start->discard span entirely; it never becomes an episode.
            if open_start is None:
                warn("episode_discard with no open episode; ignoring it.")
                continue
            warn("episode_discard — dropping the current (unfinished) episode span.")
            open_start = None

    if open_start is not None:
        if allow_open_end and fallback_end_ns is not None:
            warn(f"trailing episode_start with no episode_end; closing at last robot sample ({fallback_end_ns}).")
            episodes.append({"start": open_start, "end_ns": fallback_end_ns, "synthetic_end": True})
        else:
            warn("trailing episode_start with no episode_end; dropping it (use --allow-open-end to keep).")

    # Keep only episodes with a valid, non-empty window.
    valid: List[Dict[str, Any]] = []
    for ep in episodes:
        s = ep["start"].get("robot_timestamp_ns")
        e = ep["end_ns"]
        if not isinstance(s, int) or not isinstance(e, int) or e <= s:
            warn(f"dropping episode with invalid window start={s} end={e}.")
            continue
        valid.append(ep)
    return valid


def discover_cameras(session_dir: Path) -> List[Path]:
    cameras_root = session_dir / "cameras"
    if not cameras_root.is_dir():
        return []
    return sorted(d for d in cameras_root.iterdir() if d.is_dir() and (d / "frames.jsonl").exists())


def slice_robot(rows: List[Dict[str, Any]], start_ns: int, end_ns: int) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        ts = row.get("timestamp_ns")
        if isinstance(ts, int) and start_ns <= ts <= end_ns:
            out.append(row)
    return out


def slice_camera_frames(rows: List[Dict[str, Any]], start_ns: int, end_ns: int) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        ts = row.get("host_timestamp_ns")
        if isinstance(ts, int) and start_ns <= ts <= end_ns:
            out.append(row)
    return out


def try_import_cv2() -> Any:
    try:
        import cv2  # type: ignore

        return cv2
    except Exception:
        return None


def extract_clip(
    cv2: Any,
    src_video: Path,
    dst_video: Path,
    frame_indices: List[int],
    warn: Any,
) -> Optional[int]:
    """Copy the given source-frame indices into a new mp4. Returns frames written."""
    if not frame_indices:
        return 0
    if not src_video.exists():
        warn(f"source video missing, cannot extract clip: {src_video}")
        return None
    cap = cv2.VideoCapture(str(src_video))
    if not cap.isOpened():
        warn(f"could not open source video: {src_video}")
        return None
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if fps <= 0.0:
        fps = 30.0
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    wanted = set(frame_indices)
    lo, hi = min(frame_indices), max(frame_indices)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    dst_video.parent.mkdir(parents=True, exist_ok=True)
    writer = None
    written = 0
    idx = 0
    try:
        # Read sequentially up to hi; grab() is cheap for frames we skip.
        while idx <= hi:
            ok, frame = cap.read()
            if not ok:
                break
            if idx in wanted:
                if writer is None:
                    h, w = frame.shape[0], frame.shape[1]
                    writer = cv2.VideoWriter(str(dst_video), fourcc, fps, (w or width, h or height))
                writer.write(frame)
                written += 1
            idx += 1
    finally:
        cap.release()
        if writer is not None:
            writer.release()
    if written != len(wanted):
        warn(f"clip {dst_video.name}: wrote {written} of {len(wanted)} requested frames.")
    return written


def split_session(
    session_dir: Path,
    *,
    episode_events: Optional[Path] = None,
    robot_jsonl: Optional[Path] = None,
    output_subdir: str = "episodes",
    extract_clips: bool = True,
    min_duration_s: float = 0.0,
    allow_open_end: bool = False,
    warn: Any = None,
) -> Dict[str, Any]:
    """Carve a session into per-episode folders. Returns a summary dict."""
    if warn is None:
        def warn(msg: str) -> None:  # noqa: ANN001
            print(f"[split] WARN: {msg}", file=sys.stderr, flush=True)

    session_dir = session_dir.resolve()
    events_path = episode_events or (session_dir / "episode_events.jsonl")
    robot_path = robot_jsonl or (session_dir / "robot.jsonl")

    if not events_path.exists():
        warn(f"no episode events file at {events_path}; nothing to split.")
        return {"session_dir": str(session_dir), "episodes": [], "reason": "no_episode_events"}

    events = read_jsonl(events_path)
    robot_rows = read_jsonl(robot_path) if robot_path.exists() else []
    if not robot_path.exists():
        warn(f"no robot.jsonl at {robot_path}; episodes will have empty joint data.")

    robot_ts = [r["timestamp_ns"] for r in robot_rows if isinstance(r.get("timestamp_ns"), int)]
    fallback_end_ns = max(robot_ts) if robot_ts else None

    episodes = pair_episodes(
        events,
        fallback_end_ns=fallback_end_ns,
        allow_open_end=allow_open_end,
        warn=warn,
    )

    cameras = discover_cameras(session_dir)
    camera_frames: Dict[str, List[Dict[str, Any]]] = {
        cam.name: read_jsonl(cam / "frames.jsonl") for cam in cameras
    }

    cv2 = try_import_cv2() if extract_clips else None
    if extract_clips and cv2 is None:
        warn(
            "OpenCV (cv2) not available; writing per-episode frames.jsonl that "
            "reference the parent session rgb.mp4 instead of clipping. Re-run with "
            "cv2 installed (e.g. source ~/ee26_cam_venv) for self-contained clips."
        )

    out_root = session_dir / output_subdir
    out_root.mkdir(parents=True, exist_ok=True)

    summary_episodes: List[Dict[str, Any]] = []
    written_index = 0
    for ep in episodes:
        start_ns = ep["start"]["robot_timestamp_ns"]
        end_ns = ep["end_ns"]
        duration_s = (end_ns - start_ns) / 1e9
        if duration_s < min_duration_s:
            warn(f"skipping episode {start_ns}->{end_ns} shorter than --min-duration-s ({duration_s:.3f}s).")
            continue

        name = f"episode_{written_index:03d}"
        ep_dir = out_root / name
        ep_cameras: Dict[str, Any] = {}

        ep_robot = slice_robot(robot_rows, start_ns, end_ns)
        write_jsonl(ep_dir / "robot.jsonl", ep_robot)

        for cam in cameras:
            frames = slice_camera_frames(camera_frames[cam.name], start_ns, end_ns)
            cam_out = ep_dir / "cameras" / cam.name
            cam_out.mkdir(parents=True, exist_ok=True)

            src_indices = [
                f["rgb_video_frame"] for f in frames if isinstance(f.get("rgb_video_frame"), int)
            ]
            clip_written: Optional[int] = None
            clipped = False
            if cv2 is not None and src_indices:
                clip_written = extract_clip(
                    cv2, cam / "rgb.mp4", cam_out / "rgb.mp4", src_indices, warn
                )
                clipped = clip_written is not None

            # Re-index frames.jsonl. When clipped, rgb_video_frame becomes the
            # clip-local index; otherwise it keeps pointing at the parent video.
            reindexed: List[Dict[str, Any]] = []
            for clip_idx, f in enumerate(frames):
                row = dict(f)
                if clipped:
                    row["source_rgb_video_frame"] = f.get("rgb_video_frame")
                    row["rgb_video"] = "rgb.mp4"
                    row["rgb_video_frame"] = clip_idx
                else:
                    # Point at the parent session video so frames stay resolvable.
                    row["rgb_video"] = str(Path("..") / ".." / ".." / ".." / "cameras" / cam.name / "rgb.mp4")
                reindexed.append(row)
            write_jsonl(cam_out / "frames.jsonl", reindexed)

            src_meta = cam / "metadata.json"
            if src_meta.exists():
                try:
                    meta = json.loads(src_meta.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    meta = {}
            else:
                meta = {}
            meta["split"] = {
                "source_session": session_dir.name,
                "episode": name,
                "window_ns": [start_ns, end_ns],
                "clipped": clipped,
                "frames": len(frames),
            }
            (cam_out / "metadata.json").write_text(
                json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )

            ep_cameras[cam.name] = {
                "frames": len(frames),
                "clipped": clipped,
                "clip_frames_written": clip_written,
            }

        meta = {
            "episode": name,
            "source_session": session_dir.name,
            "source_session_dir": str(session_dir),
            "start_ns": start_ns,
            "end_ns": end_ns,
            "duration_s": duration_s,
            "synthetic_end": ep.get("synthetic_end", False),
            "robot_samples": len(ep_robot),
            "start_marker": ep["start"],
            "end_marker": ep.get("end"),
            "cameras": ep_cameras,
        }
        (ep_dir / "episode_meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        summary_episodes.append(meta)
        print(
            f"[split] {name}: {duration_s:.2f}s robot={len(ep_robot)} "
            + " ".join(f"{n}={c['frames']}" for n, c in ep_cameras.items()),
            flush=True,
        )
        written_index += 1

    index = {
        "session_dir": str(session_dir),
        "episode_events": str(events_path),
        "robot_jsonl": str(robot_path),
        "cameras": [c.name for c in cameras],
        "episode_count": len(summary_episodes),
        "episodes": summary_episodes,
    }
    (out_root / "episodes_index.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"[split] wrote {len(summary_episodes)} episode(s) under {out_root}", flush=True)
    return index


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("session_dir", type=Path, help="Session folder produced by record_data_collection_session.py.")
    parser.add_argument("--episode-events", type=Path, default=None, help="Override path to episode_events.jsonl.")
    parser.add_argument("--robot-jsonl", type=Path, default=None, help="Override path to robot.jsonl.")
    parser.add_argument("--output-subdir", default="episodes", help="Subfolder under the session for episodes (default: episodes).")
    parser.add_argument("--no-extract-clips", action="store_true", help="Do not clip per-episode mp4s; reference the parent video instead.")
    parser.add_argument("--min-duration-s", type=float, default=0.0, help="Skip episodes shorter than this (default: 0).")
    parser.add_argument("--allow-open-end", action="store_true", help="Keep a trailing episode_start with no episode_end, closing at the last robot sample.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    if not args.session_dir.exists():
        print(f"session dir does not exist: {args.session_dir}", file=sys.stderr)
        return 2
    result = split_session(
        args.session_dir,
        episode_events=args.episode_events,
        robot_jsonl=args.robot_jsonl,
        output_subdir=args.output_subdir,
        extract_clips=not args.no_extract_clips,
        min_duration_s=args.min_duration_s,
        allow_open_end=args.allow_open_end,
    )
    return 0 if result.get("episode_count", 0) >= 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
