"""Keep the map key in README.md in step with the numbered summits.

The key lives between two HTML comments, so the rest of the README is never
touched. GitHub renders README images as plain <img> tags (no hover, no
links inside the picture), so the key is where the map says what is what.
"""

from __future__ import annotations

import re
from pathlib import Path

from .labels import PlacedLabel

START = "<!-- terrain-key:start -->"
END = "<!-- terrain-key:end -->"
DESCRIPTION_MAX = 70


def _cell(text: str) -> str:
    """Make free text safe inside a Markdown table cell."""
    text = " ".join(text.split()).replace("|", "\\|")
    for ch in "<>":
        text = text.replace(ch, "")
    return text


def _short(text: str) -> str:
    """First clause of a description, at most DESCRIPTION_MAX characters."""
    for sep in (": ", " - ", " — ", ". "):
        head = text.split(sep, 1)[0]
        if 20 <= len(head) < len(text):
            text = head
            break
    if len(text) <= DESCRIPTION_MAX:
        return text
    return text[: DESCRIPTION_MAX - 1].rsplit(" ", 1)[0].rstrip(",.;:-") + "…"


def key_markdown(labels: list[PlacedLabel]) -> str:
    rows = ["| | Summit | Busiest week |", "|:-:|---|---|"]
    for lb in labels:
        if lb.name:
            name = f"[{_cell(lb.name)}]({lb.url})" if lb.url else f"**{_cell(lb.name)}**"
            what = f"{name} · {_cell(_short(lb.description))}" if lb.description else name
            unit = "commit" if lb.count == 1 else "commits"
        else:
            what = ("Private repositories, reviews and pull requests" if lb.summit.lane.key == "other"
                    else "A private repository")
            unit = "contribution" if lb.count == 1 else "contributions"
        rows.append(f"| {lb.ref} | {what} | {lb.month} · {lb.count} {unit} |")
    return "\n".join(rows)


def update_readme(path: Path, labels: list[PlacedLabel]) -> str | None:
    """Return the README text with a fresh key, or None if it has no key
    markers (then the README is left alone)."""
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        return None
    block = f"{START}\n{key_markdown(labels)}\n{END}"
    return pattern.sub(lambda _: block, text, count=1)
