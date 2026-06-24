"""Ball trackers. ColorBlobTracker is pure-numpy (testable without OpenCV);
HoughTracker is an OpenCV fallback (lazy import). Both return a BallObservation in pixels.

For the Intel bonus, a learned detector exported to OpenVINO can replace these and earn the
OpenVINO points — keep the BallObservation interface and swap the implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class BallObservation:
    found: bool
    u: float = 0.0
    v: float = 0.0
    radius_px: float = 0.0


class ColorBlobTracker:
    """Threshold an HSV colour range and take the largest blob's centroid. Numpy-only.

    Defaults target an ORANGE table-tennis ball. For a white ball, raise val_min and drop sat_min.
    """

    def __init__(self, hue_range=(5, 25), sat_min=0.35, val_min=0.35, min_area_px=20) -> None:
        self.hue_lo, self.hue_hi = hue_range  # degrees [0,360)
        self.sat_min = sat_min
        self.val_min = val_min
        self.min_area_px = min_area_px

    @staticmethod
    def _rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        r, g, b = (rgb[..., 0] / 255.0, rgb[..., 1] / 255.0, rgb[..., 2] / 255.0)
        mx = np.maximum(np.maximum(r, g), b)
        mn = np.minimum(np.minimum(r, g), b)
        diff = mx - mn
        safe = np.where(diff > 1e-6, diff, 1.0)  # avoid 0/0 on greyscale pixels
        hue = np.zeros_like(mx)
        mask = diff > 1e-6
        # piecewise hue
        rmax = mask & (mx == r)
        gmax = mask & (mx == g)
        bmax = mask & (mx == b)
        hue[rmax] = (60 * ((g - b) / safe)[rmax]) % 360
        hue[gmax] = (60 * ((b - r) / safe)[gmax] + 120) % 360
        hue[bmax] = (60 * ((r - g) / safe)[bmax] + 240) % 360
        sat = np.where(mx > 1e-6, diff / np.maximum(mx, 1e-6), 0.0)
        return hue, sat, mx

    def detect(self, frame_rgb: np.ndarray) -> BallObservation:
        hue, sat, val = self._rgb_to_hsv(frame_rgb)
        if self.hue_lo <= self.hue_hi:
            hue_ok = (hue >= self.hue_lo) & (hue <= self.hue_hi)
        else:  # wrap-around range (e.g. red)
            hue_ok = (hue >= self.hue_lo) | (hue <= self.hue_hi)
        mask = hue_ok & (sat >= self.sat_min) & (val >= self.val_min)
        area = int(mask.sum())
        if area < self.min_area_px:
            return BallObservation(found=False)
        vs, us = np.nonzero(mask)
        u, v = float(us.mean()), float(vs.mean())
        radius = float(np.sqrt(area / np.pi))
        return BallObservation(found=True, u=u, v=v, radius_px=radius)


class HoughTracker:
    """Circle detector via OpenCV Hough (lazy import). Use when colour thresholding is unreliable."""

    def __init__(self, dp=1.2, min_dist=50, param1=100, param2=30, min_radius=5, max_radius=80) -> None:
        self._cfg = dict(dp=dp, minDist=min_dist, param1=param1, param2=param2,
                         minRadius=min_radius, maxRadius=max_radius)

    def detect(self, frame_rgb: np.ndarray) -> BallObservation:
        import cv2  # lazy
        gray = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2GRAY)
        gray = cv2.medianBlur(gray, 5)
        circles = cv2.HoughCircles(gray, cv2.HOUGH_GRADIENT, **self._cfg)
        if circles is None:
            return BallObservation(found=False)
        u, v, r = circles[0][0]
        return BallObservation(found=True, u=float(u), v=float(v), radius_px=float(r))
