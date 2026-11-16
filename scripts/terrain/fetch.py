"""Fetch the last 52 weeks of contributions from the GitHub GraphQL API.

Two kinds of query are combined:

* ``contributionCalendar``: daily totals for the whole window. This is the
  same number the green squares on a profile show, including restricted
  (private) contributions and non-commit work such as PRs and reviews.
* ``commitContributionsByRepository``: per-repository commit counts by day.
  A repository's ``contributions`` connection is paginated and can't be
  paged independently inside the list, so the window is split into 13-week
  chunks. A chunk has at most 91 days, so ``first: 100`` always holds them.

Whatever the calendar counts beyond the per-repo commits becomes the
"other" lane of the map. That includes commits to the profile repository
itself (the one named after the user, or the one running the workflow):
it would otherwise turn up as a summit on its own map.
"""

from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone

import requests

from .data import N_WEEKS, Activity, Repo, week_index, window_for

API_URL = "https://api.github.com/graphql"
CHUNK_WEEKS = 13
MAX_ATTEMPTS = 3

CALENDAR_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar {
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""

REPOS_QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      commitContributionsByRepository(maxRepositories: 100) {
        repository { name nameWithOwner isPrivate description }
        contributions(first: 100) {
          pageInfo { hasNextPage }
          nodes { occurredAt commitCount }
        }
      }
    }
  }
}
"""


class FetchError(RuntimeError):
    """Raised when GitHub data can't be fetched or looks wrong."""


def token_from_env() -> str | None:
    """Local runs use GH_TOKEN or GITHUB_TOKEN. In Actions the workflow sets
    GH_TOKEN from the PROFILE_TOKEN secret, or from GITHUB_TOKEN if that
    secret is missing."""
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    return None


def _graphql(session: requests.Session, query: str, variables: dict) -> dict:
    """POST one query with retries on transient failures."""
    last_error = "unknown error"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = session.post(API_URL, json={"query": query, "variables": variables}, timeout=30)
        except requests.RequestException as exc:
            last_error = f"network error: {exc}"
        else:
            # One line, so it reads cleanly as a GitHub Actions annotation.
            body = " ".join(resp.text.split())[:200]
            if resp.status_code == 200:
                try:
                    payload = resp.json()
                except ValueError:
                    payload = None
                    last_error = f"malformed JSON response: {body}"
                if payload is not None:
                    if payload.get("errors"):
                        messages = "; ".join(e.get("message", "?") for e in payload["errors"])
                        raise FetchError(f"GraphQL error: {messages}")
                    if not payload.get("data") or payload["data"].get("user") is None:
                        raise FetchError("GraphQL response has no user data")
                    return payload["data"]
            elif resp.status_code in (401, 403) and "rate limit" not in body.lower():
                raise FetchError(f"HTTP {resp.status_code}: check the token ({body})")
            else:
                last_error = f"HTTP {resp.status_code}: {body}"
        if attempt < MAX_ATTEMPTS:
            time.sleep(2 ** attempt)
    raise FetchError(f"giving up after {MAX_ATTEMPTS} attempts ({last_error})")


def _is_profile_repo(login: str, info: dict) -> bool:
    name, full = info["name"], info["nameWithOwner"]
    return name.lower() == login.lower() or full.lower() == os.environ.get("GITHUB_REPOSITORY", "").lower()


def _iso(day: date, end_of_day: bool = False) -> str:
    moment = datetime.combine(day, datetime.max.time() if end_of_day else datetime.min.time())
    return moment.replace(microsecond=0, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_activity(login: str, token: str, today: date | None = None) -> Activity:
    """Download and aggregate weekly activity for `login`."""
    today = today or datetime.now(timezone.utc).date()
    start, end = window_for(today)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"bearer {token}",
        "User-Agent": "terrain-map-generator",
    })

    # 1) Calendar: weekly totals over the whole window.
    data = _graphql(session, CALENDAR_QUERY, {"login": login, "from": _iso(start), "to": _iso(end, True)})
    calendar = data["user"]["contributionsCollection"]["contributionCalendar"]
    calendar_weekly = [0] * N_WEEKS
    for week in calendar["weeks"]:
        for day in week["contributionDays"]:
            idx = week_index(start, date.fromisoformat(day["date"]))
            if idx is not None:
                calendar_weekly[idx] += day["contributionCount"]

    # 2) Commits per repository, chunk by chunk.
    repos: dict[str, Repo] = {}
    for chunk_start_week in range(0, N_WEEKS, CHUNK_WEEKS):
        c_start = start + timedelta(weeks=chunk_start_week)
        c_end = min(end, c_start + timedelta(weeks=CHUNK_WEEKS, days=-1))
        if c_start > end:
            break
        data = _graphql(session, REPOS_QUERY, {"login": login, "from": _iso(c_start), "to": _iso(c_end, True)})
        for entry in data["user"]["contributionsCollection"]["commitContributionsByRepository"]:
            info = entry["repository"]
            if _is_profile_repo(login, info):
                continue
            conn = entry["contributions"]
            if conn["pageInfo"]["hasNextPage"]:
                raise FetchError(f"unexpected pagination for {info['nameWithOwner']}")
            repo = repos.setdefault(info["nameWithOwner"], Repo(
                name=info["name"], full_name=info["nameWithOwner"],
                private=bool(info["isPrivate"]), weekly=[0] * N_WEEKS,
                description=(info.get("description") or "").strip(),
            ))
            for node in conn["nodes"]:
                # occurredAt is midnight (US Pacific) of the contribution day
                # expressed in UTC, so the date part is the calendar day.
                idx = week_index(start, date.fromisoformat(node["occurredAt"][:10]))
                if idx is not None:
                    repo.weekly[idx] += node["commitCount"]

    # 3) Whatever the calendar counts beyond listed commits goes to "other".
    #    Clamped at zero: the calendar uses the user's time zone while commit
    #    days use Pacific time, so a commit can land one week apart.
    other_weekly = [
        max(0, calendar_weekly[w] - sum(r.weekly[w] for r in repos.values()))
        for w in range(N_WEEKS)
    ]

    activity = Activity(
        login=login, start=start, end=end,
        repos=sorted((r for r in repos.values() if r.total > 0), key=lambda r: (-r.total, r.full_name)),
        other_weekly=other_weekly,
    )
    if activity.total == 0:
        raise FetchError("API returned zero contributions for the window; refusing to draw a blank map")
    return activity
