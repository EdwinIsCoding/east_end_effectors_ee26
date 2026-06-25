// EE26 operator console — consume the telemetry SSE stream and render it.
"use strict";

const NUM_JOINTS = 7;
const ANOM_VEL = 1.2; // rad/s — highlight a joint moving faster than this
const FAULT_KEYS = ["packet_timeout", "jump_rejected", "workspace_clamped", "robot_not_ready",
  "control_exception", "ik_rejected", "gripper_fault"];
const GRIP_MAX_M = 0.08;

const $ = (id) => document.getElementById(id);

// Build the per-joint rows once; cache their nodes for fast updates.
const jointNodes = [];
(function buildJoints() {
  const root = $("joints");
  for (let i = 0; i < NUM_JOINTS; i++) {
    const row = document.createElement("div");
    row.className = "joint";
    row.innerHTML =
      `<span class="j">j${i}</span>` +
      `<span class="ang tnum">—</span>` +
      `<span class="vel tnum">—</span>` +
      `<canvas width="200" height="26"></canvas>`;
    root.appendChild(row);
    jointNodes.push({ row, ang: row.querySelector(".ang"), vel: row.querySelector(".vel"),
      canvas: row.querySelector("canvas") });
  }
})();

(function buildFaults() {
  const root = $("faults");
  for (const k of FAULT_KEYS) {
    const el = document.createElement("span");
    el.className = "fault";
    el.dataset.key = k;
    el.textContent = k.replace(/_/g, " ");
    root.appendChild(el);
  }
})();

function sparkline(canvas, data) {
  const ctx = canvas.getContext("2d");
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (!data || data.length < 2) return;
  let lo = Math.min(...data), hi = Math.max(...data);
  if (hi - lo < 1e-6) { hi += 0.5; lo -= 0.5; }
  const x = (i) => (i / (data.length - 1)) * w;
  const y = (v) => h - ((v - lo) / (hi - lo)) * (h - 4) - 2;
  ctx.beginPath();
  ctx.moveTo(0, y(data[0]));
  for (let i = 1; i < data.length; i++) ctx.lineTo(x(i), y(data[i]));
  ctx.strokeStyle = "rgba(163,163,163,0.9)";
  ctx.lineWidth = 1;
  ctx.stroke();
  ctx.lineTo(w, h); ctx.lineTo(0, h); ctx.closePath();
  ctx.fillStyle = "rgba(120,120,120,0.10)";
  ctx.fill();
}

function fmt(v, d = 3) { return (v === undefined || v === null) ? "—" : Number(v).toFixed(d); }

function render(s) {
  // header
  const dot = $("conn-dot");
  dot.className = "dot " + (s.connected ? "live" : "stale");
  $("conn-label").textContent = s.connected ? "live" : (s.samples ? "stale" : "waiting");
  $("source-pill").textContent = "source " + (s.source || "—");
  $("rate").textContent = (s.rate_hz != null ? s.rate_hz.toFixed(1) : "—") + " Hz";
  $("samples").textContent = s.samples ?? "—";
  const ep = s.episode || {};
  $("episode").textContent = ep.active ? `REC ${String(ep.count).padStart(2, "0")}` : `idle (${ep.count || 0})`;

  const L = s.latest;
  $("tele-meta").textContent = L ? `${s.samples} samples · ${s.rate_hz}Hz · ${s.status}` : "awaiting frames";
  if (!L) return;

  for (let i = 0; i < NUM_JOINTS; i++) {
    const n = jointNodes[i];
    const ang = L.q[i] ?? 0, vel = L.dq[i] ?? 0;
    n.ang.textContent = fmt(ang, 3);
    n.vel.textContent = fmt(vel, 2) + " r/s";
    n.row.classList.toggle("anom", Math.abs(vel) > ANOM_VEL);
    sparkline(n.canvas, s.series.q[i]);
  }

  const gripMm = (L.gripper_width || 0) * 1000;
  $("grip").textContent = gripMm.toFixed(1) + " mm";
  $("grip-bar").style.width = Math.max(0, Math.min(100, (L.gripper_width / GRIP_MAX_M) * 100)) + "%";
  $("grip-state").textContent = L.gripper_state || "—";
  $("tcp").textContent = (L.tcp_xyz || []).map((v) => v.toFixed(3)).join(", ") || "—";
  $("manip").textContent = fmt(L.target_manipulability, 4);
  $("cmode").textContent = L.control_mode || "—";
  $("teleop").textContent = (L.teleop_state || "—") + (L.teleop_active ? " · active" : "");
  $("mode-meta").textContent = L.target_fresh ? "target fresh" : "target stale";

  let anyFault = false;
  for (const el of document.querySelectorAll(".fault")) {
    const on = !!(L.faults && L.faults[el.dataset.key]);
    el.classList.toggle("on", on);
    anyFault = anyFault || on;
  }
  $("fault-meta").textContent = anyFault ? "FAULT" : "nominal";
}

function clock() { $("clock").textContent = new Date().toISOString().slice(11, 19) + "Z"; }
setInterval(clock, 1000); clock();

function connect() {
  const es = new EventSource("/telemetry/stream");
  es.onmessage = (e) => { try { render(JSON.parse(e.data)); } catch (_) {} };
  es.onerror = () => { $("conn-dot").className = "dot stale"; $("conn-label").textContent = "reconnecting"; };
}
connect();
