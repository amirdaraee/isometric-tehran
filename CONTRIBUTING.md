# Contributing

Thanks for helping draw Tehran. Issues and pull requests are welcome, in English or Persian. Small fixes can go
straight to a pull request. For bigger changes (a new layer, a pipeline change, a new district) please open an
issue first so we can agree on the approach.

## Layout

| Folder | What | In git |
|---|---|---|
| `city.json` | The city: bbox, projection, camera, resolution, time zone, opening view | yes |
| `web/` | The viewer: React, Vite, OpenSeadragon | yes |
| `web/src/i18n/` | Interface text in English (`en.ts`) and Persian (`fa.ts`) | yes |
| `web/public/data/` | Pins, landmark cards, street lines, water and cloud masks (pipeline output) | yes |
| `web/public/tiles/` | The map tiles (pipeline output; about 11 MB in the first draft) | yes, for now |
| `pipeline/` | Python: downloads, scene, render, tiles, places; tests in `pipeline/tests/` | yes |
| `docs/` | Data sources and screenshots | yes |
| `data/` | Raw downloads and work files | no |

## Setup

    # viewer (Node 22.18 or newer)
    cd web && npm install && npm run dev        # http://localhost:3000

    # pipeline (Python 3.11+, uv)
    cd pipeline
    uv venv && uv pip install -r pyproject.toml --extra fetch --extra dev

The map works straight after cloning: the tiles and data are committed. You only need the pipeline to change the
map itself.

## Branches and pull requests

- `main` is protected: every change goes through a pull request, and needs one approving review from the code
  owner (see `.github/CODEOWNERS`) and green checks.
- Work on a branch named for the change, such as `fix/search-zwnj`, `feat/streets-fa-labels` or
  `landmark/sepahsalar`.
- One topic per pull request. Say what changed and how you checked it, and add a screenshot for anything visible.
  The pull request template has the checklist.

## Checks (run them before a pull request)

The same checks run in CI as three jobs: `web`, `pipeline` and `repo-checks`.

    cd web
    npm run typecheck
    npm test
    npm run build

    cd pipeline
    .venv/bin/ruff check .
    .venv/bin/python -m pytest

    python3 scripts/check_repo.py      # no big files, raw data or secrets

## Good first issues

- **Translations:** improve the Persian interface text in `web/src/i18n/fa.ts`.
- **Names:** add missing `name:en` or `name:fa` to places in OpenStreetMap (then they appear after the next build).
- **Heights:** add `height` or `building:levels` to buildings in the core in OpenStreetMap.
- **Landmarks:** add one from the list of national heritage sites in the bbox (see below).
- **Viewer:** a Persian (Solar Hijri) date next to the time when the interface is in Persian.
- **Accessibility:** keyboard focus order and screen-reader labels in right-to-left mode.

## How to add a landmark

1. Find the landmark on [Wikidata](https://www.wikidata.org). It must have coordinates inside the bbox in
   `city.json`, and preferably English and Persian labels and a Wikipedia article.
2. Add it to [`pipeline/landmarks.json`](pipeline/landmarks.json): `qid`, a `slug` (lower-case English, with
   dashes), a `kind` (palace, mosque, bazaar, museum, square, gate, street, government, church, park...), and
   `"featured": true` only for the most important ones. Use `"en"` only to correct an English label.
3. Rebuild the data:

       cd pipeline
       .venv/bin/python fetch_data.py wikidata
       .venv/bin/python build_places.py

4. Check the pin and the card in the viewer, and commit `pipeline/landmarks.json` and the changed files in
   `web/public/data/`.

Only facts from Wikidata and Wikipedia go on the cards, with their sources. If something is wrong, fix it on
Wikidata or Wikipedia first.

## How to add or fix a translation

- Interface text: every key in `web/src/i18n/en.ts` has its Persian text in `web/src/i18n/fa.ts`. `npm test`
  fails if a key is missing or a `{placeholder}` differs. Use Persian punctuation (، ؛ ؟) and the zero-width
  non-joiner (‌) where Persian needs it, for example «می‌شود».
- Place names come from OpenStreetMap (`name`, `name:en`, `name:fa`) and Wikidata labels. Fix them there, not in
  the generated files.
- Another language: add a messages file, add it to `LANGS` and `DIR` in `web/src/i18n/core.ts`, and open an issue
  first: labels would then need a choice of which two names to show.

## Working on the viewer

- **New optional features need a switch.** Add a key to `DEFAULTS` in `web/src/features.ts` with a one-line
  comment and check it with `on("yourFeature")`. Test with `?off=yourFeature`.
- **Animate through the frame clock** (`web/src/frame.ts`, `subscribe`), not your own `requestAnimationFrame`.
- **Load public files through `asset()`** from `web/src/config.ts`, never a bare `"/data/..."` path, so the site
  works under `/isometric-tehran/` on GitHub Pages.
- **Right to left:** test every visible change with `?lang=fa`. Prefer logical CSS (`inset-inline-end`,
  `padding-inline-start`) and `dir="auto"` on text that can be in either script.
- Map positions are in map pixels. Convert to the screen with `map.current.toScreen()`.

## Working on the pipeline

- Downloads stay small and only cover the bbox. Record every new source in `fetch_data.py` (it writes
  `data/raw/SOURCES.json`) and in `docs/DATA.md`, with its licence.
- Data must have an open licence that allows redistribution, and its credit goes in the README and the viewer's
  About panel.
- One heavy process at a time; the first-draft render takes about 10 s and 2 GB of memory.
- **No paid or GPU jobs without the owner's approval of the budget first.**
- Base art rules (for the painting to come): nothing that moves is drawn into the map (no cars, people, birds),
  water is still, trees are summer.

## Art licence

Code is MIT. The map imagery (rendered and painted tiles) and drawings are CC BY-NC 4.0
([LICENSE-ART.md](LICENSE-ART.md)). Contributions to the art are accepted under the same licence.

## What not to commit

Raw data or anything from `data/`, `.env` files, keys or tokens, `node_modules`, `.venv`, `web/dist`, GeoParquet,
GeoTIFF or numpy archives. `scripts/check_repo.py` (the `repo-checks` job) refuses them, and any file over 2 MB.
