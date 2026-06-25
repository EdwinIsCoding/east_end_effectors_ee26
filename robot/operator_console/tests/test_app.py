"""Smoke tests for the console Flask app (non-streaming routes). Gated on flask + cv2."""
import pytest

pytest.importorskip("flask")
pytest.importorskip("cv2")

from robot.operator_console import app as console_app
from robot.operator_console import cameras as cam
from robot.operator_console import telemetry as tel


def _client():
    hub = tel.TelemetryHub(source="synthetic")
    cams = cam.CameraManager(cam.FALLBACK_CAMERAS, synthetic=True, width=160, height=90)
    cams.start()
    app = console_app.create_app(hub, cams)
    return app.test_client(), hub


def test_healthz_and_index():
    client, _ = _client()
    assert client.get("/healthz").get_json()["ok"] is True
    r = client.get("/")
    assert r.status_code == 200 and b"operator console" in r.data


def test_api_state_reflects_ingest():
    client, hub = _client()
    assert client.get("/api/state").get_json()["latest"] is None
    hub.ingest(next(tel.synthetic_observations(count=1)))
    state = client.get("/api/state").get_json()
    assert state["latest"] is not None and len(state["latest"]["q"]) == 7
    assert {c["id"] for c in state["cameras"]} == {"wrist", "third_person"}
