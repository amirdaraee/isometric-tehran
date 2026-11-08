# Working on Isometric Tehran

Guidance for anyone changing this repository, people and coding agents alike. Read README.md for what the project
is and CONTRIBUTING.md for the full rules; this is the short version.

## Layout

- `city.json`: the city (bbox, CRS EPSG:32639, camera, 0.25 m per pixel, time zone, opening view). Pipeline and
  viewer both read it. Change the city here, not in code.
- `web/`: the viewer (React 18, Vite 6, OpenSeadragon 5, TypeScript strict).
  - `src/App.tsx` wires everything; `src/features.ts` holds the feature switches.
  - `src/i18n/`: `core.ts` (pure: messages, bilingual labels, Persian search folding), `en.ts`, `fa.ts`,
    `index.ts` (React glue, sets `<html lang dir>`).
  - `src/components/`: `IsometricMap` (deep zoom), `Pins`, `Search`, `Streets`, `LandmarkPanel`, `SceneControl`,
    `Water`, `Clouds`, `Feedback` (switched off).
  - `public/tiles/` and `public/data/`: pipeline output, committed for now.
- `pipeline/`: Python 3.11+. `common.py` (config, projection, camera), `fetch_data.py`, `osm.py`, `heights.py`,
  `build_scene.py`, `render.py`, `build_tiles.py`, `build_places.py`, `build_water_mask.py`, `build_clouds.py`;
  `landmarks.json` is the curated landmark list; tests in `pipeline/tests/` with fixtures.
- `pipeline/paint/`: the painter (docs/PAINTING.md). `paint.json` (model, LoRA repos, block size, versions),
  `blocks.py` (plan, status, prepare), `paint.py` (estimate, run local or RunPod, submit, dry-run), `painter.py`
  + `md_core.py` (whole-block painting, numpy) + `qwen.py` (the real model, torch) + `stub.py` (CPU stand-in),
  `review.py` (maintainer checks, blend, tiles), `claims.json` and `submissions/` (changed only by pull request),
  `prompts/`. `build_variant_tiles.py` builds the golden and night pyramids.
- `scripts/check_repo.py`: size, raw-data and secret checks.
- `data/`: raw downloads and work files. Never committed.

## Commands

    cd web && npm install && npm run dev       # http://localhost:3000
    cd web && npm run typecheck && npm test && npm run build
    cd pipeline && uv venv && uv pip install -r pyproject.toml --extra fetch --extra dev
    cd pipeline && .venv/bin/ruff check . && .venv/bin/python -m pytest
    python3 scripts/check_repo.py

Rebuild the map: `fetch_data.py all`, `build_scene.py`, `render.py`, `build_tiles.py`, `build_places.py`,
`build_water_mask.py`, `build_clouds.py mask` (all from `pipeline/` with `.venv/bin/python`).

Painting: `paint/blocks.py plan|status`, `paint/paint.py dry-run` (CPU, no GPU or network), `paint/paint.py
estimate|run|submit`, `paint/review.py check|fetch|blend|tiles|publish`. torch and diffusers are only in the
`paint` extra: nothing in CI or the tests may import them (`qwen.py` is imported only for a real run).

## Tests to run before a pull request

The CI checks are `web` (typecheck, `npm test`, build), `pipeline` (ruff, pytest) and `repo-checks`. Run the same
locally. Web tests are `*.test.ts` files run by `node --test` (no test framework), and must only import pure
modules (no DOM, no `import.meta.env`). Pipeline tests must not need downloads or a GPU: use
`pipeline/tests/fixtures/`.

## Conventions

- Every optional viewer feature has a switch in `features.ts`; check it with `on("name")`.
- Public files are loaded through `asset()` (`src/config.ts`): the site runs under `/isometric-tehran/`.
- Animate through the frame clock (`src/frame.ts`), never with your own `requestAnimationFrame` loop.
- All interface text goes through `t()` with keys in both `en.ts` and `fa.ts`. Place labels use `bilingual()`.
  Check right-to-left with `?lang=fa`.
- Comments explain why, in plain English. Keep the existing style: small modules, no new dependencies without a
  reason.
- Python: ruff (line length 120), type hints where they help, no network access in tests.

## Data rules

- Download only the bbox in `city.json`. Record every source, date and licence in `fetch_data.py` and
  `docs/DATA.md`, and credit it in the README and the About panel.
- Only open data that allows redistribution. Landmark facts only from Wikidata and Wikipedia, with links.
- No paid services, no GPU or cloud jobs (RunPod, Modal or others) without the owner's approval first. The
  painter's paid path (`paint.py run --backend runpod`) prints its cost and needs `--yes`; keep it that way, and
  keep it on the user's own `RUNPOD_API_KEY` from the environment (never a key in a file).
- Base art (for painting): nothing that moves, still water, summer trees.

## What not to commit

`data/`, `.env*`, keys or tokens, `node_modules/`, `.venv/`, `web/dist/`, GeoParquet, GeoTIFF, numpy archives,
files over 2 MB, more than 150 MB of tiles.

## Workflow

`main` is protected: branch, open a pull request, get one review from the code owner, keep the checks green. Do
not push to `main` directly.
