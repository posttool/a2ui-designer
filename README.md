# A2UI Designer

A2UI Designer is a web app for teaching a generative-UI model how to lay out
[A2UI](https://a2ui.org) surfaces for *your* product. It does this by example:
you point at what is right and wrong in generated cards, and the app turns
that into a **design constitution**. It then tests that constitution against
large sets of realistic data and packages the result as a design system that
any A2UI agent and web renderer can use.

- **Protocol:** A2UI **v1.0** (release candidate), on the unmodified upstream
  Basic catalog shipped in `@a2ui/web_core`.
- **Renderer:** the official, unmodified
  [`@a2ui/web_core` + `@a2ui/lit`](https://github.com/a2ui-project/a2ui)
  npm packages (declared in [`package.json`](package.json) and bundled at build
  time by `npm run build`).
- **Runtime:** Python ≥ 3.10 standard library + SQLite, Node.js ≥ 20 + npm (for
  installing `@a2ui/web_core` and `@a2ui/lit` and building the web renderer),
  Chrome/Chromium for screenshots, and a Gemini API key.

## Quick start

```bash
git clone https://github.com/posttool/a2ui-designer.git
cd a2ui-designer
npm install && npm run build     # installs @a2ui/web_core + @a2ui/lit and builds the renderer
export GEMINI_API_KEY=...        # https://aistudio.google.com/apikey
./run.sh                         # http://localhost:8100 (also runs npm install/build if needed)
```

Click **New project**, pick a few scenario areas, and you land in an
**immersive teaching session**. The six starter cards are pre-generated (see
[Starter examples](#starter-examples)), so you can start teaching right away.

| Option | Default | Notes |
|---|---|---|
| `--port` | `8100` | |
| `--data_dir` | `~/.a2ui_designer` | SQLite DB, screenshots, generated assets |
| `--auth_mode` | `local` | `local` = single user, no login. `password` = shared secret from `--password_file` or `$A2UID_PASSWORD`, for a hosted instance |
| `--admins` | – | JSON list of usernames that are admins in `password` mode |
| `--https` | off | Self-signed TLS (needs `openssl`) so browsers allow the microphone for voice teaching |
| `--no_browser` | off | Disables headless Chrome (no screenshots, crops or render-based checks) |
| `--model` | `gemini-3.8-flash` | Default generator model; also selectable per project |

Environment variables:

| Variable | Purpose |
|---|---|
| `GEMINI_API_KEY` | Gemini API key. Alternatively put it in `~/.config/a2ui_designer/gemini_api_key` |
| `CHROME_BIN` | Chrome/Chromium binary. Otherwise the app looks for `google-chrome`, `google-chrome-stable`, `chromium`, `chromium-browser`, then the macOS app bundles |
| `FIGMA_API_KEY` | Optional: Figma imports (users can also save their own token) |
| `STITCH_API_KEY` | Optional: [Stitch](https://stitch.withgoogle.com) imports |
| `A2UID_WEB_IMAGES=0` | Don't resolve sample image placeholders via Wikimedia Commons |
| `A2UID_STARTER_CACHE=0` | Always generate starter cards live instead of using the shipped cache |

### Hosting

Run the server behind any reverse proxy with `--auth_mode password`. The whole
app is one Python process plus a headless Chrome. Run it on a small VM or in a
container that has Chromium installed.

## What you can do

- **Teach by example.** Each new project opens in immersive mode: just the
  card. You point at components, regions or omitted data, by voice or by
  typing. Gemini turns your observations into candidate rules, which you
  confirm. **Expert mode** (`I`) adds the input data and observation panes for
  the current session. **Finish** assembles the constitution from the confirmed
  rules, writes Dos & Don'ts with crops, and runs a before/after effect study.
- **Constitutions** hold *decisions in context*: which component or template
  to use for which data, hierarchy, density, disclosure, and states. Visual
  styling lives in design tokens, and component behavior lives in the catalog.
  A scope check flags rules that are in the wrong place.
- **Teachings ledger.** Every rule is tracked across phrasings and trials
  (judge, effect attribution, designer verdicts). The ledger flags rules that
  have been rephrased repeatedly without improving.
- **Synthetic data library.** 26 built-in genres across agentic, personal
  assistant, documents, apps, communication, care & health and enterprise.
  You can also synthesize samples from a free-text brief, or add custom
  categories.
- **Runs, review and effect studies.** For each revision, sample and viewport,
  Gemini generates an A2UI v1.0 payload. The payload is validated (with one
  repair round), rendered by the official renderer, screenshotted, and
  pre-scored by a multimodal judge. You review worst-first, click components
  to inspect them, save Dos & Don'ts, and compare runs or constitution
  revisions side by side.
- **Design system.** Design tokens, plus **custom components** authored as
  sanitized HTML/CSS templates or as compositions of Basic components. You can
  create them by describing them, or import them from Figma, Stitch or
  screenshots (analyzed, then confirmed before merging). Also: reusable layout
  templates and imagery/icon styles for Gemini-generated images and SVGs.
- **Connected apps.** MCP servers (Streamable HTTP) as live data sources. A
  bundled demo server is included.
- **Design package export.** A zip that works on its own in your project; see
  below.

## A2UI: what is upstream and what is ours

| What | Where | Status |
|---|---|---|
| A2UI v1.0 spec JSON, Basic catalog v1, its 43 examples, and web renderer | [`package.json`](package.json) (`@a2ui/web_core` + `@a2ui/lit`) | **Unmodified** published npm packages installed into `node_modules/@a2ui/` and bundled into `web/vendor/a2ui.bundle.js` by `npm run build` ([`tools/renderer/build.mjs`](tools/renderer/build.mjs)) |
| Host layer: design tokens → CSS variables, inspect/boxes, custom-component wrappers, composition lowering | [web/renderer/](web/renderer/) | Ours. Uses only the libraries' public APIs (`MessageProcessor`, `Catalog`, `A2uiLitElement`, …) |
| Extension catalog: `HeroCard` (full-bleed image + scrim) and `SvgIcon` (image/inline-SVG icon) | [seeds/a2ui_designer_catalog.json](seeds/a2ui_designer_catalog.json) | Ours. A separate catalog with its own id (`https://posttool.github.io/a2ui-designer/catalogs/extensions/v1/catalog.json`). **Off by default**; enable it per project in Design system |
| Project catalogs (your custom components) | generated | Each project publishes its own v1.0 catalog. The default id is `https://posttool.github.io/a2ui-designer/catalogs/projects/<slug>/catalog.json`; set your own in Package settings |

Payloads mix catalogs as A2UI v1.0 specifies: `createSurface.catalogId` is the
Basic catalog, and components from the extension catalog or a project catalog
carry their own `catalogId`.

To upgrade to a newer A2UI release (for example, the final 1.0 release):

```bash
npm install @a2ui/web_core@X @a2ui/lit@X && npm run build     # upgrade A2UI packages & rebuild renderer
python3 tools/build_starter_cache.py                          # regenerate starter cards (needs GEMINI_API_KEY + Chrome)
```

## Design package

**Package** in a project downloads `<project>-design-package.zip`:

- `prompt/system_prompt.md`: the exact generator system prompt (role, output
  contract, constitution, compact catalog, templates, imagery). Use it in your
  agent.
- `catalog/catalog.json`: a v1.0 catalog with the project's own id and
  embedded `instructions`. `catalog/project_catalog.json` contains only the
  custom components, and `catalog/components.json` has the full component
  definitions.
- `renderer/`: the same official bundle plus the host layer. `index.js`
  exports `mountSurface` and `loadPackage()`.
- `demo.html`: live-renders every example with your components and tokens.
  It works from any static host, or opened directly from disk.
- `tokens/` (CSS variables, `theme.json`, W3C DTCG `design-tokens.json`),
  `templates/`, `imagery/`, `assets/`, `constitution.md`, and an `index.html`
  gallery.

```html
<div id="ui"></div>
<script type="module">
  import {loadPackage, mountSurface} from './renderer/index.js';
  const pkg = await loadPackage('.');
  mountSurface(document.getElementById('ui'), messagesFromYourAgent, pkg);
</script>
```

## Starter examples

Every new project starts from the same seed samples
([seeds/starter_samples.json](seeds/starter_samples.json)). Their baseline
cards are generated **once** and shipped in
[seeds/starter_examples/](seeds/starter_examples/): the A2UI payload, the
screenshot, and component boxes for click-to-point. A new project copies them
in instead of calling Gemini and Chrome. A cached card is only used when every
input matches: seed, viewport, catalog, tokens, model, generator prompt,
renderer fingerprint and an empty constitution. Anything else is generated
live. `tests/starter_cache_test.py` fails when the shipped cache is stale.

## Development

```bash
python3 -B -W ignore::ResourceWarning -m unittest discover -s tests -p '*_test.py'
python3 tests/renderer_check.py   # headless Chrome over all upstream examples + fixtures
```

Layout:

| Path | What |
|---|---|
| `server/` | stdlib HTTP server, routing, auth |
| `core/` | storage (SQLite), service layer, settings/tokens, A2UI catalog/validator/normalizer (`core/a2ui/`), layout diff, data links |
| `sim/` | generator, judge, reviser, runs pipeline, teaching sessions + ledger, effect studies, synthesizer, imagery, starter cache |
| `refs/` | Figma / Stitch / screenshot imports and the component builder |
| `exports/` | design package + admin bundles |
| `mcp/` | MCP client + demo server + form generation |
| `render/` | headless Chrome (raw CDP) pool |
| `llm/` | Gemini REST client + prompts |
| `web/` | vanilla ES-module UI (no build step), `web/renderer/` host, `web/vendor/` official bundle |
| `seeds/` | verbatim upstream A2UI, extension catalog, starter samples + cached starter examples |
| `tools/` | renderer bundle build, upstream sync, starter-cache builder |
| `docs/` | API/renderer contract, imports, teaching sessions, designer guide |

Report bugs and ideas at
[github.com/posttool/a2ui-designer/issues](https://github.com/posttool/a2ui-designer/issues)
(the in-app **Report an issue** link pre-fills version and context).

## License

Apache-2.0; see [LICENSE](LICENSE) and [NOTICE](NOTICE). A2UI is © Google LLC,
Apache-2.0.
