"""Map ball pixel position to plate-frame coordinates.

Plate frame: origin at plate centre, +x image-right, +y image-up (image v is flipped),
units = metres. Calibrate once: click the plate centre and rim in the camera view to get
centre_px + radius_px, and measure the real plate radius.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlateCalibration:
    center_px: tuple[float, float]
    radius_px: float
    plate_radius_m: float

    def pixel_to_plate(self, u: float, v: float) -> tuple[float, float]:
        """Pixel (u right, v down) → (x, y) metres in the plate frame."""
        cx, cy = self.center_px
        scale = self.plate_radius_m / self.radius_px
        return (u - cx) * scale, -(v - cy) * scale

    def normalized(self, u: float, v: float) -> tuple[float, float]:
        """(x, y) in [-1, 1] at the rim (radius-independent of real size)."""
        cx, cy = self.center_px
        return (u - cx) / self.radius_px, -(v - cy) / self.radius_px

    def on_plate(self, u: float, v: float, margin: float = 1.05) -> bool:
        cx, cy = self.center_px
        return (u - cx) ** 2 + (v - cy) ** 2 <= (self.radius_px * margin) ** 2
