"""Ground-plane calibration: map coordinates (lat/lng) into camera-image pixels.

A fixed camera looking at flat ground sees a projective transform of that ground, so four or more point pairs
— a spot on the map and the same spot in the camera image — define a homography. With it, a zone an operator
draws on the map is converted into the pixel polygon the ML fence already enforces, which is why the pipeline
itself needs no change.

Latitude/longitude degrees are converted to local metres around the first reference point before fitting:
degree-sized numbers are numerically poor for a homography solve, and metres keep the residual error readable.
"""
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

logger = logging.getLogger("sih26187.calibration")

MIN_POINTS = 4
MAX_POINTS = 24
#: A fit worse than this is almost certainly mismatched point pairs rather than lens distortion.
MAX_ACCEPTABLE_ERROR_PX = 60.0
EARTH_RADIUS_M = 6_378_137.0


class CalibrationError(ValueError):
    """The calibration cannot be computed or applied; the message is shown to the operator."""


def _to_metres(lat: float, lng: float, origin: Sequence[float]) -> Tuple[float, float]:
    """Equirectangular projection around the origin — exact enough over a camera's field of view."""
    origin_lat, origin_lng = float(origin[0]), float(origin[1])
    east = math.radians(lng - origin_lng) * EARTH_RADIUS_M * math.cos(math.radians(origin_lat))
    north = math.radians(lat - origin_lat) * EARTH_RADIUS_M
    return east, north


def _matrix(calibration: Dict[str, Any]) -> np.ndarray:
    matrix = np.asarray(calibration.get("homography"), dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all():
        raise CalibrationError("The stored calibration is not a valid homography")
    return matrix


def compute(points: List[Dict[str, Sequence[float]]], image_size: Sequence[int]) -> Dict[str, Any]:
    """Fit the ground plane from point pairs [{"image": [x, y], "geo": [lat, lng]}, ...].

    Returns the calibration record stored on the camera (points, origin, homography, fit error)."""
    if not MIN_POINTS <= len(points) <= MAX_POINTS:
        raise CalibrationError(f"Calibration needs between {MIN_POINTS} and {MAX_POINTS} point pairs")
    width, height = int(image_size[0]), int(image_size[1])
    if width < 2 or height < 2:
        raise CalibrationError("image_size must be the camera frame size in pixels")

    image_points, geo_points = [], []
    for index, pair in enumerate(points):
        try:
            x, y = float(pair["image"][0]), float(pair["image"][1])
            lat, lng = float(pair["geo"][0]), float(pair["geo"][1])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise CalibrationError(f"Point {index + 1} must be {{image: [x, y], geo: [lat, lng]}}") from exc
        if not (0 <= x <= width and 0 <= y <= height):
            raise CalibrationError(f"Point {index + 1} is outside the {width}x{height} camera frame")
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise CalibrationError(f"Point {index + 1} has an invalid map position")
        image_points.append([x, y])
        geo_points.append([lat, lng])

    if len({(round(x, 2), round(y, 2)) for x, y in image_points}) < MIN_POINTS:
        raise CalibrationError("Every calibration point must be a different spot in the camera image")
    if len({(round(lat, 7), round(lng, 7)) for lat, lng in geo_points}) < MIN_POINTS:
        raise CalibrationError("Every calibration point must be a different spot on the map")

    origin = geo_points[0]
    source = np.array([_to_metres(lat, lng, origin) for lat, lng in geo_points], dtype=np.float64)
    destination = np.array(image_points, dtype=np.float64)

    matrix, _mask = cv2.findHomography(source, destination, method=0)
    if matrix is None or not np.isfinite(matrix).all():
        raise CalibrationError("The points are in a line or repeated — pick four spread-out landmarks")

    calibration: Dict[str, Any] = {
        "points": [{"image": image, "geo": geo} for image, geo in zip(image_points, geo_points)],
        "origin": origin,
        "homography": matrix.tolist(),
        "image_size": [width, height],
    }
    error = reprojection_error(calibration)
    if error > MAX_ACCEPTABLE_ERROR_PX:
        raise CalibrationError(
            f"The points do not describe one flat surface (average error {error:.0f} px). "
            "Re-pick landmarks that lie on the ground and match exactly."
        )
    calibration["error_px"] = round(error, 2)
    calibration["calibrated_at"] = datetime.now(timezone.utc).isoformat()
    return calibration


def reprojection_error(calibration: Dict[str, Any]) -> float:
    """Average distance in pixels between each calibration point and where the fit puts it."""
    matrix = _matrix(calibration)
    origin = calibration.get("origin") or [0.0, 0.0]
    distances = []
    for pair in calibration.get("points", []):
        lat, lng = float(pair["geo"][0]), float(pair["geo"][1])
        projected = _project_metres(matrix, _to_metres(lat, lng, origin))
        if projected is None:
            return float("inf")
        expected = (float(pair["image"][0]), float(pair["image"][1]))
        distances.append(math.dist(projected, expected))
    return float(np.mean(distances)) if distances else float("inf")


def _project_metres(matrix: np.ndarray, point: Tuple[float, float]) -> Optional[Tuple[float, float]]:
    vector = matrix @ np.array([point[0], point[1], 1.0], dtype=np.float64)
    if not np.isfinite(vector).all() or abs(vector[2]) < 1e-12:
        return None
    # A non-positive scale means the point sits behind the camera plane and has no image.
    if vector[2] <= 0:
        return None
    return float(vector[0] / vector[2]), float(vector[1] / vector[2])


def project_polygon(calibration: Dict[str, Any], geo_polygon: Sequence[Sequence[float]]) -> List[List[int]]:
    """Map polygon [[lat, lng], ...] → camera pixel polygon [[x, y], ...] the ML fence can enforce."""
    if not calibration:
        raise CalibrationError("This camera is not calibrated yet, so a map zone cannot be projected onto it")
    matrix = _matrix(calibration)
    origin = calibration.get("origin") or [0.0, 0.0]
    width, height = (calibration.get("image_size") or [1920, 1080])[:2]

    pixels: List[List[int]] = []
    for index, point in enumerate(geo_polygon):
        lat, lng = float(point[0]), float(point[1])
        projected = _project_metres(matrix, _to_metres(lat, lng, origin))
        if projected is None:
            raise CalibrationError(f"Corner {index + 1} of the zone is behind the camera — it cannot see that area")
        # Clamp to the frame: a corner may sit just outside while the zone itself is visible.
        pixels.append([
            int(round(min(max(projected[0], 0.0), float(width)))),
            int(round(min(max(projected[1], 0.0), float(height)))),
        ])

    if len({(x, y) for x, y in pixels}) < 3:
        raise CalibrationError("The zone collapses to a line in this camera's view — draw it inside the camera's field of view")
    return pixels


def covers(calibration: Dict[str, Any], geo_polygon: Sequence[Sequence[float]]) -> bool:
    """True when at least one corner of the map polygon falls inside the camera frame."""
    try:
        pixels = project_polygon(calibration, geo_polygon)
    except CalibrationError:
        return False
    width, height = (calibration.get("image_size") or [1920, 1080])[:2]
    return any(0 < x < width and 0 < y < height for x, y in pixels)
