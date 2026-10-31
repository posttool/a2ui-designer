# Contributing to A2UI Designer

Thanks for your interest! Here is what you need to know to work on the code.

## Requirements

- **Python ≥ 3.10** (standard library only; no `pip install` needed).
- **Google Chrome or Chromium** (used headlessly over the Chrome DevTools
  Protocol for screenshots and layout measurement).
- **Node.js ≥ 20 + npm** — *only* if you are rebuilding the vendored A2UI
  renderer bundle (`web/vendor/a2ui.bundle.js`). Ordinary development and end
  users do not need Node.

## Running the tests

```bash
python3 -B -W ignore::ResourceWarning -m unittest discover -s tests -p '*_test.py'
python3 tests/renderer_check.py
```

Both suites run offline (with a scripted LLM in `tests/fixtures.py` and the
vendored upstream examples in `seeds/a2ui/`).

## Keeping A2UI unmodified

Two directories hold upstream A2UI artifacts and must never be hand-edited:

- `seeds/a2ui/` — verbatim files from `github.com/a2ui-project/a2ui`.
  `tests/seeds_test.py` checks every file against the sha256 in
  `seeds/a2ui/UPSTREAM.json`. Refresh with
  `python3 tools/sync_a2ui.py --ref <tag-or-sha>`.
- `web/vendor/a2ui.bundle.js` — built from the published `@a2ui/web_core` and
  `@a2ui/lit` packages by `tools/renderer/build.mjs`:
  ```bash
  cd tools/renderer && npm install && npm run build
  ```

A2UI Designer's own additions (tokens, custom-component HTML/CSS templates, the
`HeroCard`/`SvgIcon` extension catalog, inspect mode) live in `web/renderer/`,
`core/a2ui/` and `seeds/a2ui_designer_catalog.json`.

## Rebuilding the starter-example cache

If you change `seeds/starter_samples.json`, `llm/prompts/generate_ui.md`, the
default theme/catalog, or the renderer, `tests/starter_cache_test.py` will fail
until the shipped starter cards in `seeds/starter_examples/` are regenerated:

```bash
export GEMINI_API_KEY=...
python3 tools/build_starter_cache.py
```

## License

Contributions are licensed under Apache-2.0 (see [LICENSE](LICENSE)).
