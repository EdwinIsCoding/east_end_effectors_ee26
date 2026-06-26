"""Classical CV for the C1 shape-sorter insertion (perception half — runs off-robot).

Detects the white socket (cube with a dark polygonal hole) and the white peg on the RED PLATE, and
recovers shape + center + symmetry-reduced yaw. The Desktop adds camera->base extrinsics + depth
(see `backproject`) and the pick/align/insert control. Tuned for white-on-red; the silver 8020 table
is intentionally NOT supported (white-on-silver has no contrast — use the plate).

Outputs are in PIXELS; convert to robot-base metres with `backproject(center_px, depth_m, K, T_cam_base)`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

SHAPE_BY_SIDES = {3: "triangle", 4: "square", 5: "pentagon", 6: "hexagon"}


@dataclass
class Detection:
    role: str                      # "socket" | "peg"
    shape: str                     # triangle/square/pentagon/hexagon/circle/unknown
    center_px: tuple               # (u, v)
    yaw_deg: Optional[float]       # symmetry-reduced [0, 360/n); None for circle
    n_sides: int                   # 0 for circle
    area_px: float
    quality: float                 # 0..1 (solidity * fill)
    grasp_axis_deg: Optional[float] = None  # minAreaRect angle: parallel-jaw grasp orientation (sign calibrated on HW)
    polygon: np.ndarray = field(default_factory=lambda: np.empty((0, 2)))


def _import_cv2():
    import cv2  # lazy so `backproject` / dataclasses import without OpenCV
    return cv2


def segment_white(bgr: np.ndarray, sat_max: int = 90, val_min: int = 150) -> np.ndarray:
    """Binary mask of white objects on the red plate: low saturation AND high value."""
    cv2 = _import_cv2()
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    s, v = hsv[..., 1], hsv[..., 2]
    mask = ((s < sat_max) & (v > val_min)).astype(np.uint8) * 255
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def classify_polygon(contour) -> tuple:
    """Return (shape, n_sides, approx_polygon[Nx2]). Circle => ('circle', 0, hull)."""
    cv2 = _import_cv2()
    peri = cv2.arcLength(contour, True)
    area = cv2.contourArea(contour)
    if peri <= 0 or area <= 0:
        return "unknown", -1, contour.reshape(-1, 2).astype(float)
    circularity = 4.0 * math.pi * area / (peri * peri)
    approx = cv2.approxPolyDP(contour, 0.035 * peri, True).reshape(-1, 2).astype(float)
    n = len(approx)
    if circularity > 0.82 and n > 6:
        return "circle", 0, approx
    if circularity > 0.88:           # near-perfect circle even if approx gave few pts
        return "circle", 0, approx
    return SHAPE_BY_SIDES.get(n, "unknown"), (n if n in SHAPE_BY_SIDES else -1), approx


def estimate_yaw(polygon: np.ndarray, n_sides: int) -> Optional[float]:
    """Canonical orientation in [0, 360/n): angle of the centroid->vertex vector, mod the symmetry."""
    if n_sides < 3 or len(polygon) < 3:
        return None
    c = polygon.mean(axis=0)
    v0 = polygon[0] - c
    ang = math.degrees(math.atan2(v0[1], v0[0]))   # image y is down; sign calibrated on hardware
    period = 360.0 / n_sides
    return ang % period


def _solidity(contour) -> float:
    cv2 = _import_cv2()
    area = cv2.contourArea(contour)
    hull = cv2.convexHull(contour)
    h = cv2.contourArea(hull)
    return float(area / h) if h > 0 else 0.0


def _largest_interior_hole(object_mask, white_mask, min_frac: float = 0.03):
    """The socket hole = the non-white void inside the white footprint (a gap in the white mask).

    Robust to a lit/beige cavity OR a dark one — it only needs the cavity to not be white.
    """
    cv2 = _import_cv2()
    void = ((object_mask > 0) & (white_mask == 0)).astype(np.uint8) * 255
    void = cv2.morphologyEx(void, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    cnts, _ = cv2.findContours(void, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    obj_area = float((object_mask > 0).sum())
    best = max(cnts, key=cv2.contourArea)
    if cv2.contourArea(best) < min_frac * obj_area:
        return None
    return best


def _detection_from_contour(role, bgr, contour) -> Detection:
    cv2 = _import_cv2()
    shape, n, poly = classify_polygon(contour)
    M = cv2.moments(contour)
    cx = M["m10"] / M["m00"] if M["m00"] else float(poly[:, 0].mean())
    cy = M["m01"] / M["m00"] if M["m00"] else float(poly[:, 1].mean())
    (_, _), (_, _), rect_angle = cv2.minAreaRect(contour)
    return Detection(role=role, shape=shape, center_px=(float(cx), float(cy)),
                     yaw_deg=estimate_yaw(poly, n), n_sides=max(n, 0),
                     area_px=float(cv2.contourArea(contour)), quality=round(_solidity(contour), 3),
                     grasp_axis_deg=float(rect_angle), polygon=poly)


def detect_scene(bgr: np.ndarray, min_area_px: int = 1500) -> dict:
    """Find the socket (white face with a dark hole) and the peg (solid white) on the red plate.

    Returns {"socket": Detection|None, "peg": Detection|None, "objects": [Detection,...]}.
    Socket shape/yaw come from its HOLE (the insertion target); the peg from its silhouette.
    """
    cv2 = _import_cv2()
    mask = segment_white(bgr)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    objects, socket, peg = [], None, None
    for c in sorted(cnts, key=cv2.contourArea, reverse=True):
        if cv2.contourArea(c) < min_area_px:
            continue
        obj_mask = np.zeros(mask.shape, np.uint8)
        cv2.drawContours(obj_mask, [c], -1, 255, -1)
        hole = _largest_interior_hole(obj_mask, mask)
        if hole is not None and socket is None:
            socket = _detection_from_contour("socket", bgr, hole)   # shape/yaw FROM THE HOLE
            objects.append(socket)
        elif peg is None:
            peg = _detection_from_contour("peg", bgr, c)
            objects.append(peg)
        else:
            objects.append(_detection_from_contour("object", bgr, c))
    return {"socket": socket, "peg": peg, "objects": objects}


def backproject(center_px, depth_m: float, K: np.ndarray, T_cam_base: np.ndarray) -> np.ndarray:
    """Pixel (u,v) + metric depth -> 3D point in the robot base frame. Pure numpy (no robot needed).

    K = 3x3 intrinsics (fx,fy,cx,cy); T_cam_base = 4x4 camera->base transform.
    """
    u, v = float(center_px[0]), float(center_px[1])
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    p_cam = np.array([(u - cx) / fx * depth_m, (v - cy) / fy * depth_m, depth_m, 1.0])
    return (T_cam_base @ p_cam)[:3]


def annotate(bgr: np.ndarray, scene: dict) -> np.ndarray:
    """Draw detections for debugging."""
    cv2 = _import_cv2()
    out = bgr.copy()
    colors = {"socket": (0, 255, 0), "peg": (255, 128, 0), "object": (0, 0, 255)}
    for det in scene["objects"]:
        col = colors.get(det.role, (200, 200, 200))
        if len(det.polygon) >= 3:
            cv2.polylines(out, [det.polygon.astype(np.int32)], True, col, 3)
        u, v = int(det.center_px[0]), int(det.center_px[1])
        cv2.circle(out, (u, v), 5, col, -1)
        yaw = f"{det.yaw_deg:.0f}" if det.yaw_deg is not None else "-"
        cv2.putText(out, f"{det.role}:{det.shape} yaw={yaw}", (u + 8, v),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, col, 2)
    return out


def _main() -> int:
    import argparse
    cv2 = _import_cv2()
    ap = argparse.ArgumentParser(description="Detect C1 socket/peg in an image (red plate).")
    ap.add_argument("image")
    ap.add_argument("--out", default=None, help="save annotated image here")
    args = ap.parse_args()
    bgr = cv2.imread(args.image)
    if bgr is None:
        print(f"could not read {args.image}")
        return 1
    scene = detect_scene(bgr)
    for role in ("socket", "peg"):
        d = scene[role]
        print(f"{role}: " + (f"{d.shape} center={tuple(round(x) for x in d.center_px)} "
                             f"yaw={d.yaw_deg} quality={d.quality}" if d else "None"))
    if args.out:
        cv2.imwrite(args.out, annotate(bgr, scene))
        print(f"annotated -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
