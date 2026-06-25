# C1 Classical Insertion — Desktop Runbook (drive the robot to near-perfect)

End-to-end runbook for the DESKTOP session to finish the C1 classical CV insertion pipeline on the real
Franka with direct camera access. Perception is already built + tested off-robot (`robot/c1_vision/`,
validated on red-plate photos); this runbook covers calibration, depth, control, and tuning to a target
success rate. Strategy context: `PLAN.md` → "C1 — Classical CV insertion pipeline".

---

## 0. Operating principles (read first — these govern everything)

1. **PHYSICAL-SETUP-FIRST. If something can't be done cleanly in software, STOP and ask the operator to
   change the physical setup — do NOT invent a fragile next-best software workaround.** Prefer a one-line
   operator request over a complex hack. Examples of correct behaviour:
   - Bad contrast / detection flaky → ask to use the **red plate** + fix lighting (don't over-tune thresholds).
   - Peg pose unreliable from a lying peg → ask the operator to present pegs **cross-section-up** (don't build a 3-D peg estimator).
   - Camera doesn't see the whole plate → ask to **re-aim/raise the camera** (don't crop-hack).
   - Insertion jams → ask to **add a chamfer / check peg-socket clearance** (don't fight it with force).
   - Extrinsics poor → ask to **re-place the AprilTag** / re-measure (don't fudge offsets).
   Make every physical request **specific and verifiable** ("place the pentagon cube hole-up at plate
   centre; peg cross-section-up 10 cm to its right"), then wait for confirmation.
2. **Safety:** confirm with the operator before EVERY first motion of a new step. Start at low speed.
   E-stop reachable. FCI single-client (Desk OR bridge, never both). Live motion: operator holds the
   **X4 enabling grip**. Rehearse `error_recovery`. Loosen collision thresholds only deliberately and revert.
3. **Gate-by-gate:** validate each sub-step in isolation (perception → backproject → grasp → align →
   insert) and pass its GATE before combining. Never run the full loop until the parts pass.
4. **Measure "near-perfect":** target **≥9/10 successful insertions per shape** across ≥2 shapes, with
   logged failure modes. Quantify; don't eyeball.
5. **Git:** `git pull` before work, small commits, push often, stay in `robot/`. The off-robot teammate
   may be away — you own `robot/` now.

## Physical-setup checklist (ask the operator to confirm up front)
- [ ] **Red plate** is the work surface (NOT bare 8020). Even, diffuse lighting; minimal glare/shadow.
- [ ] **External/overhead D405** mounted top-down, whole plate in FOV; **wrist D405** unobstructed.
- [ ] Cube placed **hole-up** (vertical insertion = 4-DOF x,y,z,yaw). Pegs **cross-section-up**.
- [ ] **AprilTag** (known size) placed at a measured pose for extrinsics — or attached to the gripper for hand-eye.
- [ ] Sockets printed with **chamfered entries**; note the peg↔socket clearance.
- [ ] Workspace clear; E-stop in reach; someone available to hold the **X4 grip** for live runs.

---

## Phase C1.0 — Env + safety preflight
- [ ] Booted into the **RT kernel** (`6.8.0-rt8-franka`). Bridge built (libfranka 0.9.2 prefix). FCI active in Desk.
- [ ] `cv2` + `pyrealsense2` available; `python -m pytest robot/c1_vision/tests -q` green.
- [ ] Robot homes safely (`robot/franka-sanity-checks/rehome.sh`). **GATE: safe home.**

## Phase C1.1 — Cameras + intrinsics
- [ ] Both D405 enumerate; serials match `data_collection.yaml`. Read **factory intrinsics K** from each (RealSense API).
- [ ] Capture live frames; confirm the overhead D405 sees the full red plate. *If not → ask operator to re-aim/raise it.*
- [ ] **GATE:** live color+depth from both cameras; K recorded.

## Phase C1.2 — Camera→base extrinsics (the precision-critical step)
- [ ] Operator places the **AprilTag** at a measured pose (or on the gripper). Run a calibration routine →
      `T_cam_base` (4×4) for the overhead camera (and wrist if used).
- [ ] **Validate by touch:** teleop/jog the TCP to a known plate point; compare `backproject(pixel,depth,K,T)`
      to the measured TCP position. *If error > a few mm → ask operator to re-place/re-measure the tag; don't fudge.*
- [ ] **GATE:** back-projected point within ~3 mm of ground truth.

## Phase C1.3 — Perception on REAL D405 frames
- [ ] Run `python -m robot.c1_vision.detect <live_frame> --out annot.jpg` on real overhead frames.
- [ ] Re-tune `segment_white` thresholds for the real lighting/exposure (small edits only). Confirm
      `detect_scene` gives correct **socket shape + center + yaw** for each shape (pentagon/triangle/circle/…).
- [ ] *If white-on-white still fails → ask operator to fix lighting / confirm red plate; don't hack.*
- [ ] **GATE:** socket detection correct on ≥3 shapes, live.

## Phase C1.4 — Control interface (decide, then build the move-to-pose primitive)
The classical loop needs **absolute Cartesian moves** (go above socket; rotate yaw; descend) and **contact
compliance**. Pick one (ask operator/Edwin if unsure), then implement + unit-test the primitive:
- **(Recommended) Dedicated libfranka C1 controller** (C++, links `~/opt/libfranka-0.9.2`): Cartesian motion
  generation + Cartesian impedance for compliant insertion; reads `O_F_ext`. Perception (Python) feeds it
  target poses (UDP/file). Cleanest path to "near-perfect" contact behaviour.
- **OR extend the bridge** to accept an absolute Cartesian-pose command (new mode + IK in bridge).
- **OR drive via `UdpXrSource`** (teleop pose path) — works but anchor/delta-based, less precise for scripting.
- [ ] Ensure the chosen path exposes **`O_F_ext_hat`** (external wrench). *If missing, add it to the obs/controller.*
- [ ] **GATE:** commanded "move EE to (x,y,z,yaw)" lands within ~2 mm / ~1° on hardware, at safe speed.

## Phase C1.5 — Pick the peg
- [ ] From `detect_scene` → peg center+depth → `backproject` → grasp pose (approach above, descend, close, lift).
- [ ] Validate grasp reliability. *If pegs are hard to localize/grasp → ask operator to present them
      cross-section-up or add a simple placement jig; don't build a brittle estimator.*
- [ ] **GATE:** ≥9/10 successful grasps.

## Phase C1.6 — Align + approach
- [ ] Move above socket; rotate EE to **(socket_yaw − peg_yaw) mod 360/n** (skip for circle). Descend to ~3 mm above rim.
- [ ] **GATE:** peg hovers concentric + angularly aligned over the hole (eyeball + wrist-cam servo).

## Phase C1.7 — Insert (compliant + search)
- [ ] Compliant descent + small **1–3 mm spiral/Lissajous search**, monitoring `O_F_ext`; stop on seating
      force or target depth; back off + retry on jam.
- [ ] Tune search radius, descent speed, force thresholds, impedance stiffness. *If it consistently jams →
      ask operator to verify chamfer + clearance; don't crank force.*
- [ ] **GATE:** repeated insertions for one shape.

## Phase C1.8 — Full loop + verify
- [ ] Chain pick→align→insert→verify (seated = target Z + force band). Add `error_recovery` between trials.
- [ ] **GATE:** full autonomous loop succeeds end-to-end.

## Phase C1.9 — Tune to near-perfect
- [ ] Run **10 trials × ≥2 shapes**; log success rate + failure modes (between trials ask operator to reset
      objects to specified poses). Iterate the weakest stage.
- [ ] **DONE when ≥9/10 per shape**, with the failure modes understood. Record results + the final config.

---

## Notes
- Monitor live via the **operator console** (`robot/operator_console/`) for telemetry/cameras during runs.
- Classical alone does NOT earn the Intel/OpenVINO bonus — it's the reliable C1 floor; pursue the VLA/OpenVINO
  ceiling separately once data exists.
- Keep the off-robot teammate's `CONTRACT.md` intact (dataset/wire formats) — C1 classical doesn't change it.
