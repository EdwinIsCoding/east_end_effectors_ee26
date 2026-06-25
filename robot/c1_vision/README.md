# c1_vision — classical CV for the C1 shape-sorter insertion (perception half)

Detects the **socket** (white cube with a shaped hole) and the **peg** on the **red plate**, returning
shape + center + symmetry-reduced yaw in pixels. Built/tested off-robot; the Desktop adds camera→base
extrinsics + depth and the control loop. See `PLAN.md` → "C1 — Classical CV insertion pipeline".

Install: `pip install opencv-python` (or `-headless`) + numpy.

## API (`c1_vision.detect`)
```python
from c1_vision.detect import detect_scene, backproject, annotate
scene = detect_scene(bgr)            # bgr = cv2.imread(...) or a D405 color frame (BGR)
sock = scene["socket"]               # Detection | None  (shape/yaw FROM THE HOLE = insertion target)
peg  = scene["peg"]                  # Detection | None
# Detection: role, shape, center_px(u,v), yaw_deg (None for circle), n_sides, area_px, quality, polygon
p_base = backproject(sock.center_px, depth_m, K, T_cam_base)   # pixel+depth -> robot base (metres)
```
CLI / quick look: `python -m c1_vision.detect <image.jpg> --out annot.jpg`

## What works / caveats
- **Socket detection is solid** (the key output): finds the non-white void inside the white footprint →
  shape (triangle/square/pentagon/hexagon/circle) + center + yaw. Validated on the real red-plate photos.
- **Peg**: center is reliable for pickup; exact shape/yaw is best-effort from a *lying* 3-D peg silhouette.
  Make it reliable by presenting pegs **cross-section-up**, or just grasp at a fixed gripper yaw and rotate
  to the socket yaw (you control in-hand yaw via the grasp).
- **Red plate only.** White-on-silver (bare 8020) has no contrast — not supported by design.
- **Yaw sign**: image-y is down; the absolute sign is calibrated against the robot on hardware (like C2).
- Phone photos ≠ D405 frames — re-tune `segment_white` thresholds on a few real D405 stills.

## Desktop integration (robot-bound, TODO)
1. Camera→base **extrinsics** (AprilTag/hand-eye) → `T_cam_base`; D405 **intrinsics** → `K`.
2. Live D405 color+depth → `detect_scene` → `backproject` socket/peg to base metres.
3. Control: pick peg → above socket → rotate EE to (socket_yaw − peg_yaw) mod 360/n → descend to ~3 mm →
   compliant + 1–3 mm spiral search on `O_F_ext` → seated check.

## Tests
`python -m pytest robot/c1_vision/tests -q` — synthetic shapes (classify, yaw-equivariance, scene) +
`backproject` math. No robot, no photos.
