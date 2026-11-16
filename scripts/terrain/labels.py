"""Choose which summits to mark and number them for the README key.

The map itself carries no project names. Each chosen summit gets a small
numbered marker; the README lists what each number is (see readme.py).
Markers are placed greedily, highest summit first. A summit near the frame
gets its marker nudged inwards a little; one that would still leave the
frame or touch another marker is dropped, so markers never overlap.
Numbers then run west to east, so the key reads in time order.
"""

from __future__ import annotations

from dataclasses import dataclass

from .data import Activity
from .field import HeightField, Summit

MARKER_R = 10.0            # radius of the numbered summit marker
MARKER_SIZE = 12.0         # font size of the number inside it
KNOCKOUT_R = 14.0          # contours are cleared this far around a marker

MAX_NAMED = 5              # summits of distinct public repositories
MAX_UNNAMED = 2            # summits of private / merged work
UNNAMED_MIN_HEIGHT = 0.5   # unnamed markers only on prominent summits
MIN_SPACING = 40.0         # minimum distance between two markers
FRAME_INSET = 4.0          # keep markers this far inside the neatline
MAX_NUDGE = 10.0           # a marker may sit this far off its summit to stay inside


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    def inside(self, width: float, height: float, inset: float = FRAME_INSET) -> bool:
        return self.x0 >= inset and self.y0 >= inset and self.x1 <= width - inset and self.y1 <= height - inset


@dataclass
class PlacedLabel:
    summit: Summit
    x: float = 0.0                 # marker centre, normally the summit itself
    y: float = 0.0
    ref: int = 0                   # number on the map and in the key, 1-based
    name: str | None = None        # repository name, None for private / merged work
    url: str | None = None         # link for the key (public repositories only)
    description: str = ""
    month: str = ""                # e.g. "Apr 2026", the busiest week near the summit
    count: int = 0                 # contributions in that week

    @property
    def box(self) -> Box:
        return Box(self.x - MARKER_R, self.y - MARKER_R, self.x + MARKER_R, self.y + MARKER_R)


def choose_summits(summits: list[Summit]) -> tuple[list[Summit], list[Summit]]:
    """Highest summit of up to MAX_NAMED distinct named repos, plus a few
    prominent unnamed ones (private repos, the merged lane)."""
    named: list[Summit] = []
    seen: set[str] = set()
    for s in summits:
        if s.lane.label and s.lane.key not in seen and len(named) < MAX_NAMED:
            named.append(s)
            seen.add(s.lane.key)
    unnamed = []
    for s in summits:
        if len(unnamed) >= MAX_UNNAMED:
            break
        if s in named or s.height < UNNAMED_MIN_HEIGHT:
            continue
        if any((s.x - o.x) ** 2 + (s.y - o.y) ** 2 < 70 ** 2 for o in named + unnamed):
            continue
        unnamed.append(s)
    return named, unnamed


def place_labels(field: HeightField, summits: list[Summit], activity: Activity) -> list[PlacedLabel]:
    named, unnamed = choose_summits(summits)
    repos = {r.full_name: r for r in activity.repos}

    placed: list[PlacedLabel] = []
    for s in named + unnamed:
        lo = FRAME_INSET + MARKER_R
        x = min(max(s.x, lo), field.width - lo)
        y = min(max(s.y, lo), field.height - lo)
        if abs(x - s.x) > MAX_NUDGE or abs(y - s.y) > MAX_NUDGE:
            continue
        lb = PlacedLabel(summit=s, x=x, y=y)
        if any((x - o.x) ** 2 + (y - o.y) ** 2 < MIN_SPACING ** 2 for o in placed):
            continue
        if s in named:
            repo = repos.get(s.lane.key)
            lb.name = s.lane.label
            if repo and not repo.private:
                lb.url = f"https://github.com/{repo.full_name}"
                lb.description = repo.description
        lb.month = activity.week_start(s.week).strftime("%b %Y")
        lb.count = s.count
        placed.append(lb)

    placed.sort(key=lambda lb: (lb.x, lb.y))
    for i, lb in enumerate(placed, start=1):
        lb.ref = i
    return placed
