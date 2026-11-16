# Terrain map generator

`generate_terrain.py` turns the last 52 weeks of GitHub activity into a
topographic contour map and writes `assets/terrain-light.svg` and
`assets/terrain-dark.svg`, which the profile README embeds as the header
image. The intro text in that image ("Hi, I'm ...") comes from
[`profile.toml`](../profile.toml): edit it there, not in the SVGs.

## How the map is built

| Stage | Module | What it does |
| --- | --- | --- |
| fetch | `terrain/fetch.py` | GraphQL `contributionCalendar` (weekly totals) and `commitContributionsByRepository` (commits per repo per day, queried in 13-week chunks). Calendar contributions not explained by repo commits (PRs, issues, reviews, restricted private work) go into an "other" lane, as do commits to the profile repository itself. |
| field | `terrain/field.py` | One lane per top-7 repository plus one merged lane, ordered from the centre outwards. Each repo-week adds a Gaussian hill weighted by `log1p(commits)`. A seeded domain warp, a broad massif layer and a low seeded noise floor make it read as terrain. All randomness is seeded from the login, so identical data gives an identical map. |
| contours | `terrain/contours.py` | Marching squares (`skimage.measure.find_contours`) per level, vertex smoothing, Douglas-Peucker simplification and a chord-length Hermite spline written as compact relative cubic Beziers. Every 5th line is an index contour; dashed half-interval lines fill the flat ground. |
| about | `terrain/about.py` | Reads `profile.toml` and wraps the paragraphs. GitHub can't put a background behind README text, so the intro is drawn into the SVG over the western part of the map, where the contours fade out. The lettering is monospace, so wrapping by character count is exact. Text that would run off the map stops the run with an error. |
| render | `terrain/render.py` | Self-contained SVG: one system monospace stack, the intro text, a month graticule, a legend, a scale bar, a north arrow and corner coordinates. Contours draw in once with CSS (disabled for `prefers-reduced-motion`). |

## Running it

```sh
python -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip

# Real data: reads GH_TOKEN, falling back to GITHUB_TOKEN
GH_TOKEN=... python scripts/generate_terrain.py

# No token needed: deterministic fake data, written to build/mock/
python scripts/generate_terrain.py --mock

# Save fetched data, or re-render from a saved file without hitting the API
python scripts/generate_terrain.py --dump-data build/activity.json
python scripts/generate_terrain.py --from-data build/activity.json --out-dir build/preview
```

If fetching fails (no token, bad token, API error), the script prints a
warning, leaves the existing SVGs untouched and exits 0. Both SVGs are
checked for well-formed XML and a 300 KB size budget before either file is
replaced, and each write is atomic.

## Token and scopes

The workflow uses the `PROFILE_TOKEN` repository secret if it exists, and
otherwise the built-in `GITHUB_TOKEN`.

* **Built-in `GITHUB_TOKEN` (no setup):** public contributions only.
  Private work is still counted in the calendar total, so it shows up as
  unnamed terrain in the "other" lane.
* **`PROFILE_TOKEN` to include private contributions per repository**,
  either of:
  * a **classic personal access token** with the **`repo`** scope (add
    **`read:org`** if some of the work is in private organisation
    repositories; for organisations with SAML SSO, also authorise the token
    for that organisation);
  * a **fine-grained personal access token** with *Repository access: All
    repositories* and the *Contents: Read-only* permission (*Metadata:
    Read-only* is included automatically). Fine-grained tokens only cover
    repositories of their resource owner, so work in other people's
    organisations stays in the "other" lane.

Also enable **Settings → Public profile → Contributions & activity → Include
private contributions on my profile**, so private activity is counted in
the calendar. Repository names never appear on the map itself.

## Automation

`.github/workflows/terrain.yml` runs every Sunday at 03:17 UTC and on manual
dispatch. It installs the pinned requirements, regenerates the map and
commits as `github-actions[bot]`, but only if one of the SVGs actually
changed. The only permission it needs is `contents: write`.
