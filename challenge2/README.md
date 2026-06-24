# Challenge 2 — Ball-balance on TCP plate

Keep a table-tennis ball centred on a plate at the TCP. Bonus: hold balance across **4 TCP poses**.
Classical: track ball → PD on (position, velocity) → plate tilt via Cartesian pose. Logic authored
off-robot (hardware-free, tested); the **Desktop wires the command sink to the bridge**.

## Package map (`challenge2/src/`)
| Module | Role |
|---|---|
| `calibration.py` | `PlateCalibration`: ball pixel → plate-frame metres (centre/rim/real-radius). |
| `tracker.py` | `ColorBlobTracker` (numpy, orange ball) / `HoughTracker` (OpenCV). `BallObservation` in px. |
| `controller.py` | `PDBalanceController`: ball pos/vel → `(tilt_x, tilt_y)` clamped. |
| `plate_command.py` | `tilt_to_pose(base, tilt_x, tilt_y, signs)` → target TCP pose (orientation only). |
| `loop.py` | `balance_step()` / `run()`: tracker→PD→pose→`CommandSink`. Mock-testable. |

## The boundary the Desktop implements
A `CommandSink.send(pose)` where `pose = (position[3], quat_xyzw[4])`. Forward the target TCP pose to
the bridge's **Cartesian-pose** path (the teleop/pose interface, not the joint policy port). Position is
held; only orientation tilts. Base pose = the plate-level pose, or a slow trajectory between the 4 poses.

## Bring-up order (Desktop, on hardware)
1. Mount plate + a clear external/overhead D405 view of the plate. Calibrate `PlateCalibration`
   (click centre + rim, measure real radius). Confirm `ColorBlobTracker` locks the ball (tune hue for
   the actual ball colour; white ball → raise `val_min`, drop `sat_min`).
2. **Sign check first:** with low gains, nudge `tilt_x` and confirm the ball rolls the expected way;
   flip `signs=(±1, ±1)` in `tilt_to_pose` until correct. (Camera mounting can invert either axis.)
3. Raise `kp/kd`; keep `max_tilt_rad` modest. Hold centre, then add the 4-pose trajectory (smooth,
   low-accel) and let PD reject drift.

## Run (hardware-free demo of the loop)
```bash
cd challenge2 && python -m pytest tests -q   # uses the off-robot venv
```

## Intel bonus
Swap the classical tracker for a learned ball detector exported to **OpenVINO** on Pantherlake
(keep the `BallObservation` interface). Mind the camera→control latency budget; the loop is only as
fast as perception.
