"""Choose which summits to label and place labels without collisions.

SVG has no text measurement without a browser, so widths are estimated
from per-character tables with a safety margin. Placement is greedy:
summits are handled highest first, each label tries a ranked list of
positions around its marker, and the first spot that stays inside the frame
and clear of every other label and marker wins. A summit with no free spot
is dropped entirely, so labels never overlap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .data import Activity
from .field import HeightField, Summit

# Type sizes in SVG user units, shared with render.py.
NAME_SIZE = 15.0           # summit name, serif italic
MONTH_SIZE = 8.5           # month under the name, sans caps, letter-spaced
MONTH_TRACKING = 0.14      # letter-spacing of the month line, em
SPOT_SIZE = 10.5           # spot height number, sans

MAX_NAMED = 5
MAX_UNNAMED = 2
UNNAMED_MIN_HEIGHT = 0.5   # unnamed spot heights only on prominent summits
FRAME_INSET = 7.0          # keep labels this far inside the neatline
PAD = 4.0                  # minimum gap between any two boxes
NAME_MAX_CHARS = 20

# Approximate advance widths in em, generous for Georgia / DejaVu Serif and
# Segoe UI / Helvetica / DejaVu Sans, the fonts the stacks resolve to.
_SERIF_ITALIC = {"lower": 0.52, "upper": 0.70, "digit": 0.60, "narrow": 0.30, "dash": 0.40, "space": 0.27}
_SANS = {"lower": 0.55, "upper": 0.70, "digit": 0.60, "narrow": 0.30, "dash": 0.40, "space": 0.30}
_SAFETY = 1.08


def text_width(text: str, size: float, serif: bool = False, tracking: float = 0.0) -> float:
    table = _SERIF_ITALIC if serif else _SANS
    total = 0.0
    for ch in text:
        if ch in "ijlrtfI.,:;'!|":
            key = "narrow"
        elif ch in "-_":
            key = "dash"
        elif ch == " ":
            key = "space"
        elif ch.isdigit():
            key = "digit"
        elif ch.isupper() or ch in "mwMW":
            key = "upper"
        else:
            key = "lower"
        total += table[key] + tracking
    return total * size * _SAFETY


@dataclass
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    def overlaps(self, other: "Box", pad: float = PAD) -> bool:
        return not (self.x1 + pad <= other.x0 or other.x1 + pad <= self.x0 or
                    self.y1 + pad <= other.y0 or other.y1 + pad <= self.y0)

    def inside(self, width: float, height: float, inset: float = FRAME_INSET) -> bool:
        return self.x0 >= inset and self.y0 >= inset and self.x1 <= width - inset and self.y1 <= height - inset


@dataclass
class PlacedLabel:
    summit: Summit
    number: str                    # spot height text
    marker_box: Box                # triangle + number
    name: str | None = None        # repo name, None for unnamed summits
    month: str | None = None       # e.g. "APR 2026"
    anchor: str = "start"          # text-anchor of the name block
    text_x: float = 0.0            # anchor x of name and month lines
    name_y: float = 0.0            # baseline of the name
    month_y: float = 0.0           # baseline of the month line
    label_box: Box | None = None


def display_name(name: str) -> str:
    """Shorten long repository names at a word break: "Circuit-Debugger-
    Find-the-Fault" becomes "Circuit-Debugger"."""
    if len(name) <= NAME_MAX_CHARS:
        return name
    words = re.split(r"(?<=[-_ .])", name)
    short = ""
    for word in words:
        if len(short + word) > NAME_MAX_CHARS:
            break
        short += word
    short = short.rstrip("-_ .")
    return short if len(short) >= 4 else name[: NAME_MAX_CHARS - 1] + "…"


def _marker_box(s: Summit, number: str) -> Box:
    # Triangle centred on the summit, number set to its right (see render.py).
    w = text_width(number, SPOT_SIZE)
    return Box(s.x - 5, s.y - 6.5, s.x + 7 + w, s.y + 4.5)


def _candidates(s: Summit, marker: Box, w: float, h: float):
    """Yield (anchor, text_x, top) for the name block, best positions first."""
    cy = s.y - h / 2                       # block vertically centred on summit
    for gap in (6.0, 16.0, 28.0):
        yield "start", marker.x1 + gap, cy                       # right
        yield "middle", s.x, marker.y0 - gap - h                 # above
        yield "end", s.x - 7 - gap, cy                           # left
        yield "middle", s.x, marker.y1 + gap                     # below
        yield "start", s.x + 2 + gap, marker.y0 - gap - h        # upper right
        yield "end", s.x - 2 - gap, marker.y0 - gap - h          # upper left
        yield "start", s.x + 2 + gap, marker.y1 + gap            # lower right
        yield "end", s.x - 2 - gap, marker.y1 + gap              # lower left


def _block_box(anchor: str, text_x: float, top: float, w: float, h: float) -> Box:
    x0 = {"start": text_x, "middle": text_x - w / 2, "end": text_x - w}[anchor]
    return Box(x0, top, x0 + w, top + h)


def choose_summits(summits: list[Summit]) -> tuple[list[Summit], list[Summit]]:
    """Highest summit of up to MAX_NAMED distinct named repos, plus a few
    prominent unnamed spot heights (private repos, the merged lane)."""
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
    labels = [PlacedLabel(summit=s, number=str(s.count), marker_box=_marker_box(s, str(s.count)))
              for s in named + unnamed]
    # Markers must sit fully inside the frame; drop any that don't.
    labels = [lb for lb in labels if lb.marker_box.inside(field.width, field.height)]

    placed: list[PlacedLabel] = []
    for lb in labels:
        others = [o.marker_box for o in labels if o is not lb] + [o.label_box for o in placed if o.label_box]
        if any(lb.marker_box.overlaps(b) for b in others):
            continue
        if lb.summit.lane.label and lb.summit in named:
            name = display_name(lb.summit.lane.label)
            month = activity.week_start(lb.summit.week).strftime("%b %Y").upper()
            w = max(text_width(name, NAME_SIZE, serif=True), text_width(month, MONTH_SIZE, tracking=MONTH_TRACKING))
            # Name cap height ~0.72em above its baseline, descenders ~0.24em,
            # then the month line beneath.
            h = NAME_SIZE * 0.96 + 3.0 + MONTH_SIZE * 0.95
            for anchor, text_x, top in _candidates(lb.summit, lb.marker_box, w, h):
                box = _block_box(anchor, text_x, top, w, h)
                if not box.inside(field.width, field.height):
                    continue
                if box.overlaps(lb.marker_box) or any(box.overlaps(b) for b in others):
                    continue
                lb.name, lb.month, lb.anchor, lb.text_x = name, month, anchor, text_x
                lb.name_y = top + NAME_SIZE * 0.74
                lb.month_y = top + h - MONTH_SIZE * 0.2
                lb.label_box = box
                break
            else:
                continue          # nowhere to put it: drop the summit entirely
        placed.append(lb)

    # Unplaced markers were still treated as obstacles above, which is only
    # conservative. Final guard: nothing that survived may overlap.
    boxes = [b for lb in placed for b in (lb.marker_box, lb.label_box) if b]
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not a.overlaps(b, pad=0), "label placement produced an overlap"
    return placed
