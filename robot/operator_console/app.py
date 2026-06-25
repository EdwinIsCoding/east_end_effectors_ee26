"""Operator console web app — live D405 feeds + robot telemetry. Read-only (never commands the robot).

    python -m robot.operator_console.app                       # UDP 28081 + cameras (auto-synthetic if none)
    python -m robot.operator_console.app --source synthetic     # no robot, no cameras: demo everything
    python -m robot.operator_console.app --source replay --replay recordings/obs.jsonl

Then open http://<host>:8080. FCI single-client is irrelevant here — the console only *listens* to the
bridge's observation stream; it does not connect to the robot or send actions.
"""
from __future__ import annotations

import argparse
import json
import threading
import time

from flask import Flask, Response, jsonify, render_template

from . import cameras as cameras_mod
from . import telemetry as tel


def create_app(hub: tel.TelemetryHub, camera_manager: cameras_mod.CameraManager,
               camera_fps: float = 20.0) -> Flask:
    app = Flask(__name__)

    @app.route("/")
    def index():
        return render_template("console.html", cameras=camera_manager.cameras)

    @app.route("/healthz")
    def healthz():
        return jsonify(ok=True, source=hub.source, samples=hub.total)

    @app.route("/api/state")
    def api_state():
        snap = hub.snapshot()
        snap["cameras"] = [{"id": c["id"], "label": c["label"], "obs_key": c.get("obs_key", "")}
                           for c in camera_manager.cameras]
        return jsonify(snap)

    @app.route("/telemetry/stream")
    def telemetry_stream():
        def gen():
            while True:
                yield f"data: {json.dumps(hub.snapshot())}\n\n"
                time.sleep(0.12)  # ~8 Hz UI refresh
        return Response(gen(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.route("/camera/<camera_id>.mjpg")
    def camera_mjpg(camera_id):
        def gen():
            period = 1.0 / camera_fps if camera_fps > 0 else 0.05
            while True:
                overlay = hub.overlay_info()
                overlay["stamp"] = time.strftime("%H:%M:%S")
                jpg = camera_manager.jpeg(camera_id, overlay=overlay)
                if jpg is not None:
                    yield (b"--frame\r\nContent-Type: image/jpeg\r\n"
                           b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                time.sleep(period)
        return Response(gen(), mimetype="multipart/x-mixed-replace; boundary=frame")

    return app


def build_runtime(args) -> tuple[tel.TelemetryHub, cameras_mod.CameraManager, threading.Event]:
    stop = threading.Event()
    hub = tel.TelemetryHub(source=args.source)

    if args.source == "udp":
        listener = tel.UdpObservationListener(hub, bind_ip=args.bind_ip, port=args.obs_port)
        listener.start()
    elif args.source == "synthetic":
        tel.run_source(hub, tel.synthetic_observations, fps=args.fps, loop=True, stop=stop)
    elif args.source == "replay":
        if not args.replay:
            raise SystemExit("--source replay requires --replay <obs.jsonl>")
        obs = list(tel.iter_jsonl_observations(args.replay))
        print(f"[replay] loaded {len(obs)} observations from {args.replay}")
        tel.run_source(hub, lambda: iter(obs), fps=args.fps, loop=args.loop, stop=stop)
    else:
        raise SystemExit(f"unknown --source {args.source}")

    synthetic_cams = args.synthetic_cameras or args.source == "synthetic"
    cams = cameras_mod.CameraManager.from_config(args.camera_config, synthetic=synthetic_cams)
    cams.start()
    return hub, cams, stop


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", choices=("udp", "replay", "synthetic"), default="udp",
                   help="Telemetry source (default: udp = live bridge stream).")
    p.add_argument("--replay", type=str, default=None, help="JSONL of raw obs for --source replay.")
    p.add_argument("--loop", action="store_true", help="Loop the replay file.")
    p.add_argument("--obs-port", type=int, default=tel.DEFAULT_OBS_PORT, help="Bridge obs UDP port.")
    p.add_argument("--bind-ip", type=str, default="0.0.0.0")
    p.add_argument("--fps", type=float, default=30.0, help="Replay/synthetic ingest rate.")
    p.add_argument("--camera-config", type=str, default=None, help="Path to data_collection.yaml.")
    p.add_argument("--synthetic-cameras", action="store_true", help="Force synthetic camera frames.")
    p.add_argument("--host", type=str, default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    hub, cams, _stop = build_runtime(args)
    app = create_app(hub, cams)
    print(f"[console] source={args.source} cameras={'synthetic' if cams.synthetic else 'realsense'} "
          f"-> http://{args.host}:{args.port}")
    try:
        app.run(host=args.host, port=args.port, threaded=True, debug=False)
    finally:
        cams.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
