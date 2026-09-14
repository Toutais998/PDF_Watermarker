"""Canvas/PDF coordinate and polygon geometry helpers."""

import math
from typing import Iterable, List, Tuple

import cv2
import pymupdf as fitz
import numpy as np


class GeometryMixin:
    def _point_on_page(self, x: float, y: float) -> bool:
        if not self.original_img:
            return False
        return (
            self.image_offset_x <= x <= self.image_offset_x + self.original_img.width and
            self.image_offset_y <= y <= self.image_offset_y + self.original_img.height
        )

    def _canvas_to_pdf(self, x: float, y: float) -> Tuple[float, float]:
        return (
            (x - self.image_offset_x) * self.scale_factor_x,
            (y - self.image_offset_y) * self.scale_factor_y,
        )

    def _pdf_to_canvas(self, x: float, y: float) -> Tuple[float, float]:
        return (
            x / self.scale_factor_x + self.image_offset_x,
            y / self.scale_factor_y + self.image_offset_y,
        )

    @staticmethod
    def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    @staticmethod
    def _perpendicular_distance(p, a, b) -> float:
        ax, ay = a
        bx, by = b
        px, py = p
        denom = math.hypot(bx - ax, by - ay)
        if denom == 0:
            return 0.0
        return abs((by - ay) * px - (bx - ax) * py + bx * ay - by * ax) / denom

    @staticmethod
    def _build_rotated_rect(a, b, half_width: float) -> List[Tuple[float, float]]:
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length == 0:
            return [a, b, b, a]
        nx, ny = -dy / length, dx / length
        return [
            (ax + nx * half_width, ay + ny * half_width),
            (bx + nx * half_width, by + ny * half_width),
            (bx - nx * half_width, by - ny * half_width),
            (ax - nx * half_width, ay - ny * half_width),
        ]

    @staticmethod
    def _flatten(points: Iterable[Tuple[float, float]]) -> List[float]:
        flat = []
        for x, y in points:
            flat.extend([x, y])
        return flat

    @staticmethod
    def _polygon_bbox(poly: List[Tuple[float, float]]) -> fitz.Rect:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        return fitz.Rect(min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def _point_in_polygon(point: Tuple[float, float], polygon: List[Tuple[float, float]]) -> bool:
        x, y = point
        inside = False
        n = len(polygon)
        for i in range(n):
            x1, y1 = polygon[i]
            x2, y2 = polygon[(i + 1) % n]
            if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9) + x1):
                inside = not inside
        return inside

    def _rect_intersects_polygon(self, rect: fitz.Rect, polygon: List[Tuple[float, float]]) -> bool:
        corners = [(rect.x0, rect.y0), (rect.x1, rect.y0), (rect.x1, rect.y1), (rect.x0, rect.y1)]
        if any(self._point_in_polygon(c, polygon) for c in corners):
            return True
        poly_bbox = self._polygon_bbox(polygon)
        return rect.intersects(poly_bbox)

    @staticmethod
    def _polygon_to_relative(poly_pdf: List[Tuple[float, float]], page) -> List[Tuple[float, float]]:
        return [(x / page.rect.width, y / page.rect.height) for x, y in poly_pdf]

    @staticmethod
    def _relative_to_polygon(rel_poly: List[Tuple[float, float]], page) -> List[Tuple[float, float]]:
        return [(x * page.rect.width, y * page.rect.height) for x, y in rel_poly]

    @staticmethod
    def _make_polygon_mask(bbox_pdf: fitz.Rect, poly_pdf: List[Tuple[float, float]], width: int, height: int):
        pts = []
        for x, y in poly_pdf:
            px = int(round((x - bbox_pdf.x0) / max(1e-6, bbox_pdf.width) * (width - 1)))
            py = int(round((y - bbox_pdf.y0) / max(1e-6, bbox_pdf.height) * (height - 1)))
            pts.append([px, py])
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(mask, [np.array(pts, dtype=np.int32)], 255)
        return mask
