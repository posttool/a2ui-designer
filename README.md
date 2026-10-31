# Design Constitution Builder

A web app for designers to author **design constitutions** — the direction a
generative-UI engine (emitting [A2UI v0.9](https://a2ui.org)) follows when
choosing components, layouts, and templates for arbitrary input data — and to
test, review, and evolve them against large sets of synthetic and live-tool data
across Web, Android Compose, Android RemoteCompose, and iOS SwiftUI.

-   **Projects**: each project has a constitution revision
    history, a revision chat, a **Design system** (`/p/:pid/design` — Tokens,
    Components, Templates, Imagery & Icons), **Runs** & **Effect studies**,
    **Review**, **Dos & Don'ts** (screenshots + markdown), an **Imports**
    pipeline (`/p/:pid/imports`), a **Package** browser (`/p/:pid/package`), and
    **Settings** (viewports, renderers, Gemini models, component strategy,
    default data source). **New project** (`/wizard`) asks for a name,
    scenario areas and an optional brief and starts a teaching session (or
    opens a blank Studio / imports a `.md`).
-   **Teaching sessions** (`/p/:pid/teach/:sid`, also *Teach by example* on the
    Constitution page and project cards): many
    anchored observations per card (components, input-data paths,
    data↔component relations, drawn regions) next to the card's input data,
    distilled into candidate rules you confirm as you go, generalized to other
    library scenarios (*Where else does this apply?* → *Try it* probe cards),
    layout templates captured from examples (*when should it appear again?*),
    **immersive mode** by default (just the card, pointing and voice — `./run.sh --https` for the microphone) with an **Expert mode** (`I`) that shows the input data and observation panes,
    and Finish = constitution assembled from confirmed rules + Dos & Don'ts with
    crops + Before vs. After effect study. Every rule/template is tracked in
    the **Teachings** ledger (`/p/:pid/teachings`): phrasings, trials from the
    judge / effect attribution / designer verdicts, and signals such as
    *rephrased without gain*. Contract: `docs/TEACH.md`.
-   **Constitutions start empty.** Constitutions hold *decisions in context*
    (which component or template for which data, hierarchy, density, disclosure,
    ask/act/hand off, states) — never visual styling, which lives in theme
    tokens (CSS), or component behavior, which belongs in the catalog. A **Scope
    check** flags misplaced rules, and **Constitution effect studies**
    (`/p/:pid/effects/:eid`) run no-constitution control vs. treatment (plus a
    second control for temperature noise) and attribute layout changes to
    numbered constitution rules (`R1..Rn`).
-   **Synthetic data** (`/data`): a global library across 26 built-in genres in
    7 groups (Agentic, Personal assistant & coordination, Documents & editorial,
    Apps & data, Communication & media, Care & health, Enterprise), spread
    across size, state, urgency, participants, permissions, locale, and edge
    cases. "Generate starter library" creates 3 per genre (78). Designers can
    add custom categories / subcategories, or synthesize from a free-text brief
    and let Gemini file samples into existing or new categories. Image
    placeholders (`/img/<slug>/<w>x<h>`) are resolved to Wikimedia Commons
    photos (`sim/images.py`, credits in `samples.meta.image_credits`;
    `DCB_WEB_IMAGES=0` disables).
-   **Runs & multi-renderer review**: revision × samples × viewports × enabled
    renderers (`web`, `android_compose`, `android_remote_compose`,
    `ios_swiftui`). For each output, Gemini generates A2UI (validated against
    the project catalog with one repair round; active templates and imagery/SVG
    styles are included in the system prompt, and `/gen/img` + `/gen/svg` URLs
    are materialized via Gemini image/SVG models), each enabled renderer draws
    and screenshots it, and a multimodal LLM judge pre-scores it. Designers
    review worst-first, compare renderers side by side (`All N Side-by-Side` +
    `.rc` wire bytecode inspector), click components in Live View to inspect
    A2UI JSON + live theme tokens or scope verdicts/chat to that component, save
    Dos & Don'ts, and compare runs side by side. Runs with no explicit samples
    use the project's **default data source** (`settings.data_source`: a sample
    set, a selection, or a category). Each run snapshots its theme tokens
    (`Tokens T#`); when project tokens change later the run shows **token
    drift** and can be **re-themed** (re-rendered against current tokens on all
    enabled renderers with 0 LLM calls).
-   **Connected apps** (`/apps`): register a server-side MCP server (Streamable
    HTTP URL + headers), like a Gemini Connected App, or add the bundled demo
    app (pharmacy, calendar, shop). Pick a tool, fill its arguments in an A2UI
    form generated from the tool's input schema under your constitution, call
    it, and import the result as a sample. Runs then render both stages of the
    journey for those samples: the input form (`stage:"form"`) and the result
    (`stage:"result"`).
-   **Imports** (`/p/:pid/imports`): bring in Figma files (browse your saved
    files/projects/teams and pick pages/frames, or paste a URL with a personal
    access token / `FIGMA_API_KEY` / `~/.figma_token`), Stitch projects (browse
    your Stitch projects and pick screens, or paste a project URL with a Stitch
    API key / `STITCH_API_KEY` / `~/.stitch_api_key`), or screenshots. Every
    import runs `ingesting → analyzing → review → planning → plan_ready →
    merged` with live step and crop progress: Gemini splits the material into
    cropped **extractions** (`component`, `template`, `token_reference`,
    `ignore`) and asks clarifying questions on uncertain items; once confirmed,
    a selectable **merge plan** applies changes into theme tokens, custom
    components (auto-activated when clean), reusable templates, project data
    samples, and a constitution revision + chat summary. Figma frames and Stitch
    screens also yield structured **project data** (screen text and content)
    that becomes a project sample set and, optionally, the default run data
    source. When imported material sets only some of the 22 color roles, the
    rest are filled with contrast-safe Material 3 colors
    (`core/material_color_utilities.py`, a stdlib port of Material Color
    Utilities); typography tokens are backfilled from Figma/Stitch facts and any
    `--a2ui-*` token the imported material reads is brought in as a custom
    token. Contract: [docs/IMPORTS.md](docs/IMPORTS.md). Authoring guide:
    [docs/DESIGN_REFERENCE_GUIDELINES.md](docs/DESIGN_REFERENCE_GUIDELINES.md).
-   **Design system** (`/p/:pid/design`): four layers — constitution rules
    (`R1..Rn`) → templates → components → tokens (+ imagery styles) — with a
    **dependency graph** (`core/dependencies.py`) that shows which rules use
    which templates/components/tokens/styles (`Used in R#` badges), flags broken
    cross-layer references, and records the design-system snapshot each
    constitution revision was written against (`rev R# · Tokens T#`).
    -   **Tokens**: 42 canonical tokens across 4 groups (22 color roles, 6
        shape/elevation, 8 typography, 6 spacing/density — `card_padding`,
        `surface_padding`, `card_margin`, and `item_margin` take 1–4 CSS box
        values) plus `scheme`, custom `--a2ui-*` tokens, presets, and a two-way
        synced CSS editor. Every token edit creates a **token revision**
        (`T1..Tn`) with a diff, browsable and revertible in the Token History
        drawer. Non-default Google Fonts are loaded on demand by every renderer
        surface.
    -   **Components**: Basic Catalog toggles plus custom A2UI components built
        from Imports, Figma, Stitch, screenshots, or a text description, with
        visual fidelity scoring and one-click **Fix with Gemini** repair. The
        project **component strategy** (`settings.catalog.component_strategy`,
        or per-component `definition.strategy`) picks how they are built:
        `web_template` (web-native HTML template + scoped shadow-DOM CSS),
        `basic_composition` (a parameterized Basic Catalog composition that is
        lowered into Basic components so it renders on all 4 renderers, and can
        be saved as a template), `native_targets` (generated Jetpack Compose,
        RemoteCompose, and SwiftUI code plus a composition for previews), or
        `auto` (decided per component: its **Basic fit** — how faithfully the
        design maps onto Basic Catalog components, scored from Figma/Stitch
        structure or estimated by Gemini — is compared with the project's
        **Basic-fit threshold**, set with a slider in Design system →
        Components: at or above it the component becomes a Basic catalog
        composition, below it a custom web component; existing auto-built
        components can be re-decided in one click, pinned ones keep their
        choice). Figma nodes are compiled deterministically
        (`refs/figma_compiler.py`).
    -   **Templates**: reusable screen/section layouts composed from Basic and
        custom components, injected into the generator prompt (`# Templates`).
    -   **Imagery & Icons**: reusable style presets (with reference images) for
        generated raster imagery (`/gen/img`, `Card.backgroundImage` with
        scrims, custom `data-bg`) and vector SVG icons (`/gen/svg`, `Icon`
        `{url}`/`{svg}`/`size`, custom inline `<svg>`).
-   **Package** (`/p/:pid/package`): a file-tree browser of the downloadable
    design package `.zip` (every file opens in a viewer: HTML/CSS syntax
    highlighting, JSON tree/source, images, and a live `index.html` gallery
    preview). The package holds `constitution.md`, `catalog/catalog.json`,
    `catalog/rules.txt`, `catalog/examples/`, `catalog/components/<Name>/`
    (definition, `template.html`, `styles.css`, `composition.json`, `compose/`,
    `remote_compose/`, `swiftui/`, previews) plus `CatalogRegistry.kt` /
    `CatalogRegistry.swift`, `templates/<slug>/`, `imagery/{styles.json,refs/}`,
    `tokens/{theme.json,tokens.css,design-tokens.json}`, and a static gallery
    (`index.html`).
-   **Admins** (`/admin`) see all projects, download
    constitutions/history/guidelines, triage an inbox of escalated or unfixable
    Don'ts into **benchmark bundles** (`cases.jsonl` + screenshots +
    constitution snapshots) for model/benchmark designers, and view **Bug
    reports** (`?tab=bugs`).
-   **Report a bug**: top-bar **Report bug** button, user menu, Review toolbar,
    and error-page/error-toast actions open a bug report dialog targeting
    Buganizer component **AIR › On Device Intelligence › Gen UX** (`1629228`,
    configurable via `--bug_component_id` / `DCB_BUG_COMPONENT_ID`). Every
    context item (route/project/run/output IDs, redacted settings, constitution
    markdown, output evaluation & judge verdict, A2UI JSON, sample data, output
    screenshots, recent API/console/server errors, and pasted/uploaded/captured
    screenshots) is an individually checkable row with an inline preview and a
    full **Preview exact report** view before filing via the Buganizer CLI (or
    via a pre-filled Buganizer link + context `.zip` bundle).

## Run

```bash
cd google3/experimental/users/dkaram/design_constitution_builder
./run.sh                       # http://dkaram.c.googlers.com:8810/
./run.sh --port 8811 --data_dir /tmp/dcb
```

Stdlib-only Python 3.13 (no pip), SQLite, vanilla JS ES modules (no build step).
Data lives in `~/.design_constitution_builder/` (`app.db`, `screens/`,
`thumbs/`, `guidelines/`, `bundles/`, `references/`, `components/`,
`templates/`, `imagery/`, `gen/`, `imgcache/`, `secret.key`). Gemini key:
`$GEMINI_API_KEY` or `~/.config/agent_os/gemini_api_key`. Screenshots and import
region crops require `/usr/bin/google-chrome` (note: `--no_browser` disables the
headless Chrome pool, skipping Web screenshots and import crops). Admins are
listed in `admins.json`. Sign-in is dev-mode (type your LDAP); `--auth_mode iap`
trusts IAP headers for Cloud Run deployment.

## Test

```bash
python3 -B -W ignore::ResourceWarning -m unittest discover -s tests -p '*_test.py'
node tests/renderer_check.mjs
python3 -B tools/render_spec_examples.py   # contact sheet of the 43 spec examples
```

## Renderer gaps

Known differences between the Web, Android Compose, RemoteCompose, and iOS
SwiftUI renderers — and a log of renderer-specific work — live in
[docs/RENDERER_GAPS.md](docs/RENDERER_GAPS.md). Update it whenever a renderer
change lands or a new gap is found.

## Layout

<!-- disableFinding(LINE_OVER_80) -->

<!-- mdformat off(reason: standard GFM table rendering in Critique and Code Search) -->

| Path | What |
| :--- | :--- |
| `core/` | SQLite storage, service (projects, revisions, token revisions, chat, samples, guidelines), `settings.py` (42 tokens + `scheme`, box spacing, theme hash/diff, models, renderers, component strategies, data source), `material_color_utilities.py` (stdlib Material 3 color port), `dependencies.py` (4-layer dependency graph + design-system snapshots), `components.py`, `imagery.py`, `bugs.py` (Buganizer CLI filing, reviewable context collection, secret redaction, context `.zip` bundles), `diffs.py`, `layout_diff.py`, `a2ui/` (catalog, validator, prompt schema, normalize, `custom_web.py` sanitizer, `lowering.py` composition → Basic lowering) |
| `llm/` | Stdlib Gemini REST client (text, structured JSON, multimodal & image generation), `ScriptedLlm` test double, `prompts/*.md` (`generate_ui`, `generate_form`, `generate_svg`, `judge`, `revise_constitution`, `scope_check`, `synthesize_samples`, `attribute_effect`, `analyze_import`, `plan_import_merge`, `analyze_design_reference`, `build_component`, `compare_component`, `teach_distill`, `teach_applications`, `teach_pattern`, `teach_voice_turn`) |
| `sim/` | Taxonomy (26 built-in genres + custom categories), synthesizer, generator (A2UI + templates + imagery/SVG rules), `imagery_gen.py`, `images.py` (Wikimedia Commons resolver), judge, reviser, `runs.py` (incl. token drift + zero-LLM re-theme), `effects.py` (effect studies), `connected_apps.py`, `wizard.py` (scenario domains + starter samples), `teach.py` (teaching sessions), `teach_ledger.py` (teachings: phrasings, trials, rule bindings, signals) |
| `mcp/` | Stdlib MCP client (Streamable HTTP + SSE), bundled demo MCP server (`/mcp/demo`), JSON Schema → A2UI form builder |
| `refs/` | `figma.py` (REST client, file/project/team & frame discovery, component scan, frame contents), `figma_compiler.py` (deterministic Figma node → HTML/CSS and → Basic composition), `native_codegen.py` (Compose / RemoteCompose / SwiftUI code + catalog registries), `stitch.py` (Stitch MCP client, project & screen discovery, HTML facts), `references.py`, `imports.py` (Imports pipeline, extractions, crops, Q&A, merge plan, templates, project data), `components_import.py` (`ComponentBuilder`) |
| `render/` | Stdlib CDP client, headless-Chrome `browser_pool.py`, `native_images.py`, Android Compose & RemoteCompose worker (`android/`, `android_worker.py`), iOS SwiftUI runner (`ios/`, `ios_runner.py`, `cloudsims_runner.py`), placeholder SVG images |
| `server/` | HTTP/1.1 server + router with `{name:path}` params (`http.py`), routing table & handlers (`app.py`), auth (`auth.py`), CLI entrypoint (`main.py`) |
| `exports/` | `bundles.py` (constitution.md, history.json, guidelines ZIP, admin benchmark bundle), `package.py` (`package_files()` is the single source of the design package contents; `package_zip()` zips them) |
| `web/` | Vanilla ES module SPA (`index.html`, `app.css`, `app.js`, `views/` incl. `package.js`, `components/` incl. `code_highlight.js`), fresh A2UI v0.9 web renderer + shadow-DOM custom component runtime + on-demand Google Fonts loading (`renderer/`), `render.html` |
| `seeds/` | Vendored A2UI v0.9 Basic Catalog + 43 spec examples (Apache 2.0, from `//third_party/a2ui`), starter samples (`starter_samples.json`), starter suggestions |
| `docs/CONTRACT.md` | Shared backend, UI, and renderer API contract |
| `docs/IMPORTS.md` | Imports pipeline (`ingesting → analyzing → review → planning → plan_ready → merged`), extraction, merge plan (incl. project data), & template shapes |
| `docs/DESIGN_REFERENCE_GUIDELINES.md` | Designer guide for preparing Figma files, Stitch projects, and screenshots |
| `docs/RENDERER_GAPS.md` | Known cross-renderer differences and completed renderer work log |

<!-- mdformat on -->

<!-- enableFinding(LINE_OVER_80) -->
