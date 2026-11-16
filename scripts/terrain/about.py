"""The "about me" text that sits over the terrain, read from profile.toml.

A GitHub README can't put a background behind Markdown text, so the text is
drawn into the SVG itself, over a faded part of the map. The lettering is
monospace, so wrapping by character count is exact enough to lay out
without a browser.
"""

from __future__ import annotations

import textwrap
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Profile:
    greeting: str = ""
    name: str = ""
    tagline: str = ""
    about: list[str] = field(default_factory=list)
    site: str = ""

    def plain_text(self) -> str:
        """Everything on the sheet as one string, for the SVG <desc> and alt text."""
        parts = [f"{self.greeting} {self.name}.".strip(), self.tagline, *self.about, self.site]
        return " ".join(p for p in parts if p)


def load_profile(path: Path) -> Profile:
    """Read profile.toml; a missing file gives an empty profile (map only)."""
    if not path.exists():
        return Profile()
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)
    about = raw.get("about", [])
    if isinstance(about, str):
        about = [about]
    return Profile(
        greeting=str(raw.get("greeting", "")).strip(),
        name=str(raw.get("name", "")).strip(),
        tagline=str(raw.get("tagline", "")).strip(),
        about=[" ".join(str(p).split()) for p in about if str(p).strip()],
        site=str(raw.get("site", "")).strip(),
    )


def wrap(text: str, chars: int) -> list[str]:
    return textwrap.wrap(text, width=chars, break_long_words=False, break_on_hyphens=False)
