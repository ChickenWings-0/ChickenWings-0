"""Turn weekly activity into a smooth 2D height field.

Layout: X runs over the 52 weeks, Y is split into one horizontal "lane" per
top repository (plus one lane for everything else). Every repo-week with
commits adds an elongated Gaussian hill to its lane, so consecutive busy
weeks merge into ridges and quiet weeks become valleys.

Steps, each deterministic:

1. lanes      pick the top repositories, merge the rest into one lane and
              order the lanes from the centre outwards so the main range sits
              in the middle of the sheet.
2. hills      sum Gaussians weighted by log1p(commits) so one huge week
              can't flatten every other hill.
3. warp       resample through a smooth, seeded displacement so outlines
              wander like real terrain rather than perfect ellipses.
4. relief     blur away grid artefacts, add a broad massif layer (peaks
              joined by saddles) and a little flank texture, then lift the
              foothills with a log curve.
5. base       add a very low seeded noise floor so empty months read as
              gentle ground instead of a flat void.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .data import N_WEEKS, Activity

# Field resolution, in map units (1 unit == 1 SVG user unit of the map frame).
MAP_WIDTH = 1120.0
MAP_HEIGHT = 440.0
CELL = 2.0

MAX_LANES = 8              # top repositories + 1 merged lane
HILL_SIGMA_WEEKS = 1.35    # along-time spread of one week's hill
HILL_SIGMA_LANE = 0.42     # across-lane spread, as a fraction of lane spacing
MAX_LANE_SPACING = 64.0    # cap on the spacing used for hill width, map units
MEANDER = 0.28             # ridge wander across its lane, fraction of spacing
WARP_AMPLITUDE = 20.0      # max displacement of the domain warp, map units
WARP_EDGE_FADE = 60.0      # warp fades to zero this close to the east/west edges
EDGE_MARGIN = 14.0         # hill centres stay at least this far from those edges
MASSIF_SIGMA = 52.0        # blur radius of the broad massif layer, map units
MASSIF_WEIGHT = 0.35       # share of the massif layer in the final relief
RELIEF = 0.12              # +- strength of the mid-frequency flank relief
FOOTHILL_LIFT = 3.0        # > 0 widens foothills and spaces out summit contours
SMOOTH_SIGMA = 2.5         # blur of the summed hills, in cells
FINAL_SIGMA = 1.5          # last light blur of the finished field, in cells
BASE_AMPLITUDE = 0.1       # noise floor height relative to the highest peak


@dataclass
class Lane:
    key: str                 # stable identifier (owner/name or "other")
    label: str | None        # repository name; None for private or merged work
    weekly: np.ndarray       # commits (or contributions) per week
    y: float = 0.0           # lane centre, map units from the top


@dataclass
class HeightField:
    z: np.ndarray            # (ny, nx), normalised to 0..1
    lanes: list[Lane]
    lane_z: np.ndarray       # (lanes, ny, nx) each lane's share before normalising
    width: float = MAP_WIDTH
    height: float = MAP_HEIGHT

    @property
    def shape(self) -> tuple[int, int]:
        return self.z.shape


def week_x(week: float, width: float = MAP_WIDTH) -> float:
    """Map x of the middle of `week`."""
    return (week + 0.5) * width / N_WEEKS


def _seed(*parts: str) -> int:
    """Stable seed from strings (Python's hash() is salted per process)."""
    return zlib.crc32("|".join(parts).encode("utf-8"))


def _fbm(shape: tuple[int, int], seed: int, octaves=((6, 3, 1.0), (12, 5, 0.5), (24, 10, 0.25))) -> np.ndarray:
    """Smooth fractal value noise in [0, 1]: coarse random grids, cubic-upsampled."""
    rng = np.random.default_rng(seed)
    ny, nx = shape
    out = np.zeros(shape)
    for gx, gy, weight in octaves:
        grid = rng.random((gy + 3, gx + 3))
        up = ndimage.zoom(grid, ((ny + 2 * ny / gy) / grid.shape[0], (nx + 2 * nx / gx) / grid.shape[1]), order=3)
        oy, ox = (up.shape[0] - ny) // 2, (up.shape[1] - nx) // 2
        out += weight * up[oy:oy + ny, ox:ox + nx]
    out -= out.min()
    return out / (out.max() or 1.0)


# -- 1. lanes -----------------------------------------------------------------

def build_lanes(activity: Activity, max_lanes: int = MAX_LANES) -> list[Lane]:
    named = activity.repos[: max_lanes - 1]
    rest = activity.repos[max_lanes - 1:]

    lanes = [
        Lane(key=r.full_name, label=None if r.private else r.name, weekly=np.array(r.weekly, float))
        for r in named
    ]
    other = np.array(activity.other_weekly, float)
    for r in rest:
        other += np.array(r.weekly, float)
    if other.sum() > 0:
        lanes.append(Lane(key="other", label=None, weekly=other))

    # Centre-out ordering: busiest lane in the middle, then alternate either
    # side, so the sheet reads as one range with foothills to the edges.
    lanes.sort(key=lambda lane: (-lane.weekly.sum(), lane.key))
    n = len(lanes)
    slots = sorted(range(n), key=lambda s: (abs(s - (n - 1) / 2), s))
    ordered: list[Lane | None] = [None] * n
    for lane, slot in zip(lanes, slots):
        ordered[slot] = lane

    spacing = MAP_HEIGHT / n
    for i, lane in enumerate(ordered):
        lane.y = (i + 0.5) * spacing
    return ordered  # type: ignore[return-value]


# -- 2-5. the field -------------------------------------------------------------

def build_field(activity: Activity) -> HeightField:
    lanes = build_lanes(activity)
    nx = int(MAP_WIDTH / CELL) + 1
    ny = int(MAP_HEIGHT / CELL) + 1
    xs = np.linspace(0, MAP_WIDTH, nx)
    ys = np.linspace(0, MAP_HEIGHT, ny)
    spacing = MAP_HEIGHT / max(len(lanes), 1)
    week_w = MAP_WIDTH / N_WEEKS
    sx = HILL_SIGMA_WEEKS * week_w
    # With only a few lanes, spacing gets large; cap the hill width so a
    # lone repository still forms a mountain rather than a full-height wall.
    sy = HILL_SIGMA_LANE * min(spacing, MAX_LANE_SPACING)

    # 2. One Gaussian per repo-week. Separable, so build it as an outer
    #    product of an x-profile and a y-profile. Centres get a small seeded
    #    jitter so the lanes don't read as ruled lines.
    lane_raw = np.zeros((len(lanes), ny, nx))
    for li, lane in enumerate(lanes):
        rng = np.random.default_rng(_seed(activity.login, lane.key))
        jitter_x = rng.uniform(-0.3, 0.3, N_WEEKS) * week_w
        # The ridge line meanders smoothly across its lane instead of
        # running dead straight: a seeded random walk, low-pass filtered.
        walk = ndimage.gaussian_filter1d(rng.normal(size=N_WEEKS), 3.0, mode="reflect")
        jitter_y = walk / (np.abs(walk).max() or 1.0) * MEANDER * spacing
        for w in np.nonzero(lane.weekly)[0]:
            amp = np.log1p(lane.weekly[w])
            # Keep centres off the very edge so first/current-week hills
            # still have a summit inside the frame.
            cx = np.clip(week_x(w) + jitter_x[w], EDGE_MARGIN, MAP_WIDTH - EDGE_MARGIN)
            gx = np.exp(-0.5 * ((xs - cx) / sx) ** 2)
            gy = np.exp(-0.5 * ((ys - lane.y - jitter_y[w]) / sy) ** 2)
            lane_raw[li] += amp * np.outer(gy, gx)

    # 3. Domain warp: sample every lane through the same displacement field.
    #    Long wavelengths only: if the displacement changed faster than the
    #    distance it covers, the field would fold over and contours would
    #    grow cusps.
    warp_seed = _seed(activity.login, "warp")
    warp_octaves = ((4, 2, 1.0), (8, 4, 0.4))
    dx = (_fbm((ny, nx), warp_seed, warp_octaves) - 0.5) * 2 * WARP_AMPLITUDE / CELL
    dy = (_fbm((ny, nx), warp_seed + 1, warp_octaves) - 0.5) * 2 * WARP_AMPLITUDE / CELL
    # Fade the warp out towards the east and west edges, so hills in the
    # first and the current week stay inside the frame instead of being
    # pushed off the sheet.
    edge = np.minimum(xs, MAP_WIDTH - xs) / WARP_EDGE_FADE
    taper = np.clip(edge, 0, 1) ** 2 * (3 - 2 * np.clip(edge, 0, 1))   # smoothstep
    dx *= taper
    dy *= taper
    rows, cols = np.mgrid[0:ny, 0:nx].astype(float)
    coords = np.array([rows + dy, cols + dx])
    lane_z = np.stack([
        ndimage.map_coordinates(layer, coords, order=3, mode="nearest") for layer in lane_raw
    ]) if len(lanes) else np.zeros((0, ny, nx))

    # 4. Smooth away grid artefacts and normalise to the highest summit.
    act = lane_z.sum(axis=0) if len(lanes) else np.zeros((ny, nx))
    act = ndimage.gaussian_filter(act, SMOOTH_SIGMA)
    act = act / (act.max() or 1.0)

    # Terrain has hierarchy: peaks sit on broad massifs joined by saddles.
    # A heavily blurred copy of the field provides those shoulders, and a
    # mid-frequency relief term roughens the flanks into spurs and gullies.
    massif = ndimage.gaussian_filter(act, MASSIF_SIGMA / CELL)
    massif = massif / (massif.max() or 1.0)
    act = (1 - MASSIF_WEIGHT) * act + MASSIF_WEIGHT * massif
    relief = _fbm((ny, nx), _seed(activity.login, "relief"), octaves=((16, 7, 1.0), (32, 13, 0.5)))
    act = ndimage.gaussian_filter(act * (1 - RELIEF + 2 * RELIEF * relief), SMOOTH_SIGMA / 2)
    act = act / (act.max() or 1.0)
    # Lift the foothills: more contour lines on the lower slopes and less
    # crowding near the summits, the way real mountains are shaped. A log
    # curve rather than a power: its slope at zero is finite, so microscopic
    # ripples on flat ground are not blown up into wiggly contours.
    act = np.log1p(FOOTHILL_LIFT * np.clip(act, 0, None)) / np.log1p(FOOTHILL_LIFT)

    # 5. A gentle seeded floor, mostly long wavelengths.
    base = _fbm((ny, nx), _seed(activity.login, "base"), octaves=((4, 2, 1.0), (9, 4, 0.45), (18, 8, 0.15)))
    z = BASE_AMPLITUDE * base + (1 - BASE_AMPLITUDE) * act
    z = ndimage.gaussian_filter(z, FINAL_SIGMA)
    z = (z - z.min()) / (z.max() - z.min())
    return HeightField(z=z, lanes=lanes, lane_z=lane_z)
