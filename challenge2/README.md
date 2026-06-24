# Challenge 2 — Ball-balance on TCP plate

**Type:** dynamic stabilization (ball-on-plate). Feedback on ball position → command plate tilt (TCP orientation) via Cartesian pose. Bonus: hold balance across **4 TCP poses**.

## Approach (classical, recommended for the time budget)
1. **Track** ball on the plate (D405 overhead/wrist) — colour-blob or Hough circle → (x, y) on plate.
2. **Control** PD/LQR on (ball position, velocity) → commanded plate tilt.
3. **Command** tilt as a Cartesian pose to the arm (smooth, low accel between the 4 poses; feed-forward the known trajectory, let feedback reject drift).
4. **Intel bonus (optional):** run the tracker through OpenVINO on Pantherlake.

## Notes
- Orientation authority matters more than position authority here.
- Mind camera→control latency; the loop is only as fast as perception.
- Prototype risky controller gains in the MuJoCo sim sandbox before the real arm.

(Code TBD. Owner: Desktop runtime; tracker/control logic can be drafted off-robot.)
