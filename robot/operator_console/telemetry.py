"""Telemetry ingest for the operator console.

Parses the bridge's UDP observation JSON (CONTRACT §3 / observation_pub.cpp), keeps a rolling
buffer, and derives the things the dashboard shows: per-joint angle + velocity series, gripper,
TCP pose, control/teleop mode, episode state, faults, and the stream rate.

Three sources feed the same TelemetryHub:
  * UdpObservationListener — the real bridge stream (port 28081).
  * replay_jsonl()         — a recorded file of raw obs lines (offline dev, no robot).
  * synthetic_observations() — generated motion so the console runs with nothing connected.
"""
from __future__ import annotations

import json
import math
import socket
import threading
import time
from collections import deque
from typing import Any, Iterable, Optional

NUM_JOINTS = 7
DEFAULT_OBS_PORT = 28081
DEFAULT_BUFFER = 600          # ~20 s at 30 Hz
DEFAULT_SERIES_WINDOW = 150   # points sent to the UI per sparkline
STALE_AFTER_S = 0.75          # no obs for this long -> not "live"

FAULT_KEYS = ("packet_timeout", "jump_rejected", "workspace_clamped", "robot_not_ready",
              "control_exception", "ik_rejected", "gripper_fault")


def _as_floats(seq: Any, n: int) -> list[float]:
    try:
        out = [float(v) for v in seq]
    except (TypeError, ValueError):
        return [0.0] * n
    if len(out) < n:
        out += [0.0] * (n - len(out))
    return out[:n]


def parse_observation(obs: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw bridge obs (nested under robot_state/status) into a flat display record.

    Tolerates a flat dict and missing fields so replay/synthetic/partial packets don't crash the UI.
    """
    rs = obs.get("robot_state", obs)
    status = obs.get("status", obs)
    return {
        "timestamp_ns": int(obs.get("timestamp_ns", 0) or 0),
        "q": _as_floats(rs.get("q", []), NUM_JOINTS),
        "dq": _as_floats(rs.get("dq", []), NUM_JOINTS),
        "q_cmd": _as_floats(rs.get("q_cmd", []), NUM_JOINTS),
        "gripper_width": float(rs.get("gripper_width", 0.0) or 0.0),
        "gripper_state": str(rs.get("gripper_state", "")),
        "tcp_xyz": _as_floats(rs.get("tcp_position_xyz", []), 3),
        "tcp_quat": _as_floats(rs.get("tcp_orientation_xyzw", []), 4),
        "control_mode": str(status.get("control_mode", "")),
        "teleop_state": str(status.get("teleop_state", "")),
        "teleop_active": bool(status.get("teleop_active", False)),
        "target_fresh": bool(status.get("target_fresh", False)),
        "episode_start": bool(status.get("episode_start", False)),
        "episode_end": bool(status.get("episode_end", False)),
        "target_manipulability": float(status.get("target_manipulability", 0.0) or 0.0),
        "faults": {k: bool((status.get("fault_flags", status) or {}).get(k, False)) for k in FAULT_KEYS},
    }


class TelemetryHub:
    """Thread-safe rolling telemetry store with derived metrics for the dashboard."""

    def __init__(self, buffer: int = DEFAULT_BUFFER, series_window: int = DEFAULT_SERIES_WINDOW,
                 source: str = "udp") -> None:
        self._lock = threading.Lock()
        self._records: deque[dict[str, Any]] = deque(maxlen=buffer)
        self._arrivals: deque[float] = deque(maxlen=120)  # wall-clock recv times for rate calc
        self._series_window = series_window
        self.source = source
        self.total = 0
        self.episode_count = 0
        self.episode_active = False
        self._prev_start = False
        self._last_monotonic = 0.0

    def ingest(self, raw_obs: dict[str, Any], now: Optional[float] = None) -> None:
        rec = parse_observation(raw_obs)
        now = time.monotonic() if now is None else now
        with self._lock:
            # Velocity: prefer the bridge's dq; otherwise finite-difference q.
            if not any(rec["dq"]) and self._records:
                prev = self._records[-1]
                dt_ns = rec["timestamp_ns"] - prev["timestamp_ns"]
                if dt_ns > 0:
                    dt = dt_ns / 1e9
                    rec["dq"] = [(a - b) / dt for a, b in zip(rec["q"], prev["q"])]
            self._records.append(rec)
            self._arrivals.append(now)
            self._last_monotonic = now
            self.total += 1
            # Episode count on rising edge of episode_start (matches record_robot_observations).
            if rec["episode_start"] and not self._prev_start:
                self.episode_count += 1
                self.episode_active = True
            self._prev_start = rec["episode_start"]
            if rec["episode_end"]:
                self.episode_active = False

    def _rate_hz(self) -> float:
        if len(self._arrivals) < 2:
            return 0.0
        span = self._arrivals[-1] - self._arrivals[0]
        return (len(self._arrivals) - 1) / span if span > 0 else 0.0

    def overlay_info(self) -> dict[str, Any]:
        """Small dict for per-frame camera overlays (no series copy — cheap at video rates)."""
        with self._lock:
            if not self._records:
                return {"episode": "no data", "control_mode": "", "gripper": 0.0}
            r = self._records[-1]
            episode = f"REC {self.episode_count:02d}" if self.episode_active else "idle"
            return {"episode": episode, "control_mode": r["control_mode"],
                    "gripper": r["gripper_width"]}

    def snapshot(self, now: Optional[float] = None) -> dict[str, Any]:
        now = time.monotonic() if now is None else now
        with self._lock:
            if not self._records:
                return {"source": self.source, "connected": False, "status": "waiting",
                        "rate_hz": 0.0, "samples": 0, "latest": None,
                        "series": {"q": [[] for _ in range(NUM_JOINTS)],
                                   "dq": [[] for _ in range(NUM_JOINTS)], "gripper": []},
                        "episode": {"count": 0, "active": False}}
            window = list(self._records)[-self._series_window:]
            latest = self._records[-1]
            age = now - self._last_monotonic
            connected = age < STALE_AFTER_S
            any_fault = any(latest["faults"].values())
            return {
                "source": self.source,
                "connected": connected,
                "status": "live" if connected else "stale",
                "age_s": round(age, 3),
                "rate_hz": round(self._rate_hz(), 1),
                "samples": self.total,
                "latest": latest,
                "any_fault": any_fault,
                "series": {
                    "q": [[r["q"][j] for r in window] for j in range(NUM_JOINTS)],
                    "dq": [[r["dq"][j] for r in window] for j in range(NUM_JOINTS)],
                    "gripper": [r["gripper_width"] for r in window],
                },
                "episode": {"count": self.episode_count, "active": self.episode_active},
            }


class UdpObservationListener:
    """Background UDP listener that feeds raw bridge obs into a TelemetryHub (read-only)."""

    def __init__(self, hub: TelemetryHub, bind_ip: str = "0.0.0.0", port: int = DEFAULT_OBS_PORT) -> None:
        self._hub = hub
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((bind_ip, port))
        self._sock.settimeout(0.2)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                payload, _ = self._sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                self._hub.ingest(json.loads(payload.decode("utf-8")))
            except (ValueError, UnicodeDecodeError):
                continue

    def close(self) -> None:
        self._stop.set()
        self._sock.close()


def iter_jsonl_observations(path: str) -> Iterable[dict[str, Any]]:
    """Yield raw obs dicts from a JSONL file (one obs per line); skip blank/garbage lines."""
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def synthetic_observations(count: Optional[int] = None, fps: float = 30.0, seed: int = 0):
    """Generate plausible nested obs (sinusoidal joints, toggling gripper, periodic episodes).

    Lets the console run with nothing connected. If count is None, generates forever.
    """
    q_home = [0.0, -math.pi / 4, 0.0, -3 * math.pi / 4, 0.0, math.pi / 2, math.pi / 4]
    i = 0
    while count is None or i < count:
        t = i / fps
        q = [h + 0.25 * math.sin(0.6 * t + j) for j, h in enumerate(q_home)]
        dq = [0.25 * 0.6 * math.cos(0.6 * t + j) for j in range(NUM_JOINTS)]
        grip = 0.04 * (0.5 + 0.5 * math.sin(0.2 * t))
        phase = int(t) % 10
        yield {
            "timestamp_ns": int(t * 1e9),
            "robot_state": {
                "q": q, "dq": dq, "q_cmd": q, "gripper_width": grip,
                "gripper_state": "closed" if grip < 0.02 else "open",
                "tcp_position_xyz": [0.4 + 0.05 * math.sin(0.6 * t), 0.1 * math.cos(0.6 * t), 0.45],
                "tcp_orientation_xyzw": [0.0, 1.0, 0.0, 0.0],
            },
            "status": {
                "control_mode": "JOINT_IMPEDANCE", "teleop_state": "ACTIVE",
                "teleop_active": True, "target_fresh": True,
                "episode_start": phase == 1, "episode_end": phase == 8,
                "target_manipulability": 0.08 + 0.01 * math.sin(t),
                "fault_flags": {k: False for k in FAULT_KEYS},
            },
        }
        i += 1


def run_source(hub: TelemetryHub, observations: Iterable[dict[str, Any]], fps: float = 30.0,
               loop: bool = False, stop: Optional[threading.Event] = None) -> threading.Thread:
    """Drive a TelemetryHub from any iterable of raw obs at fps in a daemon thread."""
    period = 1.0 / fps if fps > 0 else 0.0

    def _pump():
        while True:
            for obs in observations() if callable(observations) else observations:
                if stop is not None and stop.is_set():
                    return
                hub.ingest(obs)
                if period:
                    time.sleep(period)
            if not loop or not callable(observations):
                return

    thread = threading.Thread(target=_pump, daemon=True)
    thread.start()
    return thread
