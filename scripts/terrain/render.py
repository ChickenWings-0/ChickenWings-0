"""Render contours, summits and map furniture as a self-contained SVG.

No external fonts, images or scripts: one system monospace stack, and a single
optional CSS animation (contours draw in once, markers fade in after), which
is switched off for prefers-reduced-motion. Without CSS animation support
the SVG shows its final state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from xml.sax.saxutils import escape

from .contours import N_LEVELS, ContourLevel
from .data import N_WEEKS, Activity
from .field import HeightField
from .labels import KNOCKOUT_R, MARKER_R, MARKER_SIZE, PlacedLabel

# Sheet geometry, SVG user units.
SHEET_W, SHEET_H = 1200, 594
FRAME_X, FRAME_Y = 40, 48           # top-left corner of the map neatline

# Fake but internally consistent coordinates: 22' of longitude at ~47°N,
# with the latitude span matching the frame's aspect ratio (about 6').
LON_W, LON_E = (8, 30), (8, 52)
LAT_N, LAT_S = (47, 12), (47, 6)

# Monospace throughout, like a plotter-drawn survey sheet. Every platform
# has one, and fixed advance widths keep the marginalia layout predictable.
MONO = ('ui-monospace,"SF Mono",SFMono-Regular,"Cascadia Mono","Segoe UI Mono",'
        'Menlo,Consolas,"DejaVu Sans Mono","Liberation Mono",monospace')


@dataclass(frozen=True)
class Theme:
    name: str
    paper: str          # background
    ink: str            # frame, labels, marginalia
    muted: str          # secondary text
    contour: str        # intermediate + supplementary contours
    index: str          # index contours
    grid: str           # graticule
    grid_opacity: float
    contour_width: float
    index_width: float
    glow: bool


LIGHT = Theme(
    name="light", paper="#f4efe4", ink="#2e2a24", muted="#776c5f",
    contour="#a9774c", index="#7d5231", grid="#5d84a3", grid_opacity=0.28,
    contour_width=0.6, index_width=1.25, glow=False,
)
DARK = Theme(
    name="dark", paper="#0c0f13", ink="#d6dedc", muted="#7c8a88",
    contour="#5fc4b4", index="#a6f2e3", grid="#5fc4b4", grid_opacity=0.14,
    contour_width=0.6, index_width=1.1, glow=True,
)


def _f(v: float) -> str:
    """Compact number for attributes."""
    return f"{v:.1f}".rstrip("0").rstrip(".")


def _dms(deg_min: tuple[int, int], hemi: str) -> str:
    return f"{deg_min[0]}°{deg_min[1]:02d}′{hemi}"


# -- pieces -----------------------------------------------------------------------

def _style(t: Theme) -> str:
    return f"""
text{{font-family:{MONO};fill:{t.ink}}}
.muted{{fill:{t.muted}}}
.caps{{text-transform:uppercase;letter-spacing:.08em}}
.mk circle{{fill:{t.paper};stroke:{t.ink};stroke-width:1px}}
.mk text{{font-weight:600;text-anchor:middle}}
.c path{{fill:none;stroke-linecap:round;stroke-linejoin:round}}
.n path{{stroke:{t.contour};stroke-width:{t.contour_width}px}}
.i path{{stroke:{t.index};stroke-width:{t.index_width}px}}
.s path{{stroke:{t.contour};stroke-width:{t.contour_width * 0.85:.2f}px;stroke-dasharray:3 3.5;opacity:.85}}
.g{{stroke:{t.grid};stroke-width:.5px;opacity:{t.grid_opacity};fill:none}}
@keyframes draw{{from{{stroke-dasharray:1 2;stroke-dashoffset:1.01}}to{{stroke-dasharray:1 2;stroke-dashoffset:0}}}}
@keyframes fade{{from{{opacity:0}}}}
.n path,.i path{{animation:draw 1.8s cubic-bezier(.45,0,.2,1) var(--d) both}}
.s{{animation:fade 1.4s ease .2s both}}
.lb{{animation:fade 1s ease 2.6s both}}
.k path,.k{{animation:none}}
@media (prefers-reduced-motion:reduce){{.n path,.i path,.s,.lb{{animation:none}}}}
""".strip()


def _knockout(labels: list[PlacedLabel], width: float, height: float) -> str:
    """Mask that clears contours and grid in a small disc around every
    marker, the way printed maps knock lines out behind their symbols."""
    discs = "".join(f'<circle cx="{_f(lb.x)}" cy="{_f(lb.y)}" r="{KNOCKOUT_R}"/>' for lb in labels)
    return (f'<mask id="ko" maskUnits="userSpaceOnUse" x="-10" y="-10" width="{_f(width + 20)}" height="{_f(height + 20)}">'
            f'<rect x="-10" y="-10" width="{_f(width + 20)}" height="{_f(height + 20)}" fill="#fff"/>'
            f'{discs}</mask>')


def _contours(levels: list[ContourLevel], t: Theme) -> str:
    glow = ' filter="url(#glow)"' if t.glow else ""
    out = [f'<g class="c"{glow}>']
    for lv in levels:
        cls = {"index": "i", "intermediate": "n", "supplementary": "s"}[lv.kind]
        # Low contours draw first, so the terrain appears to rise.
        delay = 0.1 + lv.number * 0.045
        attrs = "" if cls == "s" else ' pathLength="1"'
        style = "" if cls == "s" else f' style="--d:{delay:.2f}s"'
        out.append(f'<g class="{cls}"{style}>')
        out += [f'<path{attrs} d="{d}"/>' for d in lv.paths]
        out.append("</g>")
    out.append("</g>")
    return "\n".join(out)


def _month_starts(activity: Activity, width: float) -> list[tuple[date, float]]:
    """(first-of-month, x) for every month boundary inside the window."""
    week_w = width / N_WEEKS
    day = date(activity.start.year, activity.start.month, 1)
    out = []
    while day <= activity.end:
        if day > activity.start:
            out.append((day, (day - activity.start).days / 7 * week_w))
        day = (day + timedelta(days=32)).replace(day=1)
    return out


def _grid_and_ticks(field: HeightField, activity: Activity) -> tuple[str, str]:
    """Faint graticule (inside the map) and month ticks/labels on the frame."""
    W, H = field.width, field.height
    months = _month_starts(activity, W)
    grid = [f'<path class="g" d="{"".join(f"M{_f(x)} 0V{_f(H)}" for _, x in months)}'
            f'{"".join(f"M0 {_f(H * k / 4)}H{_f(W)}" for k in (1, 2, 3))}"/>']

    ticks = []
    for _, x in months:
        ticks.append(f"M{_f(x)} 0v5M{_f(x)} {_f(H)}v-5")
    labels = []
    edges = [0.0] + [x for _, x in months] + [W]
    names = [activity.start] + [d for d, _ in months]
    for (a, b), d in zip(zip(edges, edges[1:]), names):
        if b - a < 40:      # too narrow a sliver of a month to name
            continue
        text = d.strftime("%b").upper()
        if d.month == 1 or d == names[1]:
            text += f" ’{d.strftime('%y')}"
        labels.append(f'<text x="{_f((a + b) / 2)}" y="{_f(H + 17)}" font-size="9.5" '
                      f'class="caps muted" text-anchor="middle">{text}</text>')
    tick_path = f'<path d="{"".join(ticks)}" stroke="currentColor" stroke-width=".8" fill="none"/>'
    return "\n".join(grid), tick_path + "\n" + "\n".join(labels)


def _marker(x: float, y: float, ref: int) -> str:
    """Numbered summit marker: a small ring with the key number inside."""
    return (f'<g class="mk"><circle cx="{_f(x)}" cy="{_f(y)}" r="{MARKER_R}"/>'
            f'<text x="{_f(x)}" y="{_f(y + MARKER_SIZE * 0.35)}" font-size="{MARKER_SIZE}">{ref}</text></g>')


def _swatch(x: float, y: float, cls: str) -> str:
    """A short, static sample of a contour line style for the legend."""
    return f'<g class="c {cls} k"><path d="M{_f(x)} {_f(y)}h26"/></g>'


def _markers(labels: list[PlacedLabel]) -> str:
    return '<g class="lb">' + "".join(_marker(lb.x, lb.y, lb.ref) for lb in labels) + "</g>"


def _coordinates(field: HeightField) -> str:
    W, H = field.width, field.height
    x0, y0 = FRAME_X, FRAME_Y
    x1, y1 = x0 + W, y0 + H
    return "\n".join([
        f'<text x="{x0}" y="{y0 - 11}" font-size="10" class="muted">{_dms(LON_W, "E")}</text>',
        f'<text x="{x1}" y="{y0 - 11}" font-size="10" class="muted" text-anchor="end">{_dms(LON_E, "E")}</text>',
        f'<text transform="translate({x0 - 11} {y0}) rotate(-90)" font-size="10" class="muted" '
        f'text-anchor="end">{_dms(LAT_N, "N")}</text>',
        f'<text transform="translate({x1 + 11} {y1}) rotate(90)" font-size="10" class="muted" '
        f'text-anchor="end">{_dms(LAT_S, "N")}</text>',
    ])


def _marginalia(field: HeightField, activity: Activity, t: Theme) -> str:
    """Title block, legend, scale bar and north arrow under the map."""
    x0 = FRAME_X
    top = FRAME_Y + field.height + 44
    week_w = field.width / N_WEEKS
    out = []

    # Title block.
    out.append(f'<text x="{x0}" y="{top}" font-size="15" font-weight="600" class="caps">{escape(activity.login)}</text>')
    out.append(f'<text x="{x0}" y="{top + 20}" font-size="10.5" class="muted">Activity survey, 52 weeks to '
               f'{activity.end.day} {activity.end.strftime("%b %Y")}</text>')
    out.append(f'<text x="{x0}" y="{top + 37}" font-size="10.5" class="muted">Relief: contributions per week, log scale</text>')

    # Legend, two columns: line samples and the spot-height symbol.
    lx, ly, col = x0 + 292, top - 4, 262
    rows = [
        (lx, ly, _swatch(lx, ly, "i"), "Index contour"),
        (lx, ly + 24, _swatch(lx, ly + 24, "n"), f"Contour, interval 1/{N_LEVELS} of relief"),
        (lx + col, ly, _swatch(lx + col, ly, "s"), "Supplementary contour"),
        (lx + col, ly + 24, _marker(lx + col + 13, ly + 24, 1), "Summit, see key"),
    ]
    for x, y, sample, text in rows:
        out.append(sample)
        out.append(f'<text x="{_f(x + 40)}" y="{_f(y + 4)}" font-size="10.5">{text}</text>')

    # Scale bar: 12 weeks in alternating 4-week blocks.
    sx, sy = x0 + 760, top - 8
    segs = []
    for k in range(3):
        fill = t.ink if k % 2 == 0 else "none"
        segs.append(f'<rect x="{_f(sx + k * 4 * week_w)}" y="{sy}" width="{_f(4 * week_w)}" height="5" '
                    f'fill="{fill}" stroke="{t.ink}" stroke-width=".7"/>')
    for k, label in enumerate(("0", "4", "8", "12 weeks")):
        anchor = "start" if k == 3 else "middle"
        x = sx + k * 4 * week_w - (3 if k == 3 else 0)
        out.append(f'<text x="{_f(x)}" y="{sy + 20}" font-size="10.5" class="muted" text-anchor="{anchor}">{label}</text>')
    out += segs

    # North arrow: half-filled head on a hairline, N above.
    nx, ny = x0 + field.width - 14, top - 18
    out.append(f'<text x="{nx}" y="{ny}" font-size="12" font-weight="600" text-anchor="middle">N</text>')
    out.append(f'<path d="M{nx} {ny + 5}l-6 20 6-5z" fill="{t.ink}"/>')
    out.append(f'<path d="M{nx} {ny + 5}l6 20-6-5z" fill="none" stroke="{t.ink}" stroke-width=".8" stroke-linejoin="round"/>')
    out.append(f'<path d="M{nx} {ny + 20}v18" stroke="{t.ink}" stroke-width=".8"/>')
    return "\n".join(out)


# -- whole sheet -----------------------------------------------------------------

def render_svg(field: HeightField, levels: list[ContourLevel], labels: list[PlacedLabel],
               activity: Activity, theme: Theme) -> str:
    t = theme
    W, H = field.width, field.height
    grid, ticks = _grid_and_ticks(field, activity)
    named = ", ".join(f"{lb.ref} {lb.name} ({lb.month})" for lb in labels if lb.name)
    title = f"{activity.login}: GitHub activity as a topographic map"
    desc = (f"Contour map of {activity.total} contributions over the 52 weeks to {activity.end.isoformat()}. "
            f"Time runs left to right, each ridge is a repository, height is contributions per week on a log scale."
            + (f" Numbered summits: {named}." if named else ""))

    glow = ('<filter id="glow" x="-5%" y="-5%" width="110%" height="110%">'
            '<feGaussianBlur stdDeviation="1.8" result="b"/>'
            '<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>') if t.glow else ""

    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SHEET_W} {SHEET_H}" width="{SHEET_W}" height="{SHEET_H}" role="img" aria-labelledby="title desc" color="{t.ink}">
<title id="title">{escape(title)}</title>
<desc id="desc">{escape(desc)}</desc>
<style>
{_style(t)}
</style>
<defs><clipPath id="clip"><rect width="{_f(W)}" height="{_f(H)}"/></clipPath>{glow}
{_knockout(labels, W, H)}</defs>
<rect width="{SHEET_W}" height="{SHEET_H}" fill="{t.paper}"/>
<g transform="translate({FRAME_X} {FRAME_Y})">
<g clip-path="url(#clip)"><g mask="url(#ko)">
{grid}
{_contours(levels, t)}
</g></g>
{_markers(labels)}
<rect width="{_f(W)}" height="{_f(H)}" fill="none" stroke="{t.ink}" stroke-width="1.1"/>
<rect x="-5" y="-5" width="{_f(W + 10)}" height="{_f(H + 10)}" fill="none" stroke="{t.ink}" stroke-width=".5"/>
{ticks}
</g>
{_coordinates(field)}
{_marginalia(field, activity, t)}
</svg>
"""
