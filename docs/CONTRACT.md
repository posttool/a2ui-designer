# Design Constitution Builder — internal contract

Shared contract between the backend (`core/ llm/ sim/ mcp/ refs/ render/ server/
exports/`), the web app (`web/`), and the A2UI renderer (`web/renderer/`). Keep
it current.

Package root: `google3/experimental/users/dkaram/design_constitution_builder/`.
Run with cwd = package root: `./run.sh` (port 8810). Stdlib-only Python 3.13,
vanilla JS ES modules, no build step, no external CDNs except Google Fonts.

## Ownership

| Area                   | Owner          | Files                            |
| ---------------------- | -------------- | -------------------------------- |
| Backend + API + jobs + | main agent     | `core/ llm/ sim/ mcp/ refs/      |
: LLM + exports          :                : render/ server/ exports/ seeds/  :
:                        :                : tests/*.py`                      :
| A2UI v0.9 renderer +   | renderer agent | `web/renderer/*`,                |
: render page            :                : `web/render.html`,               :
:                        :                : `tools/render_spec_examples.py`, :
:                        :                : `tests/renderer_check.mjs`       :
| Web app UI             | ui agent       | `web/index.html web/app.css      |
:                        :                : web/app.js web/views/*           :
:                        :                : web/components/*`                :

## Concepts

-   **Constitution**: markdown that guides an A2UI generator's *layout/component
    decisions in context*. It must not contain styling that belongs in theme
    tokens (CSS) or component behavior (catalog). New projects start EMPTY (or
    from a teaching session started on the New project page).
-   **Theme tokens** (project `settings.theme`) → 42 canonical design tokens
    across 4 groups plus `scheme`, `custom_tokens` (`--a2ui-<slug>`), and
    `custom_css`. Every change is versioned as a **token revision** (`T1..Tn`,
    `token_revisions` table), independent of constitution revisions.
-   **Catalog** (project `settings.catalog` + `custom_components` table) → which
    Basic and custom components the generator may use, and how custom components
    are built (`component_strategy`).
-   **Templates** (`templates` table) → reusable screen/section layouts composed
    from Basic and custom components, injected into the generator prompt (`#
    Templates`) and exported in `package.zip`.
-   **Imagery & Iconography styles** (`imagery_styles` table) → reusable style
    presets (with reference images) for generated raster imagery (`/gen/img`)
    and vector SVG icons (`/gen/svg`).
-   **Imports** (`design_references` + `import_extractions` tables,
    `docs/IMPORTS.md`) → Figma files, Stitch projects, and screenshots go
    through `ingesting → analyzing → review → planning → plan_ready → merged`,
    splitting material into `component`, `template`, `token_reference`, or
    `ignore` extractions before applying a merge plan. Figma/Stitch imports can
    also produce project data samples (a sample set that can become the
    project's default run data source).
-   **Design-system dependencies** (`core/dependencies.py`) → the 4-layer graph
    constitution rules (`R1..Rn`) → templates → components (Basic + custom) →
    tokens (+ imagery styles), with forward/reverse edges, cross-layer issues,
    and per-revision design-system snapshots (`ds_snapshot`).
-   **Sample**: synthetic or MCP-imported input data (genre + intent + context +
    payload JSON).
-   **Run**: one constitution revision × samples × viewports × renderers →
    outputs.
-   **Output**: one sample × viewport (× stage `form`|`result` for MCP samples):
    A2UI messages, validation, per-renderer screenshots, judge, human verdict.
-   **Guideline**: a Do or Don't (screenshots + markdown body), may be escalated
    to admins.

## A2UI renderer API (`web/renderer/a2ui.js`, ES module)

```js
import {mountSurface, themeToCssVars} from '/renderer/a2ui.js';
const surface = mountSurface(containerEl, messages /* A2UI v0.9 message array */, {
  theme: {...projectSettings.theme},   // optional, see Theme tokens
  customComponents: {Name: def},       // optional project custom components
  onAction: (payload) => {},           // v0.9 client_to_server action payload
  interactive: true,                    // false = inert (screenshots still fine)
});
surface.update(moreMessages);           // apply further messages
surface.meta;  // {surfaces:n, components:n, custom:[names], unsupported:[names], missingImages:n, errors:[str]}
surface.destroy();
```

CSS: `web/renderer/a2ui.css`. All renderer classes are prefixed `a2ui-`. CSS
custom properties (set by `themeToCssVars(theme)` on the container) cover all 42
canonical tokens (`--a2ui-primary`, `--a2ui-on-primary`, `--a2ui-surface`,
`--a2ui-surface-container`, `--a2ui-on-surface`, `--a2ui-on-surface-variant`,
`--a2ui-outline`, `--a2ui-radius-card`, `--a2ui-radius-control`,
`--a2ui-radius-chip`, `--a2ui-radius-media`, `--a2ui-font`,
`--a2ui-font-heading`, `--a2ui-font-mono`, `--a2ui-type-scale`,
`--a2ui-heading-scale`, `--a2ui-space`, `--a2ui-card-padding`,
`--a2ui-card-margin`, `--a2ui-item-margin`, etc.) plus custom `--a2ui-<slug>`
tokens. Box spacing tokens (`card_padding`, `surface_padding`, `card_margin`,
`item_margin`) emit 1–4 value CSS boxes.

Fonts: `ensureThemeFonts(theme, doc)` injects `<link rel="stylesheet"
data-a2ui-theme-fonts>` for non-default Google Fonts (`font_family`,
`font_family_heading`, `font_family_mono`, custom font tokens) and
`waitForThemeFonts(theme, doc, timeoutMs)` awaits both the stylesheet and
`document.fonts.load(...)`; `surface.settled()` waits for them, so screenshots
use the imported typefaces. `fontStack` adds category-aware fallbacks
(`monospace`/`serif`/`sans-serif`), and stock Basic Catalog controls bind to
`--a2ui-font`. `Text` variant aliases (`display`, `headline`, `title`,
`subtitle`, `mono`) are normalized.

Every rendered component element has `data-a2ui-id="<component id>"`.

**Extended imagery & SVG primitives**:

-   `Card.backgroundImage`: `DynamicString` or `{url, scrim:
    "auto"|"dark"|"light"|"none", fit: "cover"|"contain"}` with scrim overlay
    and automatic foreground text/icon contrast.
-   `Icon.name`: Material icon string, or `{url}` / `{svg}` with optional `size`
    (12–128 px).
-   Generated URLs emitted by the generator
    (`/gen/img?style=<slug>&prompt=…&aspect=…` and
    `/gen/svg?style=<slug>&prompt=…`) are materialized during runs by
    `sim/imagery_gen.py` into `/api/gen/<hash>.(png|svg)`.

### Render page

`GET /render/{output_id}?viewport=phone&token=T[&scheme=light|dark]` serves
`web/render.html`. The page reads `output_id`/`token` from its URL, fetches `GET
/api/render_data/{output_id}?token=T[&theme=run|live][&token_rev=trid]` →
`{messages:[...], theme:{...}, custom_components:{...}, viewport:{id,width,
height}, framing:"card"|"page"}`. The theme defaults to the run's snapshotted
`run.config.theme` (`theme=run`); `theme=live` previews the project's current
tokens and `token_rev` a specific token revision. Composite custom components
with a Basic `composition` are lowered into Basic components
(`core/a2ui/lowering.py`) before native renders (and on Web for
`basic_composition` or template-less definitions). The page renders into
`#surface` (width = viewport width, no device chrome), then sets
`window.__a2uiReady = {height, width, overflowX:boolean, custom:[],
unsupported:[], missingImages:n, errors:[]}` after fonts/images settle (max
~4s). The screenshotter waits for it and captures the true content height
(`__a2uiReady.height`).

`GET /render/example/{name}` renders `seeds/spec_examples/{name}.json` the same
way (`GET /api/render_example/{name}` returns the same shape, default theme,
phone).

Images in samples: the synthesizer writes `/img/{photo-subject-slug}/{w}x{h}`;
`sim/images.py` replaces each with a matching Wikimedia Commons thumbnail
(cached in `image_cache`), falling back to the local labeled SVG placeholder
served at `/img/...`. People photos use
`https://randomuser.me/api/portraits/...`.

Native renderers (Android Compose, RemoteCompose, iOS SwiftUI) have no network,
so before a native render `render/native_images.py` fetches every http(s) image
and local `/api/gen/*.png` in the output's A2UI messages (disk cache
`<data_dir>/imgcache/`) and swaps the URL for a `data:image/...;base64` URI
(Commons thumbs at 500px for Android; tiered 400px/260px/180px and ≤80 KB per
surface for iOS, whose batches travel in a CloudSims launch env var). Render
meta records `images: {images, inlined, skipped, bytes}`. `/img/` placeholders
stay URLs and get each renderer's placeholder.

## Theme tokens (project `settings.theme`)

`core/settings.py` defines 42 canonical tokens across 4 groups (22 color roles,
6 shape/elevation tokens, 8 typography tokens, 6 spacing/density tokens), plus
`scheme: "light"|"dark"`, `custom_tokens: {slug: {value, type, maps_to?}}`, and
`custom_css`. `resolve_effective_theme()` resolves dark defaults, `maps_to`
targets, and `var(--a2ui-...)` references across all 4 renderers.

-   **Box spacing**: `card_padding`, `surface_padding`, `card_margin`, and
    `item_margin` accept 1–4 CSS box values (`12`, `"8px 16px"`, `"8px 16px
    12px"`, `"8px 16px 12px 4px"`); `coerce_box_value` stores an int when all
    sides match, else `"Tpx Rpx Bpx Lpx"`. Supported by all 4 renderers.
-   **Material 3 completion**: when an import sets only some of the 22 color
    roles, `expand_complementary_color_tokens` derives key colors from the
    explicit tokens and source facts, builds tonal palettes with
    `core/material_color_utilities.py` (stdlib port of Material Color Utilities:
    HCT, CAM16, `TonalPalette`, `DynamicScheme`), keeps explicit colors
    verbatim, and fills the rest with contrast-safe colors (marked
    `complementary: true`).
-   **Missing tokens**: `resolve_missing_tokens_from_material` finds `--a2ui-*`
    tokens read by imported CSS/HTML/markdown, token suggestions, or source
    facts that the theme does not define yet and synthesizes custom tokens for
    them.
-   **Token revisions** (`token_revisions` table: `id, project_id, number,
    theme_hash, theme, diff, summary, source, author_ldap, created_at`): every
    theme change through `PUT /settings`, chat, imports, or revert creates
    `T<n>`. `compute_theme_hash` (12-char sha256 of the normalized theme) and
    `compute_theme_diff` (`{changes:[{token, css_var, before, after, is_custom,
    maps_to?}], custom_css_changed, changed_css_vars, summary}`). Custom
    component previews that reference a changed `--a2ui-*` token are re-queued
    automatically.
    -   `GET /api/projects/{id}/token_revisions` → `{token_revisions:
        [TokenRevision]}` (newest first)
    -   `POST /api/projects/{id}/token_revisions/{trid}/revert` → `{settings,
        token_revision}` (new revision copying `trid`'s theme)

## Viewports & Renderers

Viewports: `phone 412×915`, `tablet 834×1194`, `desktop 1280×800`,
`smart_display 1024×600`, `watch 192×192`. `GET /api/viewports` →
`[{id,label,width,height}]`.

Renderers: `web`, `android_compose`, `android_remote_compose`, `ios_swiftui`.
`GET /api/renderers` → `{renderers:[{id, label, short_label, engine, platform,
badge, available, detail, ...}]}`. New projects default to `viewports:
["phone"]` and `renderers: ["web"]`.

Models: `GET /api/models` → `{text_models:[{id, label, note}],
image_models:[{id, label, note}], defaults:{generator, judge, image, svg}}`.

## HTTP API (JSON; cookie session `dcb_session`)

Errors: `{"error": "message"}` with 4xx/5xx. All `/api/*` require sign-in except
`/api/login`, `/api/me`, `/api/models`, `/api/gen/*`, `/api/render_data/*`,
`/api/render_component/*`, `/api/render_example/*`, `/api/render_examples`.
Timestamps ISO-8601 UTC strings.

### Auth

-   `POST /api/login {ldap}` → `{user}`; `POST /api/logout`; `GET /api/me` →
    `{user|null, auth_mode}`
-   `user = {ldap, display_name, is_admin}`

### Projects

-   `GET /api/projects` → `{projects:[Project]}` (admins: `?all=1` for
    everyone's)
-   `POST /api/projects {name, description?, constitution_md?}` → `{project}`
    (constitution defaults to "")
-   `GET /api/projects/{id}` → `{project}`; `PATCH {name?, description?,
    archived?}`; `DELETE`
-   `GET /api/projects/{id}/settings` → `{settings}`; `PUT {settings}` →
    `{settings}`
-   `GET /api/projects/{id}/catalog` → `{catalog_id, components:[{name,
    description, usage, props:[{name,type,required,enum?,description}], enabled,
    custom, has_web, component_id}]}`
-   `GET /api/projects/{id}/dependencies?revision_id=` → `{constitution_deps:
    {components, templates, tokens, imagery_styles, by_rule}, used_by:
    {components, templates, tokens, imagery_styles}, issues:[{level, layer,
    target, message, ...}], building_components, snapshot, bound_snapshot,
    revision_drift:{has_drift, token_drift, catalog_drift, templates_drift,
    bound_token_revision_number, current_token_revision_number, summary}}`
    (`core/dependencies.py`; defaults to the current revision; issues also
    appear in `GET /api/projects/{pid}/package`)

`Project = {id, owner_ldap, name, description, archived, created_at, updated_at,
current_revision:{id, number, summary, created_at}, settings,
design_sources:[{kind:"figma"|"stitch", key, name, url, references,
components}], stats:{revisions, runs, dos, donts, components, templates,
last_run_at}}`

`settings = {catalog:{base:"a2ui-basic-v0.9",
catalog_id:"https://dcb.local/catalogs/...", disabled_components:[],
custom_components:{Name: Definition},
component_strategy:"auto"|"web_template"|"basic_composition"|"native_targets",
basic_fit_threshold:0.7}, theme:{...}, viewports:["phone"], renderers:["web"],
generator:{model:"gemini-3.8-flash", temperature:0.7, max_repairs:1,
include_guidelines:false, framing:"card"}, judge:{enabled:true,
model:"gemini-3.8-flash", use_screenshot:true}, imagery:{enabled:true,
image_model:"gemini-3.1-flash-image", svg_model:"gemini-3.8-flash"},
data_source:{kind:""|"set"|"selection"|"category", sample_set_id,
sample_set_name, category, subcategory, sample_ids:[], import_id, source_kind}}`

`component_strategy:"auto"` decides per component from its **Basic fit** (0–1,
how faithfully the design maps onto Basic Catalog components; see "Design
system" below): fit ≥ `basic_fit_threshold` → `basic_composition`, else
`web_template`. Without a fit estimate it falls back to `web_template` when
`renderers == ["web"]`, else `basic_composition`
(`settings.component_strategy_decision` / `resolve_component_strategy`).
`basic_fit_threshold` is clamped to 0–1 (default 0.7; 0 = always compose from
the Basic catalog, 1 = only pixel-faithful matches). `data_source` is the
default sample source for `POST /api/projects/{id}/runs` when the request names
no `sample_set_id`/`sample_ids`.

### New project setup (`/wizard`, `sim/wizard.py`)

`/wizard` asks for a name, scenario areas and an optional brief, creates the
project and starts a teaching session (`POST /api/projects/{pid}/teach`); it
can also open a blank Studio or import a `.md`. `/p/{pid}/wizard` redirects to
`/p/{pid}/teach`. The former 3-step card-critique wizard and its
`/api/projects/{pid}/wizard[/start|/bootstrap|/graduate]` routes were removed.

-   `GET /api/wizard/domains` → `{domains:[{id, label, icon, description,
    examples}]}` (scenario areas; `pick_wizard_samples` picks the starter
    samples for a session)

### Teaching sessions & Teachings ledger (`sim/teach.py`, `sim/teach_ledger.py`, `core/data_links.py`)

Full contract in [`docs/TEACH.md`](TEACH.md). Summary: sessions
(`/api/projects/{pid}/teach`, `/api/teach/{sid}[/cards|/observations|/distill|/assemble|/finish|/graduate|/voice_turn]`),
observations (`/api/teach/observations/{obid}`), templates from examples
(`/api/teach/patterns[/draft|/check]`), the project ledger
(`/api/projects/{pid}/teachings`, `/api/teachings/{tid}[/applications|/probe|/rephrase|/escalate|/split]`)
and input-data links (`GET /api/outputs/{oid}/links`). Run kinds
`teach_baseline|teach_more|teach_probe|teach_after`; teaching runs carry
`config.rule_map` / `config.judge_constitution`. Every new revision is bound to
teachings (`rule_bindings`, `Service.add_revision(..., bind_hint=)`); the judge
cites `findings[].rule_id` (`R1..`, `T1..` for templates) and effect studies
can run over existing runs (`EffectManager.create_from_runs`, control/treatment
`labels`). Tables: `teach_sessions`, `observations`, `teachings`,
`teaching_versions`, `teaching_trials`, `rule_bindings`; crops in
`<data_dir>/teach/` (`/files/teach/…`).

### Constitution revisions

-   `GET /api/projects/{id}/revisions` → `{revisions:[Revision]}` (newest first,
    `text_md` omitted)
-   `GET /api/projects/{id}/revisions/{rid}` → `{revision}` (with `text_md`)
-   `POST /api/projects/{id}/revisions {text_md, summary}` → `{revision}`
    (becomes current)
-   `POST /api/projects/{id}/revisions/{rid}/revert` → `{revision}` (new
    revision copying rid)
-   `GET /api/projects/{id}/diff?a={rid}&b={rid}` → `{diff}`

`Revision = {id, project_id, number, text_md?, summary,
source:"seed|manual|chat|import|revert|wizard|teach", parent_id, chat_message_id,
author_ldap, created_at, ds_snapshot}` Revision 0 is created with the project
(`source:"seed"`, may be empty). `ds_snapshot` records the design system the
revision was written against (`token_revision_id`, `token_revision_number`,
`theme_hash`, `catalog_hash`, `templates_hash`, enabled Basic / active custom
components, active templates and imagery styles); the UI shows it as `rev R# ·
Tokens T#` and compares it to the live design system (`revision_drift`).

`diff = {unified:"...", rows:[{type:"equal|insert|delete|replace", a_line,
b_line, a:"text", b:"text"}], stats:{added, removed}}`

### Chat (revise constitution, tokens, catalog & imagery) — synchronous LLM call

-   `GET /api/projects/{id}/chat` → `{messages:[ChatMessage]}` (oldest first)
-   `POST /api/projects/{id}/chat {text, output_ids?:[], guideline_ids?:[],
    images?:[{data_url, name?}], selected_component?:{id, type, scope?},
    draft_md?}` → `{user_message, assistant_message}`. `draft_md` = unsaved
    editor text if any.
-   `POST /api/projects/{id}/chat/{mid}/accept {constitution_md?,
    apply_theme?:bool=true, apply_catalog?:bool=true, apply_imagery?:bool=true}`
    → `{revision, message, theme_applied, catalog_changes_applied,
    imagery_changes_applied}`
-   `POST /api/projects/{id}/chat/{mid}/discard` → `{message}`

`ChatMessage = {id, role:"user|assistant", text_md, context:{output_ids,
guideline_ids, reference_ids?, import_id?, selected_component?, images?},
proposal:null|{constitution_md, change_summary, changed_sections:[str], diff,
theme_changes:[...], custom_css:str, catalog_changes:{enable, disable,
proposed_components}, imagery_changes:[...]}, out_of_scope:[{rule, why,
suggested_home:"theme|catalog|component"}],
proposal_status:"none|pending|accepted|discarded", accepted_revision_id,
author_ldap, created_at}`

### Suggestions & scope check

-   `GET /api/projects/{id}/suggestions` → `{suggestions:[{id,
    source:"starter|judge|guideline", title, body, example_md?, count?,
    ref_ids?:[]}]}`
-   `POST /api/projects/{id}/scope_check {text_md?}` → `{items:[{rule_excerpt,
    why, suggested_home, suggested_theme_change?:{token, value}}], summary}`
    (LLM)

### Genres & samples (global library)

-   `GET /api/genres` → `{groups:[{id, label, custom, genres:[{id, label,
    description, custom}]}]}` — built-ins merged with user-created categories
    (`categories` table; group = category, genre = subcategory)
-   `POST /api/categories {kind:"group"|"genre", label, description?,
    parent_id?}` → `{category:{id,...}}` (reuses an existing one with the same
    label); `PATCH /api/categories/{id} {label?, description?, parent_id?}`;
    `DELETE` (custom only; 409 while samples/subcategories use it)
-   `GET /api/samples?genre=&group=&q=&set_id=` → `{samples:[Sample]}` (payload
    omitted in list)
-   `GET /api/samples/{id}` → `{sample}` (incl. `meta.image_credits`); `POST
    /api/samples {sample fields}`; `PATCH`; `DELETE`. Category on create/patch:
    an existing `genre` id, or `group_label` + `genre_label` (created when new);
    neither → "Uncategorized".
-   `POST /api/samples/synthesize {genres:[ids], per_genre:3, brief?, count?,
    set_name?}` → `{job}`. With genres: per_genre each, steered by `brief`. With
    no genres + `brief`: `count` samples (≤24) and the model files each into an
    existing or new category/subcategory.
-   `POST /api/samples/resolve_images {sample_ids?}` → `{job}` swaps `/img/`
    placeholders for Wikimedia Commons photos (synthesis does this
    automatically; `DCB_WEB_IMAGES=0` disables)
-   `POST /api/samples/starter_library` → `{job}` (3 per genre, creates group
    sets + "All genres")
-   `GET /api/jobs/{id}` → `{job}`; `job = {id, kind,
    status:"queued|running|done|failed", progress:{done,total}, message, result,
    error}`
-   `GET /api/sample_sets` → `{sets:[SampleSet]}`; `POST {name, description?,
    sample_ids}`; `PATCH /api/sample_sets/{id}`; `DELETE`

`Sample = {id, owner_ldap, group, genre, title, intent, context:{surface,
user_state, role, locale, time_of_day, urgency}, payload,
size_class:"xs|s|m|l|xl", state:"content|loading|empty|error", edge_cases:[str],
tags:[str], source:"synth|manual|import|mcp", created_at}` `SampleSet = {id,
name, description, owner_ldap, sample_ids:[], count, created_at}`

### Runs & outputs

-   `POST /api/projects/{id}/runs {revision_id?, sample_set_id?, sample_ids?,
    viewports?, renderers?, judge?:bool, include_forms?:bool}` → `{run}`
    (without `sample_set_id`/`sample_ids`, samples come from
    `settings.data_source`)
-   `GET /api/projects/{id}/runs` → `{runs:[Run]}`; `GET /api/runs/{id}` →
    `{run}` (poll for progress)
-   `POST /api/runs/{id}/cancel`, `POST /api/runs/{id}/resume` → `{run}`
-   `POST /api/runs/{id}/render {renderers?:[id]}` → `{run}` (re-renders outputs
    on the requested renderers without re-running the LLM generator, using the
    run's snapshotted theme)
-   `POST /api/runs/{id}/retheme {token_revision_id?, renderers?:[id]}` → `{run,
    token_revision, rethemed_outputs}` — re-binds the run to the current (or
    given) token revision and re-renders every output on the enabled renderers
    with 0 LLM calls
-   `DELETE /api/runs/{id}` → `{ok, deleted_run_ids:[id], deleted_effect_id}` —
    cancels in-flight work, deletes the run's outputs and screenshot files, and
    clears `output_id` on guidelines (which keep their own screenshot copies).
    If the run belongs to a Constitution effect study, the whole study (effect +
    all its runs) is deleted.
-   `GET
    /api/runs/{id}/outputs?sort=worst|best|genre|recent&verdict=&viewport=&genre=&valid=`
    → `{outputs:[Output]}` (a2ui omitted)
-   `GET /api/outputs/{id}` → `{output, sample}` (full, with a2ui + prompt_text)
-   `POST /api/outputs/{id}/verdict {human_verdict:"good|bad|mixed|null",
    human_note, selected_component?}` → `{output}`
-   `POST /api/outputs/{id}/rejudge` → `{output}`; `POST
    /api/outputs/{id}/regenerate` → `{output}`; `POST /api/outputs/{id}/render
    {renderers?:[id]}` → `{output}`
-   `GET /api/projects/{id}/compare?run_a=&run_b=` → `{pairs:[{sample_id,
    sample_title, genre, viewport, a:Output|null, b:Output|null}],
    summary:{a_avg, b_avg}}`

`Run` includes token binding fields: `token_revision_id`,
`token_revision_number`, `initial_token_revision_number`,
`rendered_token_revision_id`, `rendered_token_revision_number`, `theme_hash`,
`rendered_theme_hash`, `current_token_revision_number`, `current_theme_hash`,
`theme_drifted` (rendered tokens differ from the project's current tokens; shown
as `Tokens T_run → T_cur`), `catalog_hash`, and `templates_hash`.

### Constitution effect studies (`sim/effects.py`, `core/layout_diff.py`)

A study = linked runs on the same samples/viewports/renderers: `control` (no
constitution; `run.kind=effect_control`, `config.constitution_override=""`),
`treatment` (the revision; `effect_treatment`) and optional `noise` (second
control; `effect_noise`). The judge scores every arm against the revision. Runs
carry `kind`, `effect_id`, `no_constitution`, `label`.

-   `POST /api/projects/{id}/effects {revision_id?, sample_set_id?, sample_ids?,
    viewports?, renderers?, judge?, noise?:bool=true, attribute?:bool=true,
    use_screenshots?:bool=true, temperature?:number|null}` → `EffectDetail` (400
    if the revision's constitution is empty)
-   `GET /api/projects/{id}/effects` → `{effects:[{id, revision_number, status:
    "running|analyzing|done|incomplete", runs:{control, treatment, noise},
    analysis_status, headline:{attributed_pairs, top_rules}}]}`
-   `GET /api/effects/{id}` → `EffectDetail` (poll; once all runs finish the LLM
    attribution pass starts automatically)
-   `POST /api/effects/{id}/analyze` → re-run attribution

`EffectDetail = {effect, revision:{id, number, text_md}, rules:[{id:"R1",
section, text}], analysis:{status, done, total, error}, pairs:[Pair],
summary:{total, verdicts:{effect, noise, unchanged, pending}, avg_magnitude,
avg_noise_magnitude, judge:{control, treatment}, kinds, kinds_effect, overall,
rules:[{id, text, section, changes, pairs, kinds, unapplied}], by_genre}}`

`Pair = {key:"sid|viewport", sample_title, genre, viewport, control, treatment,
noise (Output|null), control_boxes|treatment_boxes:{width, height,
boxes:{"id|scope":[[x,y,w,h]]}}, magnitude:0..100, noise_magnitude, verdict:
"effect|noise|unchanged|pending", changes:[Change], attribution:{summary,
overall:"improved|regressed|mixed|neutral", unapplied_rules:[{rule_id, why}]}}`

`Change = {index, kind:"container_swap|regrouped|ungrouped|component_swap|
emphasis|reordered|added|removed", kind_label, label, a_keys, b_keys, a_focus,
b_focus (instance keys "id|scope" as in data-a2ui-id/-scope), anchor_contents,
detail, repeat?, in_noise:bool, attribution:{cause:"rule|indirect|noise",
rule_ids, confidence, explanation}|null}`

Boxes come from `window.__a2uiBoxes()` in `render.html`, captured by
`render/browser_pool.py` after the full-height resize (stored in the web render
meta, stripped from regular Output payloads).

`Run = {id, project_id, revision_id, revision_number, sample_set_id,
sample_set_name, viewports, renderers, config,
status:"queued|running|done|cancelled|interrupted|failed", counts:{total,
queued, generating, rendering, judging, done, error}, avg_score, verdicts:{good,
bad, mixed, unreviewed}, created_by, created_at, started_at, finished_at}`

`Output = {id, run_id, project_id, revision_id, revision_number, sample_id,
sample_title, genre, group, viewport, stage:"form"|"result", pair_id:str|null,
status:"queued|generating|rendering|judging|done|error", a2ui?:[messages],
prompt_text?, validation:{ok, errors:[{path,message}]}, screenshot_url|null,
thumb_url|null, render_url, render_meta, renders:{renderer_id:{status,
screenshot_url, thumb_url, meta, error}}, judge:null|Judge,
judge_score:null|1..10, human_verdict, human_note, reviewed_by, reviewed_at,
model, latency_ms, repairs, error, created_at}`

`Judge = {overall_score:1..10, verdict:"pass|warn|fail", summary,
findings:[{rule, status:"followed|violated|not_applicable", evidence,
severity:"low|medium|high"}], strengths:[str], issues:[str],
suggested_constitution_changes:[str], likely_unfixable_by_constitution:bool,
unfixable_reason}`

### Dos & Don'ts

-   `GET /api/projects/{id}/guidelines?kind=&status=` →
    `{guidelines:[Guideline]}`
-   `POST /api/projects/{id}/guidelines {kind, title, body_md, tags?,
    output_id?, fixable?}` → `{guideline}` (if output_id given, its screenshot
    is attached)
-   `GET/PATCH/DELETE /api/guidelines/{id}`; PATCH accepts `{title, body_md,
    tags, fixable, status, addressed_revision_id}`
-   `POST /api/guidelines/{id}/screenshots {output_id}` or
    `{data_url:"data:image/png;base64,..."}` → `{guideline}`
-   `POST /api/guidelines/{id}/escalate {note?}` → `{guideline}` (status
    `escalated`)

`Guideline = {id, project_id, kind:"do|dont", title, body_md, tags:[],
screenshots:[{url, output_id|null}], output_id, sample_id, revision_id,
fixable:"yes|no|unsure", status:"open|addressed|escalated|packaged",
escalation_note, addressed_revision_id, created_by, created_at, updated_at}`

### Exports (any signed-in owner; admins for all)

-   `GET /api/export/projects/{id}/constitution.md?rev={rid}`
-   `GET /api/export/projects/{id}/history.json`
-   `GET /api/export/projects/{id}/guidelines.zip?kind=&status=`
-   `GET /api/export/projects/{id}/package.zip`

### Admin (is_admin only)

-   `GET /api/admin/overview` → `{users:[...], projects:[Project &
    {owner_ldap}], totals}`
-   `GET /api/admin/inbox` → `{guidelines:[Guideline & {project_name,
    owner_ldap}]}` (escalated or fixable=no, not packaged)
-   `POST /api/admin/bundles {kind:"guidelines|benchmark", guideline_ids?:[],
    project_ids?:[]}` → `{bundle}`
-   `GET /api/admin/bundles` → `{bundles:[{id, kind, created_by, created_at,
    size_bytes, download_url, counts}]}`
-   `GET /api/admin/bundles/{id}.zip`

### Connected apps (MCP data sources; `mcp/`, `sim/connected_apps.py`)

Modeled on Gemini's Connected Apps platform: a user registers a server-side MCP
server (Streamable HTTP), DCB snapshots its tools, the user picks a tool, fills
its arguments in an A2UI form generated from the tool's `inputSchema`, DCB calls
the tool, and the result is imported as a Sample. Apps are per user and shared
across that user's projects. A bundled demo app (`kind:"demo"`, served
in-process at `POST /mcp/demo`) offers pharmacy, calendar and shop tools with
deterministic fake data.

-   `GET /api/connected_apps` → `{apps:[App]}`
-   `POST /api/connected_apps {name, url, headers?:{name:value}, description?}`
    → `{app}` (201; syncs immediately, a failed sync still saves the app with
    `status:"error"`)
-   `POST /api/connected_apps/demo` → `{app}` (registers the bundled demo app
    for the user; idempotent)
-   `GET /api/connected_apps/{aid}` → `{app}`; `PATCH {name?, url?, headers?,
    description?}` (a header value equal to its masked form is kept); `DELETE` →
    `{ok}`
-   `POST /api/connected_apps/{aid}/sync` → `{app}`
-   `POST /api/connected_apps/{aid}/form {tool, project_id?, viewport?,
    prefill?:{}, mode?:"llm"|"schema"}` → `{messages, validation, form, model,
    latency_ms, mode, fallback_reason}`. `mode:"llm"` (default) generates the
    form with the project's constitution + catalog (empty constitution + Basic
    Catalog without `project_id`); on failure it falls back to the deterministic
    schema form (`mode:"schema"`, `fallback_reason`).
-   `POST /api/connected_apps/{aid}/call {tool, args}` → `{result:ToolResult,
    latency_ms}` (MCP errors → 502 `{error}`)
-   `POST /api/connected_apps/{aid}/import {tool, args, result?, intent?,
    title?, set_id?}` → `{sample}` (201; calls the tool when `result` is
    omitted; the app becomes a category, the tool a subcategory)

`App = {id, name, description, url, kind:"remote"|"demo", status:"ok"|"error",
last_error, last_sync_at, server_info:{name, version, protocol_version},
tool_count, tools:[{name, title, description, input_schema, output_schema}],
resources:[{uri, name, description, mime_type}], headers_masked:{name:"••••"},
created_at, updated_at}`

`ToolResult = {structured:any|null, text:str, is_error:bool, content:[MCP
content parts]}`; the imported payload is `structured`, else JSON parsed from
`text`, else `{text}`.

Form messages: one surface, field values bound at `/form/<field>`, one primary
Button whose `action.event = {name:"submit", context:{form:{path:"/form"}, …}}`.
The client reads `payload.action.context.form` (or the whole context) as the
tool arguments. `form = {tool, title, schema, data_root:"/form",
submit_event:"submit", submit_label}`.

MCP samples: `source:"mcp"`, `meta.connected_app = {app_id, app_name, tool,
args, called_at, latency_ms, is_error}`, `meta.form = {tool, title, schema,
values, submit_label}`.

**Stages.** Runs give each MCP sample a `form` output and a `result` output per
viewport (`POST /api/projects/{id}/runs {…, include_forms?:bool=true}`).
`Output` gains `stage:"form"|"result"` and `pair_id` (the sibling output of the
other stage, or null). The form stage generates the tool's input form prefilled
with the sample's args under the same constitution; the judge is told the stage.
Effect studies and compare use result stages only.

### Imports, Design References & Templates (`refs/`, `docs/IMPORTS.md`)

Figma files, Stitch projects, and screenshots enter a project through
**Imports** (`/p/{pid}/imports`; `/p/{pid}/references` redirects to
`/p/{pid}/imports`). Every import runs `ingesting → analyzing → review →
planning → plan_ready → merged` with live `progress` tracking (`steps`, `done`,
`total`, `label`, `detail`). Full contract and payload shapes live in
[docs/IMPORTS.md](IMPORTS.md); authoring guidance lives in
`docs/DESIGN_REFERENCE_GUIDELINES.md` (served at
`/docs/DESIGN_REFERENCE_GUIDELINES.md`).

-   **Figma credentials & file/frame pickers**:
    -   `GET /api/me/figma` → `{has_token, source:"user"|"server"|null}`; `PUT
        {token}` (empty string clears the user token).
    -   `GET /api/figma/projects?refresh=0|1` → `{projects:[{key, name,
        kind:"file"|"project"|"team", url, last_modified, thumbnail_url,
        files?:[...]}]}` (aggregates files from DCB imports/components plus
        per-user `users.figma_sources_json`).
    -   `POST /api/figma/projects {url}` → `{source, projects}` (201); `DELETE
        /api/figma/projects/{key}` → `{projects}`.
    -   `POST /api/figma/frames {url?, file_key?}` → `{file_key, file_name,
        pages:[{id, name, frames:[{id, name, type, width, height,
        viewport_hint}]}], preselect:[node_id]}` (page & top-level frame/section
        checklist, ≤20 frames).
-   **Stitch credentials & project/screen pickers** (`refs/stitch.py` via
    `https://stitch.googleapis.com/mcp` with `X-Goog-Api-Key`):
    -   `GET /api/me/stitch` → `{has_key, source:"user"|"server"|null}`; `PUT
        {key}` (empty string clears). Server fallback: `STITCH_API_KEY` or
        `~/.stitch_api_key`.
    -   `GET /api/stitch/projects` → `{projects:[{id, title, url, screen_count,
        design_system, updated_at, source:"stitch"|"dcb"}]}` (calls MCP
        `list_projects` for owned + shared projects and merges with previously
        imported Stitch projects).
    -   `POST /api/stitch/screens {url}` → `{project:{id, title, url},
        screens:[{id, title, width, height, device_type, viewport_hint,
        has_screenshot, has_html}], design_systems:[{name, display_name}],
        preselect:[screen id]}`.
-   **Imports & Extractions** (`refs/imports.py`):
    -   `POST /api/projects/{id}/references` creates an import (`kind`:
        `"figma"|"stitch"|"screenshots"`, `url`, `node_ids?`, `screen_ids?`,
        `images?`, `title?`, `notes?`,
        `origin?:"constitution"|"design"|"imports"`). Auto-starts extraction
        analysis when ingest completes.
    -   `GET /api/projects/{pid}/imports` → `{imports:[Import]}`; `GET
        /api/imports/{iid}` → `{import:Import}` (with `extractions` and
        `progress`).
    -   `POST /api/imports/{iid}/analyze {instructions?}` → `{import}`
    -   `POST /api/imports/{iid}/confirm {ids?}` → `{import}`
    -   `POST /api/imports/{iid}/questions/{qid} {answer:{choice, note}}` →
        `{import}`
    -   `POST /api/imports/{iid}/extractions`, `POST
        /api/imports/{iid}/extractions/merge`, `PATCH
        /api/imports/{iid}/extractions/{xid}`, `DELETE
        /api/imports/{iid}/extractions/{xid}` → `{import}`
    -   `POST /api/imports/{iid}/merge/prepare {instructions?}` → `{import}`
        (409 while questions are open)
    -   `POST /api/imports/{iid}/merge/apply {lines?:{line_id:bool},
        constitution_md?, sample_set_name?}` → `{import, result}`
    -   `POST /api/imports/{iid}/data-source {sample_set_id?}` → `{import,
        sample_set, data_source, settings}` — makes the import's project data
        sample set (materialized from the plan or analysis when not merged yet)
        the project's default run data source (`settings.data_source`).
-   **Templates** (`templates` table; Design system → Templates, injected into
    `GenerationContext` under `# Templates` and exported under
    `templates/<slug>/` in `package.zip`):
    -   `GET /api/projects/{pid}/templates` → `{templates:[Template]}`
    -   `GET /api/templates/{tid}` → `{template}`; `PATCH {name?, description?,
        when_to_use?, structure_md?, components?, status?:"active"|"archived"}`
        → `{template}`; `DELETE` → `{ok}`
-   **Legacy references endpoints** (`GET /api/projects/{id}/references`,
    `GET/PATCH/DELETE /api/references/{id}`, `POST
    /api/references/{id}/analyze`) remain supported for `phase:"legacy"` rows.

### Design system: tokens, custom components, templates, imagery & package (`core/components.py`, `core/imagery.py`, `refs/components_import.py`, `exports/package.py`)

UI lives at `/p/{pid}/design?tab=tokens|components|templates|imagery` (Tokens is
the default tab; tab pills show live counts for Components, Templates, and
Imagery & Icons), `/p/{pid}/design/components/{cid}`, and the top-level
**Package** page `/p/{pid}/package` (`web/views/package.js`; the old
`?tab=package` redirects there). Theme storage is in `settings.theme`;
`settings.catalog` keeps `base`, `disabled_components`, `component_strategy`,
and `catalog_id` (default `https://dcb.local/catalogs/<project-slug>`). Custom
components live in the `custom_components` table; runs snapshot active ones into
`run.config.catalog.custom_components = {Name: Definition}`. Components queued
from an import merge (`source.kind:"import"` or `"figma"` with
`source.auto_activate:true` and `keep_name:true`) preserve their planned
PascalCase name and automatically transition to `status:"active"` once drafted
without validation issues.

**Component strategies** (`settings.catalog.component_strategy`, overridable per
component via `definition.strategy`):

-   `web_template`: `definition.web = {template, css}` (web-native HTML
    template + scoped shadow-DOM CSS). Renders natively on Web; native renderers
    show the unsupported placeholder unless the definition also has a
    `composition`.
-   `basic_composition`: `definition.composition = {root, components}`, a Basic
    Catalog tree with `{{prop}}` bindings. `core/a2ui/lowering.py`
    (`expand_composite_components`) lowers each instance into Basic components
    (`Card`, `Row`, `Column`, `Text`, `Image`, `Icon`, `Button`, `Divider`, …),
    so it renders on all 4 renderers; it can also be saved as a template.
-   `native_targets`: `definition.targets = {android_compose,
    android_remote_compose, ios_swiftui}` generated code for the package, plus a
    `composition` used for the simulator previews.
-   `auto` (default): decided **per component** from its *Basic fit* and the
    project's `settings.catalog.basic_fit_threshold` (default 0.7): fit ≥
    threshold → `basic_composition` (a template made from the Basic catalog),
    fit < threshold → `web_template` (a custom web component). Without a fit
    estimate: `web_template` when Web is the only renderer, else
    `basic_composition`.

**Basic fit** (0–1) measures how faithfully a component's design maps onto Basic
Catalog components. For Figma nodes and Stitch DOM regions it is computed
deterministically by `figma_compiler.compile_node_to_basic_skeleton(...)["fit"]`
(`{score, nodes, faithful, partial, lossy, reasons:[{node, reason, severity}],
summary:["2× absolute positioning", …]}`): every visited layer scores 1.0
(faithful), 0.5 (approximated: gradient fill, blur/inner shadow, layer opacity,
dashed/per-side borders, mixed corner radii, mixed inline text styles, plain
colored boxes, circular images) or 0.0 (inexpressible: absolute positioning,
free-form layout without auto-layout, rotation, masks, custom vector artwork,
large ellipses, nesting deeper than 8 levels); the score is the mean. For
screenshots and descriptions the build prompt's `basic_fit` / `basic_fit_reason`
(Gemini's estimate) is used instead (`components_import.basic_fit_info`). The
result is stored on the component as `source.basic_fit = {score,
source:"figma"|"stitch"|"structure"|"gemini", summary, reasons, model_estimate,
model_reason}` and the resolution as `source.strategy_decision = {strategy,
auto, threshold, fit, reason}`; both are exposed on the formatted component
(`basic_fit`, `strategy_decision`) and on `draft_custom` merge-plan lines
(`detail.basic_fit`, `detail.strategy_decision`, computed at plan time when the
Figma node / Stitch region is known).

The designer sets the rule in Design system → Components ("How custom components
are built": strategy select + Basic-fit threshold slider with a fit strip of the
project's components) and can pin a strategy per component in the editor
(`definition.strategy`; pinned components have `strategy_decision.auto:false`,
choosing "Auto" again re-decides from the recorded fit) or per merge-plan line
(`POST /api/imports/{iid}/merge/apply {strategies:{<line_id>: strategy}}`).
`POST /api/projects/{pid}/components/restrategize {dry_run?, component_ids?}`
re-runs the auto rule over existing auto-decided components after the threshold
or strategy changed (`{changed, unchanged, skipped, items:[{id, name, fit, from,
to, status:"changed"|"unchanged"|"pinned"|"no_fit"|"no_web_template",
reason}]}`); flips need no LLM call because the web template and the Basic
composition are both kept on the definition.

Figma components are compiled deterministically by `refs/figma_compiler.py`
(`compile_node_to_html_css` → token-aware HTML/CSS;
`compile_node_to_basic_skeleton` → Basic composition); `refs/native_codegen.py`
emits Compose, RemoteCompose, and SwiftUI code plus `CatalogRegistry.kt` /
`CatalogRegistry.swift`. Imported HTML templates and CSS are pretty-printed
(`custom_web.format_template`, `format_css`) and shown with syntax highlighting
(`web/components/code_highlight.js`).

`Definition` (what the generator/validator/renderer consume):

```json
{"name": "ProductTile",                       // PascalCase, unique per project, not a Basic name
 "description": "One line for the catalog.",
 "usage": "When to use / not use (goes into the generator prompt + rules.txt).",
 "props": {"type": "object", "properties": {"title": {"$ref": "common_types.json#/$defs/DynamicString"},
           "variant": {"type": "string", "enum": ["compact", "full"]},
           "footer": {"$ref": "common_types.json#/$defs/ComponentId"},
           "action": {"$ref": "common_types.json#/$defs/Action"}},
           "required": ["title"]},
 "web": {"template": "<article class=\"tile {{variant}}\">…</article>", "css": ".tile{…}"},
 "sample": {"props": {"title": "Trail Runner"}, "data": {}, "components": []}}
```

Prop types use `common_types.json#/$defs/` refs: `DynamicString`,
`DynamicNumber`, `DynamicBoolean`, `DynamicStringList`, `ComponentId` (slot, one
child), `ChildList` (slot, many children), `Action`; plain `string` with `enum`
for variants; `array` of objects for repeated data (literal array or `{"path":
"/items"}` binding). `sample.components` holds extra Basic components referenced
by slot props in `sample.props`.

**Web template syntax** (sanitized server-side in `core/a2ui/custom_web.py` and
again client-side; anything else is stripped):

| Syntax                            | Meaning                                  |
| --------------------------------- | ---------------------------------------- |
| `{{prop}}` in a text node         | resolved value → textContent (reactive)  |
| `attr="… {{prop}} …"`             | only on `src alt title class aria-*      |
:                                   : data-*` (not                             :
:                                   : `data-if/each/action/icon/bg`)           :
| `style="--name: {{prop}}"`        | only custom-property declarations        |
| `data-if="prop"`,                 | conditional                              |
: `data-if="prop\:value"`,          :                                          :
: `data-unless` (same forms; dotted :                                          :
: loop vars like `it.icon` allowed) :                                          :
| `data-each="prop as it"` +        | repeat element per array item            |
: `{{it.field}}` / `{{it}}`         :                                          :
| `<slot name="prop"></slot>`       | children of a ComponentId/ChildList prop |
| `data-action="prop"`              | Action prop (or dotted `it.action`);     |
:                                   : element becomes a button (role,          :
:                                   : tabindex, Enter/Space)                   :
| `data-icon="{{prop}}"` or         | Material Symbols Rounded icon            |
: `data-icon="name"`                :                                          :
| `data-bg="{{prop}}"`              | sets `background-image: url(...)` safely |

Allowed HTML tags: `div span p h1 h2 h3 h4 h5 h6 ul ol li img figure figcaption
section article header footer strong em small hr br slot` plus safe inline SVG
tags (`svg g path circle rect line polyline polygon ellipse defs linearGradient
radialGradient stop clipPath mask pattern text tspan`). No `script`, `style`,
`link`, `iframe`, `a`, `form`, `input`, event handlers, or `href`. CSS: no
`@import`, `url()` only `data:`; `:host` allowed; styles are scoped by a shadow
root; colors/radii/fonts/spacing must use `var(--a2ui-*)` tokens whenever a
token fits (custom tokens `--a2ui-<slug>` allowed).

**Renderer**: `mountSurface(el, messages, {customComponents: {Name:
Definition}})`; also exported `compileCustomComponent(def) → {ok, errors,
bindings, warnings, slots, actions, tokenRefs, tree, css, styleText, defaults}`
(from `/renderer/custom.js` or `/renderer/a2ui.js`; DOM-free, so the editor can
call it for instant errors).

-   `GET /api/projects/{pid}/components` → `{components:[Component],
    catalog_id}` (drafts + active; `?archived=1` includes archived)
-   `POST /api/projects/{pid}/components {definition, status?:"draft"|"active"}`
    → `{component}` (201; manual/blank creation)
-   `GET /api/components/{cid}` → `{component}` (poll while `job.stage` not
    done/error)
-   `PATCH /api/components/{cid} {definition?, status?, notes?,
    apply_token_suggestions?:bool}` → `{component}`
-   `DELETE /api/components/{cid}` → `{ok}`
-   `POST /api/projects/{pid}/components/figma/scan {url}` → `{file_name,
    components:[...], truncated:bool}`
-   `POST /api/projects/{pid}/components/import` → `{components:[Component]}`
    (201, drafts with `job.stage:"queued"`)
-   `POST /api/components/{cid}/revise {instruction, definition?, issues?,
    fix?:bool}` → `{definition, notes, fixed_issues, issues, warnings}`
    (synchronous Gemini edit or validation repair; PATCH to save)
-   `POST /api/components/{cid}/sample {instruction?}` → `{sample}`
-   `POST /api/components/{cid}/recheck` → `{component}`
-   `POST /api/components/{cid}/apply_tokens` → `{component, applied:[…]}`
-   `POST /api/components/{cid}/targets {definition?, targets?:[id], strategy?}`
    → `{component}` (generates `definition.targets` Compose / RemoteCompose /
    SwiftUI code; strategy defaults to `native_targets`)
-   `POST /api/components/{cid}/to_template` → `{template, component}` (201;
    saves the component's Basic `composition` as a reusable template; 400 when
    it has none)
-   `GET /api/render_component/{cid}?token=T&viewport=` → `{messages, theme,
    viewport, framing:"page", custom_components}`
-   `GET /api/projects/{pid}/catalog.json` → the emitted A2UI v0.9 catalog
    (`$id == catalogId`)
-   `GET /api/projects/{pid}/package` → `{catalog_id, counts:{basic_enabled,
    basic_disabled, custom_active, drafts}, issues:[{level:"error"|"warn",
    component?, message}], files:[path]}` (issues include dependency-graph
    issues)
-   `GET /api/projects/{pid}/package/files` → `{root, files:[{path, size}]}` and
    `GET /api/projects/{pid}/package/files/{path}` → one file inline (the
    Package page browser; relative links in `index.html` work). Both read
    `exports/package.py` `package_files()`, the same source `package.zip` zips.
-   `GET /api/export/projects/{pid}/package.zip` → design package (`README.md`,
    `constitution.md`, `catalog/{catalog.json, rules.txt, examples/<Name>.json,
    components/<Name>/{definition.json, template.html, styles.css,
    composition.json?, compose/<Name>.kt, remote_compose/<Name>Remote.kt,
    swiftui/<Name>.swift, preview.png?, reference.png?},
    components/CatalogRegistry.kt, components/CatalogRegistry.swift}`,
    `templates/{index.json, <slug>/template.json, <slug>/reference.png?}`,
    `imagery/{styles.json, refs/}`, `tokens/{theme.json, tokens.css,
    design-tokens.json}`, `index.html`, `LICENSE-A2UI.txt`). `tokens.css`
    `@import`s any non-default Google Fonts and uses category-aware font
    fallbacks.

**Imagery & SVG Iconography styles** (`imagery_styles` table,
`core/imagery.py`):

-   `GET /api/projects/{pid}/imagery?archived=0|1` → `{styles:[ImageryStyle]}`
-   `POST /api/projects/{pid}/imagery {name, slug?, medium:"raster"|"svg",
    target_slot, incorporation, style_text, aspect_ratio?, scrim?,
    reference_images?:[{data_url|file, name?, caption?}], enabled?:bool}` →
    `{style}` (201)
-   `PATCH /api/projects/{pid}/imagery/{sid}` → `{style}`; `DELETE` → `{ok}`
-   `POST /api/projects/{pid}/imagery/{sid}/preview {prompt?}` → `{style,
    preview_url}`
-   `GET /api/projects/{pid}/imagery/{sid}/refs/{idx}` → reference image file

**Bug reports** (`bug_reports` table, `core/bugs.py`,
`web/components/bug_report.js`, `web/components/diagnostics.js`):

-   `GET /api/bugs/config` → `{config: {component_id, component_path,
    parent_component_id, component_url, child_component_create_url,
    cli_available, snapshot_available, server_started_at, priorities, types}}`
-   `POST /api/bugs/context {project_id?, run_id?, output_id?, import_id?,
    component_id?, session_id?, selected_component?}` → `{context:
    {collected_at, reporter, server, project, constitution, run, output,
    import_ref, custom_component, teach_session}}` (all secrets redacted)
-   `POST /api/bugs/snapshot {path, width?, height?}` → `{snapshot: {data_url,
    width, height, size_bytes, filename}}`
-   `POST /api/bugs {title, description_md, priority?, type?, cc?,
    component_id?, attachments?: [...], scope?, context_summary?, sync?: bool}`
    → `{job}` (202; polls `/api/jobs/{jid}` → `result: {bug}`) or `{bug}`
    (201/200 when `sync: true`)
-   `GET /api/bugs?all=0|1` → `{bugs: [BugReport]}` (`?all=1` requires admin)
-   `GET /api/bugs/{bid}` → `{bug}`
-   `GET /api/bugs/{bid}/bundle.zip` → saved report `description.md`,
    `metadata.json`, and `attachments/*` as a `.zip`
-   `POST /api/bugs/bundle` → builds and downloads a context `.zip`
    (`README.md`, `description.md`, `context.json`, `attachments/*`) directly
    from the form

### Files

-   `GET /files/screens/{output_id}.png`, `/files/thumbs/{output_id}.png`,
    `/files/guidelines/{file}`, `/files/references/{file}`,
    `/files/components/{file}`, `/files/templates/{file}`,
    `/files/imagery/{file}`, `/files/gen/{file}`
-   `GET /api/gen/{fname}`, `GET /gen/img?...`, `GET /gen/svg?...` generated
    raster & SVG icon assets
-   `GET /img/{slug}/{w}x{h}.svg` deterministic labeled placeholder image
-   `GET /docs/{name}.md` package docs (allowlisted) for in-app help
