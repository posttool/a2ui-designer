# Contributing to A2UI Designer

Thanks for your interest! Here is what you need to know to work on the code.

## Requirements

- **Python ≥ 3.10** (standard library only; no `pip install` needed).
- **Node.js ≥ 20 + npm** (installs `@a2ui/web_core` and `@a2ui/lit` and builds `web/vendor/a2ui.bundle.js`).
- **Google Chrome or Chromium** (used headlessly over the Chrome DevTools
  Protocol for screenshots and layout measurement).

## Building and running the tests

```bash
npm install && npm run build
python3 -B -W ignore::ResourceWarning -m unittest discover -s tests -p '*_test.py'
python3 tests/renderer_check.py
```

Both test suites run offline once `npm install && npm run build` has completed (using a scripted LLM in `tests/fixtures.py` and the upstream examples in `node_modules/@a2ui/web_core`).

## Keeping A2UI unmodified

Upstream A2UI artifacts come directly from the published `@a2ui/web_core` and `@a2ui/lit` npm packages declared in [`package.json`](package.json) and are never embedded or hand-edited in the repository:

- Python loads the A2UI v1.0 Basic catalog, JSON schemas, and 43 spec examples directly from `node_modules/@a2ui/web_core/src/v1_0/schemas/`.
- `npm run build` (`tools/renderer/build.mjs`) bundles `@a2ui/web_core` and `@a2ui/lit` into `web/vendor/a2ui.bundle.js` (gitignored build artifact).

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
