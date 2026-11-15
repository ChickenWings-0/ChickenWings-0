#!/usr/bin/env python3
"""Generate the topographic contour map for the GitHub profile README.

Usage:
    python scripts/generate_terrain.py            # real data -> assets/terrain-{light,dark}.svg
    python scripts/generate_terrain.py --mock     # fake data -> build/mock/, no token needed

Real runs need GH_TOKEN or GITHUB_TOKEN. If fetching fails, the existing
SVGs are left untouched and the script exits 0 with a warning, so the
scheduled workflow simply finds nothing to commit.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

from terrain.contours import extract_contours
from terrain.data import Activity
from terrain.fetch import FetchError, fetch_activity, token_from_env
from terrain.field import build_field, find_summits
from terrain.labels import place_labels
from terrain.mock import mock_activity
from terrain.render import DARK, LIGHT, render_svg

DEFAULT_USER = "ChickenWings-0"
ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 300 * 1024


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--user", default=DEFAULT_USER, help="GitHub login (default: %(default)s)")
    p.add_argument("--mock", action="store_true", help="use deterministic fake data instead of the API")
    p.add_argument("--from-data", metavar="JSON", help="render from a JSON file written by --dump-data")
    p.add_argument("--dump-data", metavar="JSON", help="also write the activity data to this JSON file")
    p.add_argument("--out-dir", type=Path,
                   help="where to write the SVGs (default: assets/, or build/mock/ with --mock)")
    return p.parse_args(argv)


def load_activity(args: argparse.Namespace) -> Activity:
    if args.from_data:
        with open(args.from_data, encoding="utf-8") as fh:
            return Activity.from_json(fh.read())
    if args.mock:
        return mock_activity(args.user)
    token = token_from_env()
    if not token:
        raise FetchError("no token: set GH_TOKEN or GITHUB_TOKEN, or use --mock")
    return fetch_activity(args.user, token)


def render_both(activity: Activity) -> dict[str, str]:
    """The whole pipeline: field -> contours -> labels -> one SVG per theme."""
    field = build_field(activity)
    levels = extract_contours(field)
    labels = place_labels(field, find_summits(field, activity), activity)
    return {theme.name: render_svg(field, levels, labels, activity, theme) for theme in (LIGHT, DARK)}


def validate(svg: str, name: str) -> None:
    """Refuse to write anything that isn't well-formed or is too big."""
    ElementTree.fromstring(svg)
    size = len(svg.encode("utf-8"))
    if size > MAX_BYTES:
        raise ValueError(f"{name} is {size / 1024:.0f} KB, over the {MAX_BYTES // 1024} KB budget")


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def warn(message: str) -> None:
    """Print a warning, as an annotation when running in GitHub Actions."""
    print(f"::warning::{message}", file=sys.stderr)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        activity = load_activity(args)
    except FetchError as exc:
        # The existing SVGs stay untouched, so the workflow has nothing to commit.
        warn(f"could not fetch activity, keeping the existing map: {exc}")
        return 0

    if args.dump_data:
        Path(args.dump_data).write_text(activity.to_json(), encoding="utf-8")

    out_dir = args.out_dir or (ROOT / "build" / "mock" if args.mock else ROOT / "assets")
    svgs = render_both(activity)
    # Validate both before replacing either, so the pair never goes out of sync.
    for name, svg in svgs.items():
        validate(svg, name)
    for name, svg in svgs.items():
        path = out_dir / f"terrain-{name}.svg"
        write_atomic(path, svg)
        print(f"wrote {path} ({len(svg.encode('utf-8')) / 1024:.0f} KB)")

    print(f"{activity.login}: {activity.total} contributions across {len(activity.repos)} repositories, "
          f"{activity.start} to {activity.end}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
