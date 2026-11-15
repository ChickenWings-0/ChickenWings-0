#!/usr/bin/env python3
"""Generate the topographic contour map for the GitHub profile README.

Usage:
    python scripts/generate_terrain.py            # real data (needs GH_TOKEN or GITHUB_TOKEN)
    python scripts/generate_terrain.py --mock     # realistic fake data, no token needed
"""

from __future__ import annotations

import argparse
import sys

from terrain.data import Activity
from terrain.fetch import FetchError, fetch_activity, token_from_env
from terrain.mock import mock_activity

DEFAULT_USER = "ChickenWings-0"


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--user", default=DEFAULT_USER, help="GitHub login (default: %(default)s)")
    p.add_argument("--mock", action="store_true", help="use deterministic fake data instead of the API")
    p.add_argument("--from-data", metavar="JSON", help="render from a JSON file written by --dump-data")
    p.add_argument("--dump-data", metavar="JSON", help="also write the activity data to this JSON file")
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
        with open(args.dump_data, "w", encoding="utf-8") as fh:
            fh.write(activity.to_json())

    print(f"{activity.login}: {activity.total} contributions, "
          f"{len(activity.repos)} repositories, {activity.start} to {activity.end}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
