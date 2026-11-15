"""Deterministic, realistic-looking fake activity for --mock runs.

Each fake repository has one or two "project phases": a ramp-up, a busy
stretch and a taper. Weekly counts are Poisson-sampled from that curve with
a fixed seed, so --mock always produces the same map.
"""

from __future__ import annotations

from datetime import date

import numpy as np

from .data import N_WEEKS, Activity, Repo, window_for

MOCK_TODAY = date(2026, 11, 15)
MOCK_SEED = 20261115

# (name, private, [(centre week, width in weeks, peak commits/week), ...])
MOCK_REPOS = [
    ("maitri-core", False, [(30, 7, 34), (46, 4, 22)]),
    ("agent-runtime", False, [(40, 5, 28)]),
    ("rag-eval", False, [(14, 4, 19), (24, 3, 9)]),
    ("vision-pipeline", False, [(7, 5, 15)]),
    ("client-portal", True, [(20, 6, 16)]),
    ("llm-router", False, [(35, 3, 12)]),
    ("dotfiles", False, [(3, 2, 4), (27, 2, 3), (49, 2, 5)]),
    ("website", False, [(11, 2, 6), (44, 2, 7)]),
    ("notes", False, [(18, 8, 2)]),
    ("hackathon-bot", False, [(31, 1, 10)]),
]


def mock_activity(login: str = "ChickenWings-0") -> Activity:
    rng = np.random.default_rng(MOCK_SEED)
    start, end = window_for(MOCK_TODAY)
    weeks = np.arange(N_WEEKS)

    repos = []
    for name, private, phases in MOCK_REPOS:
        rate = np.zeros(N_WEEKS)
        for centre, width, peak in phases:
            # Asymmetric bump: quick ramp-up, slower taper.
            spread = np.where(weeks < centre, width * 0.7, width * 1.3)
            rate += peak * np.exp(-0.5 * ((weeks - centre) / spread) ** 2)
        # A little week-to-week texture, as real work has.
        rate *= rng.uniform(0.55, 1.35, N_WEEKS)
        weekly = rng.poisson(rate).astype(int).tolist()
        repos.append(Repo(name=name, full_name=f"{login}/{name}", private=private, weekly=weekly))

    # Reviews, issues and PRs: steady background with a mid-year bump.
    other_rate = 2.0 + 4.0 * np.exp(-0.5 * ((weeks - 33) / 9) ** 2)
    other_weekly = rng.poisson(other_rate).astype(int).tolist()

    repos.sort(key=lambda r: (-r.total, r.full_name))
    return Activity(login=login, start=start, end=end, repos=repos, other_weekly=other_weekly)
