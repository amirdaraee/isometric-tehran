## What changed

<!-- One topic per pull request. What and why, in a few lines. Link the issue: "Closes #123". -->

## How I checked it

<!-- Commands run, views looked at. For anything visible, add a screenshot, in English and with ?lang=fa. -->

## Checklist

- [ ] `cd web && npm run typecheck && npm test && npm run build`
- [ ] `cd pipeline && .venv/bin/ruff check . && .venv/bin/python -m pytest` (if the pipeline changed)
- [ ] `python3 scripts/check_repo.py`: no raw data, big files or secrets
- [ ] New interface text is in both `web/src/i18n/en.ts` and `fa.ts`, and checked right to left (`?lang=fa`)
- [ ] New optional viewer features have a switch in `web/src/features.ts`
- [ ] New data sources are open, recorded in `docs/DATA.md` and credited in the README and the About panel
- [ ] Art contributions are offered under CC BY-NC 4.0
