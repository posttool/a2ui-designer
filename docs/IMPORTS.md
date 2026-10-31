# Imports: analyze before apply

Figma files, Stitch projects, and screenshots enter a project through
**Imports** (`/p/:pid/imports`). Every import goes through four phases with live
step-by-step progress, and nothing reaches tokens, components, templates, or the
constitution until the designer applies a merge plan.

```
ingesting → analyzing → review → planning → plan_ready → merged
                ↑__________| (re-analyze with answers)
```



| Phase | What happens |
| :--- | :--- |
| `ingesting` | Fetch Figma frames + components / Stitch screens / store uploads (persisting images progressively and updating `progress`). Figma frames and Stitch screens also record their text content (`facts.frame_contents` / `facts.screen_contents`). |
| `analyzing` | Gemini splits the material into **extractions**, classifies them, extracts structured project data samples (`analysis.project_data`), and `BrowserPool` crops each extraction's bounding box (`crop_url`). |
| `review` | Designer answers clarifying questions, reclassifies, and adds or merges regions on the interactive canvas. |
| `planning` | Gemini drafts a per-destination merge plan (`tokens`, `component`, `template`, `constitution`, `data`) from confirmed extractions; missing Material 3 colors, typography, and tokens read by the material are filled in deterministically. |
| `plan_ready` | Designer toggles individual plan lines, edits the proposed constitution diff if needed, and applies. |
| `merged` | Applied. The import record links every extraction to the token, custom component, template, data sample, or constitution revision it produced. |
| `error` | Ingest, analysis, or planning failed (`error` has the message; `progress` shows which step failed). |
| `legacy` | Old design references analyzed straight into a chat proposal (can be upgraded via "Analyze as import"). |



## Extraction kinds

*   `component`: a reusable UI building block (card, list row, chip group,
    header bar, poll card…). Composite patterns are drafted as custom A2UI
    components (`draft_custom`), while bare atomic primitives map to Basic
    components (`map_basic`).
*   `template`: a screen or section composed from components; it becomes both a
    reusable **template** object (injected into the A2UI generator prompt under
    `# Templates` and exported in `package.zip`) and layout guidance in the
    constitution.
*   `token_reference`: style reference material (palettes, type ramps, spacing
    or radius sheets, restyled Basic components); it becomes theme tokens.
*   `ignore`: not useful (sample content, annotations, device chrome).

Extraction `status`: `proposed` → (`needs_answer`) → `confirmed` | `ignored` →
`merged`.

## API

All JSON. `{iid}` is the import id (stored in `design_references`).

### Source discovery (Figma & Stitch pickers)

*   `GET /api/figma/projects?refresh=0|1` → `{projects: [FigmaSource]}`: lists
    known Figma files from A2UI Designer imports/components plus saved user sources
    (`users.figma_sources_json`: files, `/v1/projects/{id}/files` folders, and
    `/v1/teams/{id}/projects` teams).
*   `POST /api/figma/projects {url}` → `{source, projects}` (201): registers a
    Figma file, project folder, or team URL for the picker (files are also
    registered automatically when imported).
*   `DELETE /api/figma/projects/{key}` → `{projects}`: removes a saved Figma
    source.
*   `POST /api/figma/frames {url?, file_key?}` → `{file_key, file_name, pages:
    [{id, name, frames: [{id, name, type, width, height, viewport_hint}]}],
    preselect: [node_id]}`: inspects top-level pages, frames, and sections (`GET
    /v1/files/{key}?depth=2`) so the designer can pick up to 20 frames.
*   `GET /api/stitch/projects` → `{projects: [{id, title, url, screen_count,
    design_system, updated_at, source: "stitch"|"a2uid"}]}`: lists owned and
    shared Stitch projects via MCP `list_projects` merged with previously
    imported Stitch projects.
*   `POST /api/stitch/screens {url}` → `{project, screens, design_systems,
    preselect}`: lists screens for the Stitch screen picker (≤20 per import).

### Imports

*   `POST /api/projects/{pid}/references` creates an import (`kind` = `figma` |
    `stitch` | `screenshots`, `url`, `node_ids` for Figma frame selection,
    `screen_ids` for Stitch screen selection, `images` for screenshots, `title`,
    `notes`, plus `origin` = `constitution` | `design` | `imports`, default
    `imports`). Analysis starts automatically when ingest finishes.
*   `GET /api/projects/{pid}/imports` → `{imports: [Import]}` (without
    `extractions`).
*   `GET /api/imports/{iid}` → `{import: Import}` (with `extractions`).
*   `PATCH /api/references/{iid}` / `DELETE /api/references/{iid}`: update
    title, notes, or image captions, or delete the import.
*   `POST /api/imports/{iid}/analyze {instructions?}`: re-run analysis.
    Confirmed/ignored extractions and answers are kept and passed as context;
    unclaimed extractions are matched by id or title.
*   `POST /api/imports/{iid}/confirm {ids?}`: confirm the given extractions, or
    every `proposed` one when `ids` is omitted (never `needs_answer`).
*   `POST /api/imports/{iid}/questions/{qid} {answer: {choice, note}}`: answer
    an import-wide question.

### Extractions

*   `PATCH /api/imports/{iid}/extractions/{xid}` with any of `{kind, title,
    description, status, answer: {choice, note}, region}`. `answer.choice` ∈
    kinds answers the extraction's question: it sets `kind` and moves it to
    `confirmed` (or `ignored` for `ignore`).
*   `POST /api/imports/{iid}/extractions {kind, title, description, region:
    {image_index, box}}`: add a region by hand (status `confirmed`, immediately
    cropped).
*   `DELETE /api/imports/{iid}/extractions/{xid}`.
*   `POST /api/imports/{iid}/extractions/merge {keep, ids}`: fold duplicate
    extractions into `keep` (their regions move to `region.instances`).

### Merge

*   `POST /api/imports/{iid}/merge/prepare {instructions?}`: drafts a merge plan
    (`409` while any extraction is `needs_answer` or any import question is
    unanswered).
*   `POST /api/imports/{iid}/merge/apply {lines: {<line_id>: bool},
    constitution_md?, sample_set_name?}`: applies selected lines (defaults to
    `line.selected`), posts a short summary to Chat, and returns `{import,
    result}`. Selected `data` lines become samples grouped into one project
    sample set (default name `<file> — Project Data`).
*   `POST /api/imports/{iid}/data-source {sample_set_id?}` → `{import,
    sample_set, data_source, settings}`: makes the import's project data sample
    set the project's default run data source (`settings.data_source = {kind:
    "set", sample_set_id, …, import_id, source_kind}`). If the import has not
    been merged yet, the set is first materialized from the plan's `data` lines
    or `analysis.project_data` (400 when the import has no project data).

### Templates (reusable)

*   `GET /api/projects/{pid}/templates` → `{templates: [Template]}`
*   `GET|PATCH|DELETE /api/templates/{tid}`; PATCH accepts `name`,
    `description`, `when_to_use`, `structure_md`, `components`, `status`
    (`active` | `archived`).

## Shapes


```json
// Import
{
  "id": "ref_…", "project_id": "p_…", "kind": "figma|stitch|screenshots",
  "origin": "constitution|design|imports", "title": "…", "notes": "…",
  "phase": "review", "status": "…legacy fetch status…", "error": null,
  "progress": {
    "mode": "import|reanalyze|plan", "step": "crop_regions",
    "label": "Cropping extracted regions (4/6)…", "detail": "6 extractions",
    "done": 4, "total": 6,
    "started_at": "…", "step_started_at": "…", "updated_at": "…",
    "steps": [
      {"id": "fetch_file", "label": "Fetch Figma document & design tokens",
       "status": "done", "detail": "7 frames", "started_at": "…", "finished_at": "…"},
      {"id": "download_frames", "label": "Render & download frame PNGs",
       "status": "done", "detail": "7/7 frames", "started_at": "…", "finished_at": "…"},
      {"id": "scan_components", "label": "Scan Figma components & thumbnails",
       "status": "done", "detail": "4 components", "started_at": "…", "finished_at": "…"},
      {"id": "gemini_analyze", "label": "Separate components, templates & tokens with Gemini",
       "status": "done", "detail": "6 extractions", "started_at": "…", "finished_at": "…"},
      {"id": "crop_regions", "label": "Crop extracted UI regions",
       "status": "running", "detail": "4/6 crops", "started_at": "…", "finished_at": null}
    ]
  },
  "source": {"url": "…", "file_key": "…", "file_name": "…", "node_ids": ["1:2"]},
  "images": [{"url": "/files/references/…", "name": "…", "width": 412,
              "height": 915, "viewport_hint": "phone", "frame_id": "1:2",
              "role": "frame|component|screen|upload", "node_id": "1:2",
              "nodes_file": "ref_…_00.nodes.json" /* Figma frames: trimmed node
              subtree (figma.trim_node_tree) used for plan-time Basic fit and
              box→node resolution; absent on older imports */}],
  "facts": {…, "frame_contents|screen_contents": [/* per frame/screen text */]},
  "analysis": {"summary": "…", "model": "…", "at": "…",
               "project_data": {"samples": [/* sample drafts */]}},
  "counts": {"component": 3, "template": 2, "token_reference": 1,
             "ignore": 0, "open_questions": 2, "merged": 0, "total": 6},
  "questions": [{"id": "q1", "text": "…", "about": ["x_…"],
                 "options": [{"value": "brand", "label": "Brand palette"}],
                 "answer": {"choice": "brand", "note": "", "by": "…",
                            "at": "…"} | null}],
  "merge_plan": MergePlan | null,
  "merge_log": [{"at": "…", "by": "ldap", "revision_id": "r_…" | null,
                 "chat_message_id": "m_…", "applied": [AppliedLine]}],
  "chat_message_id": "m_…" | null,   // legacy chat proposal or merge summary
  "extractions": [Extraction]          // GET /api/imports/{iid} only
}

// Extraction
{
  "id": "x_…", "import_id": "ref_…", "kind": "component", "status":
  "needs_answer", "title": "Order summary", "description": "…",
  "region": {"image_index": 0, "box": [x0, y0, x1, y1] /* 0-1000 of the
             image, or null = whole image */, "node_id": "12:3" | null,
             "instances": [{"image_index": 2, "box": […]}]},
  "crop_url": "/files/references/…png" | null,
  "confidence": 0.55, "rationale": "…",
  "uses": [{"xid": "x_…"} | {"basic": "Card"} | {"component_id": "cc_…"},
           …each with "name"],
  "used_by": ["x_…"],                      // computed
  "question": {"text": "…", "options": [{"value": "component",
               "label": "Reusable component"}, …]} | null,
  "answer": {"choice": "template", "note": "", "by": "…", "at": "…"} | null,
  "payload": {
    "component": {"purpose": "…", "maps_to_basic": ["Card"],
                  "props_schema": {…}, "matches_component_id": null},
    "template": {"when_to_use": "…", "structure_md": "…",
                 "slots": ["header", "list"], "components": ["Card"]},
    "tokens": [{"token": "primary", "value": "#0B57D0",
                "confidence": "exact|approx", "reason": "…"}]
  },
  "merged_into": [{"type": "component|template|tokens|revision|basic",
                   "id": "…", "label": "…", "url": "/p/…"}],
  "created_at": "…", "updated_at": "…"
}

// MergePlan
{
  "prepared_at": "…", "summary": "…",
  "lines": [{
    "id": "l1", "type": "tokens|component|template|constitution|data",
    "selected": true, "label": "…", "from": ["x_…"],
    "detail": {
      // tokens:       {token, before, after, confidence, reason, is_custom,
      //                complementary?}  // complementary = derived M3 color
      // component:    {action: "draft_custom|map_basic|link_existing",
      //                name, basic, component_id, purpose, crop_url}
      // template:     {name, description, when_to_use, structure_md,
      //                components, crop_url}
      // constitution: {constitution_md, change_summary, diff}
      // data:         {title, category, subcategory, intent, size_class,
      //                state, context, payload, edge_cases, tags}
    }
  }]
}

// AppliedLine
{"line_id": "l1", "type": "component", "label": "…",
 "target": {"type": "component", "id": "cc_…", "label": "OrderSummary",
            "url": "/p/p_…/design/components/cc_…"}}

// Template
{"id": "tpl_…", "project_id": "p_…", "name": "Checkout review",
 "description": "…", "when_to_use": "…", "structure_md": "…",
 "components": ["Card", "OrderSummary"], "image_url": "/files/…" | null,
 "source": {"import_id": "ref_…", "extraction_id": "x_…"},
 "status": "active", "created_at": "…", "updated_at": "…"}
```

## Where things land

*   **tokens**: `settings.theme` via `settings.apply_theme_changes` (creates a
    token revision `T<n>`). Before planning, A2UI Designer deterministically:
    *   fills color roles the material does not set with contrast-safe Material
        3 colors (`settings.expand_complementary_color_tokens` +
        `core/material_color_utilities.py`; lines marked `complementary`, left
        out of the LLM prompt, and skipped when unchecked);
    *   backfills `font_family`, `font_family_heading`, `font_family_mono`,
        `heading_weight`, and `body_weight` from Figma/Stitch facts (Stitch
        Tailwind `fontFamily` roles and Google Fonts links) when the import has
        `token_reference` extractions;
    *   adds custom tokens for any `--a2ui-*` token read by extractions,
        templates, the constitution, or drafted import components that the theme
        does not define yet (`settings.resolve_missing_tokens_from_material`;
        `ComponentBuilder` also syncs these while drafting).
*   **component**:
    *   `draft_custom`: queues a custom component in `ComponentBuilder`
        (`queue_import_component`, using Figma node detail when the extraction
        has a `node_id` or the region crop as reference image) with
        `keep_name=True` (preserving the planned PascalCase name so templates
        and constitution rules match) and `source.auto_activate=True`
        (automatically transitioning to `status="active"` once drafted without
        validation issues). How it is built follows the project's
        `component_strategy` (`web_template`, `basic_composition`; see `docs/CONTRACT.md`): Figma nodes compile
        deterministically via `refs/figma_compiler.py`. With `auto`, the plan
        line carries `detail.basic_fit` (computed at plan time from the Figma
        node — found by `node_id` or by resolving the region box on the frame —
        or from the Stitch screen's DOM region; absent for screenshots, whose
        fit comes from Gemini at build time) and `detail.strategy_decision`
        (`{strategy, auto, threshold, fit, reason}`), shown as a "Basic fit NN%
        → Basic/Custom" chip with a per-line **build as** picker; picks are sent
        as `POST /api/imports/{iid}/merge/apply {strategies: {<line_id>:
        "web_template"|"basic_composition"|}}` (body-wide
        `component_strategy` still applies to every line) and pin the strategy
        on the drafted component.
    *   `map_basic`: reserved for bare atomic Basic primitives; enables the
        Basic component while token lines carry the restyle.
    *   `link_existing`: links the extraction to an existing project custom
        component.
*   **template**: an active row in `templates` (Design system → Templates,
    injected into the A2UI generator prompt under `# Templates`, and exported in
    `package.zip` under `assets/`).
*   **constitution**: a new revision with `source = "import"`.
*   **data**: one sample per selected line (`source = "figma"|"stitch"`,
    `meta.import_id`, `meta.extraction_ids`), grouped into a project sample set
    recorded on the plan/merge log (`sample_set_id`); optionally the project's
    default run data source via `POST /api/imports/{iid}/data-source`.
*   **Chat**: one assistant message summarizing what was applied, with
    `context.import_id` (no pending proposal).
