"""Shared data model: weekly activity per repository over a fixed window."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

N_WEEKS = 52


@dataclass
class Repo:
    """Weekly commit counts for one repository."""

    name: str            # short name shown on the map, e.g. "SatQuery-AI"
    full_name: str       # owner/name, used for stable ordering and hashing
    private: bool        # private repositories are never named on the map
    weekly: list[int]    # commits per week, len == N_WEEKS
    description: str = ""  # GitHub repository description, used in the README key

    @property
    def total(self) -> int:
        return sum(self.weekly)


@dataclass
class Activity:
    """Everything the terrain needs: who, when, and how much per week."""

    login: str
    start: date                      # Sunday that opens week 0
    end: date                        # last day covered (inclusive)
    repos: list[Repo]
    # Contributions the calendar counts that no listed repository explains:
    # pull requests, issues, reviews and restricted (private) contributions.
    other_weekly: list[int] = field(default_factory=lambda: [0] * N_WEEKS)

    @property
    def n_weeks(self) -> int:
        return N_WEEKS

    @property
    def total(self) -> int:
        return sum(r.total for r in self.repos) + sum(self.other_weekly)

    def week_start(self, week: int) -> date:
        return self.start + timedelta(weeks=week)

    # -- (de)serialisation, used by --dump-data / --from-data ---------------

    def to_json(self) -> str:
        payload = asdict(self)
        payload["start"] = self.start.isoformat()
        payload["end"] = self.end.isoformat()
        return json.dumps(payload, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "Activity":
        raw = json.loads(text)
        return cls(
            login=raw["login"],
            start=date.fromisoformat(raw["start"]),
            end=date.fromisoformat(raw["end"]),
            repos=[Repo(**r) for r in raw["repos"]],
            other_weekly=list(raw["other_weekly"]),
        )


def window_for(today: date) -> tuple[date, date]:
    """Return (start, end) for the 52 calendar weeks ending with `today`.

    Weeks run Sunday..Saturday like GitHub's contribution calendar; the last
    week is the current, possibly partial, one.
    """
    days_since_sunday = (today.weekday() + 1) % 7
    this_sunday = today - timedelta(days=days_since_sunday)
    start = this_sunday - timedelta(weeks=N_WEEKS - 1)
    return start, today


def week_index(start: date, day: date) -> int | None:
    """Week number of `day` inside the window, or None if outside."""
    idx = (day - start).days // 7
    return idx if 0 <= idx < N_WEEKS else None
