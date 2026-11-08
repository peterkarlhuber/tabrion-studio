## Summary

<!-- What does this change and why? Name the work package (STU-0xx, QA-xx). -->

## Changes

-

## Acceptance criteria and evidence

<!-- One line per criterion of the work package: test ID, CI job or SHA. -->

## Testing

- [ ] `ruff check .`, `ruff format --check .`, `mypy --strict src tests scripts hatch_build.py`, `lint-imports`
- [ ] `python scripts/check_tabrion_api.py`
- [ ] `pytest -m "not packaging"`
- [ ] `npm run lint`, `npm run format:check`, `npm run typecheck`, `npm test` (in `frontend/`, if the frontend changed)
- [ ] `python scripts/build_frontend.py && python -m build && TABRION_STUDIO_DIST=dist pytest -m packaging` (if packaging changed)
- [ ] `mkdocs build --strict` (if docs changed)

## Checklist

- [ ] Tests cover the change (a bug fix has a test that fails without it)
- [ ] `CHANGELOG.md` updated (`Unreleased`)
- [ ] No parameter value, password or other secret in a new error or log path
- [ ] `docs/TODO.md` updated; the program status (`tabrion/docs/program/STATUS.md`) is updated by the coordinating thread
