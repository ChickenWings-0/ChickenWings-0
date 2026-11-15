"""Extract contour lines from the height field as compact SVG path data.

For every level:

1. marching squares (skimage.measure.find_contours) gives sub-cell
   accurate polylines in grid coordinates;
2. Douglas-Peucker simplification (skimage.measure.approximate_polygon)
   removes redundant vertices;
3. specks (tiny rings, short edge fragments) are dropped because they read
   as noise, not terrain (each line is first lightly smoothed along its
   length to remove marching-squares stair-steps);
4. the remaining vertices are joined by a smooth Hermite spline (tangents
   scaled by chord length) written as relative cubic Bezier commands with
   one decimal.

Three kinds of line, as on a survey map:

* index contours         every 5th level, drawn heavier
* intermediate contours  every other level
* supplementary contours half-interval lines, dashed, only on the low,
                         flat ground where normal contours are far apart
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import ndimage
from skimage.measure import approximate_polygon, find_contours

from .field import HeightField

N_LEVELS = 30              # contour interval = 1 / N_LEVELS of the relief
INDEX_EVERY = 5            # every 5th line is an index contour
SUPPLEMENTARY_BELOW = 3    # half-interval dashed lines below this level number
LINE_SMOOTH = 1.5          # blur of vertex coordinates along a line, in vertices
SIMPLIFY_TOLERANCE = 0.3   # Douglas-Peucker tolerance, in grid cells
MIN_RING_SIZE = 9.0        # drop closed rings smaller than this (map units)
MIN_OPEN_LENGTH = 14.0     # drop open fragments shorter than this (map units)
PRECISION = 1              # decimals in the SVG path data


@dataclass
class ContourLevel:
    number: float            # level number; half-steps for supplementary lines
    value: float             # field height, 0..1
    kind: str                # "index" | "intermediate" | "supplementary"
    paths: list[str] = field(default_factory=list)


def _fmt(v: float) -> str:
    """Shortest decimal form: 12.0 -> '12', -0.5 -> '-.5', 0.0 -> '0'."""
    s = f"{v:.{PRECISION}f}".rstrip("0").rstrip(".")
    if s in ("-0", ""):
        return "0"
    if s.startswith("0."):
        return s[1:]
    if s.startswith("-0."):
        return "-" + s[2:]
    return s


def _join(numbers: list[float]) -> str:
    """Join numbers SVG-style with the fewest separators.

    A '-' always starts a new number. A leading '.' does too, but only if
    the previous number already has a decimal point ("1.5.5" is 1.5, .5)."""
    out: list[str] = []
    prev = ""
    for n in numbers:
        token = _fmt(n)
        if prev and not (token[0] == "-" or (token[0] == "." and "." in prev)):
            out.append(" ")
        out.append(token)
        prev = token
    return "".join(out)


def _bezier_path(points: np.ndarray, closed: bool) -> str:
    """Smooth spline through `points` (map units) as a relative SVG path."""
    if closed:
        pts = points[:-1]                       # drop the repeated end point
        n = len(pts)
        get = lambda i: pts[i % n]              # noqa: E731
        segments = range(n)
    else:
        pts = points
        n = len(pts)
        get = lambda i: pts[min(max(i, 0), n - 1)]  # noqa: E731
        segments = range(n - 1)

    def snap(p: np.ndarray) -> np.ndarray:
        return np.round(p, PRECISION)

    def tangent(i: int) -> np.ndarray:
        # Direction through point i, normalised by the chord lengths on
        # either side. Unlike plain Catmull-Rom (p[i+1] - p[i-1]) / 2, this
        # can't overshoot where a long segment meets a short one.
        prev, nxt = get(i) - get(i - 1), get(i + 1) - get(i)
        span = np.linalg.norm(prev) + np.linalg.norm(nxt)
        return (prev + nxt) / span if span > 0 else np.zeros(2)

    # Relative offsets are computed from rounded absolute positions, so the
    # rounding never accumulates along a long line.
    cur = snap(get(0))
    parts = ["M" + _join([cur[0], cur[1]])]
    curve = []
    for i in segments:
        p1, p2 = get(i), get(i + 1)
        chord = np.linalg.norm(p2 - p1) / 3.0
        c1 = snap(p1 + tangent(i) * chord)
        c2 = snap(p2 - tangent(i + 1) * chord)
        end = snap(p2)
        curve += [*(c1 - cur), *(c2 - cur), *(end - cur)]
        cur = end
    parts.append("c" + _join(curve))
    if closed:
        parts.append("z")
    return "".join(parts)


def _polylines(field: HeightField, value: float) -> list[tuple[np.ndarray, bool]]:
    """Simplified polylines (map units) for one level, specks removed."""
    ny, nx = field.shape
    scale = np.array([field.width / (nx - 1), field.height / (ny - 1)])
    out = []
    for raw in find_contours(field.z, value):
        closed = bool(np.allclose(raw[0], raw[-1]))
        # Ease out marching-squares stair-steps before simplifying: a light
        # blur of the vertex coordinates along the line (wrapping for rings,
        # holding the ends of open lines where they meet the frame).
        if closed:
            ring = ndimage.gaussian_filter1d(raw[:-1], LINE_SMOOTH, axis=0, mode="wrap")
            raw = np.vstack([ring, ring[:1]])
        elif len(raw) > 2:
            raw = np.vstack([raw[:1], ndimage.gaussian_filter1d(raw, LINE_SMOOTH, axis=0, mode="nearest")[1:-1], raw[-1:]])
        simple = approximate_polygon(raw, tolerance=SIMPLIFY_TOLERANCE)
        pts = simple[:, ::-1] * scale            # (row, col) -> (x, y)
        if len(pts) < (4 if closed else 2):
            continue
        if closed:
            extent = np.ptp(pts, axis=0).max()
            if extent < MIN_RING_SIZE:
                continue
        else:
            length = np.linalg.norm(np.diff(pts, axis=0), axis=1).sum()
            if length < MIN_OPEN_LENGTH:
                continue
        out.append((pts, closed))
    return out


def extract_contours(field: HeightField) -> list[ContourLevel]:
    """All contour levels, lowest first."""
    interval = 1.0 / N_LEVELS
    levels: list[ContourLevel] = []
    for number in range(1, N_LEVELS):
        kind = "index" if number % INDEX_EVERY == 0 else "intermediate"
        levels.append(ContourLevel(number=number, value=number * interval, kind=kind))
    for number in range(SUPPLEMENTARY_BELOW):
        levels.append(ContourLevel(number=number + 0.5, value=(number + 0.5) * interval, kind="supplementary"))
    levels.sort(key=lambda lv: lv.number)

    for level in levels:
        for pts, closed in _polylines(field, level.value):
            level.paths.append(_bezier_path(pts, closed))
    return [lv for lv in levels if lv.paths]
