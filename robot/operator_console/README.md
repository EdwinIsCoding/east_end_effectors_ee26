# EE26 Operator Console

A live web dashboard for the Franka: the two D405 camera feeds (with annotation overlays) next to
real-time robot telemetry — joint angles + velocities, gripper, TCP pose, control/teleop mode,
episode state, faults, and the stream rate. Dark "factory-floor" UI adapted from the Aura dashboard.

**Read-only and safe.** The console *only listens* to the bridge's UDP observation stream
(port 28081, CONTRACT §3); it never connects to the robot over libfranka and never sends actions.
So it does **not** count against FCI single-client — run it alongside the bridge, Desk, or teleop.

## Run

```bash
# one-time venv (system site-packages so the Desktop's pyrealsense2 is visible for live cameras):
python3 -m venv --system-site-packages ~/ee26_console_venv
~/ee26_console_venv/bin/pip install -r robot/operator_console/requirements.txt

# live: telemetry from the bridge (UDP 28081) + the two D405s
./robot/operator_console/run_console.sh

# demo with nothing connected (synthetic motion + placeholder feeds):
./robot/operator_console/run_console.sh --source synthetic

# replay a recorded obs file (raw bridge obs, one JSON per line):
./robot/operator_console/run_console.sh --source replay --replay recordings/obs.jsonl --loop
```

Then open **http://<desktop-ip>:8080** (use `--port` to change).

### Useful flags
| Flag | Meaning |
|---|---|
| `--source udp\|replay\|synthetic` | telemetry source (default `udp` = live bridge) |
| `--replay FILE` / `--loop` | JSONL of raw obs for replay; loop it |
| `--obs-port 28081` | bridge observation UDP port |
| `--synthetic-cameras` | force placeholder feeds (skip RealSense) |
| `--camera-config PATH` | data_collection.yaml for serials (default: the repo copy) |
| `--host` / `--port` | bind address (default `0.0.0.0:8080`) |

## How it fits together

```
teleop bridge --(UDP 28081 obs JSON)--> UdpObservationListener -> TelemetryHub --(SSE)--> browser
D405 wrist + external --(pyrealsense2)--> CameraManager --(annotated MJPEG)--------------> browser
```

- `telemetry.py` — parses the nested bridge obs (`robot_state`/`status`), rolling buffer + derived
  metrics (velocity, rate, episode count). Sources: UDP, JSONL replay, synthetic.
- `cameras.py` — per-camera pipeline+thread (mirrors `tools/live_camera_view.py`), serials from
  `data_collection.yaml`, annotated JPEG; synthetic fallback when no RealSense is present.
- `app.py` — Flask routes: `/`, `/api/state`, `/telemetry/stream` (SSE), `/camera/<id>.mjpg`, `/healthz`.
- `static/` + `templates/` — the dashboard (vanilla JS; canvas sparklines; EventSource).

## Tests
```bash
~/ee26_console_venv/bin/python -m pytest robot/operator_console/tests/ -q   # full (flask+cv2): 16
python3 -m pytest robot/operator_console/tests/ -q                          # core only; cv2/flask skip
```

## Notes / next
- Camera serials and the obs port are CONTRACT values — sourced from `data_collection.yaml`, not
  hardcoded. The flaky-wrist USB caveat from `live_camera_view.py` applies to live feeds.
- Possible follow-ups: ROI/keypoint annotations on the feeds, action-vs-state overlay during deploy,
  and a record button that triggers `record_robot_observations.py`.
