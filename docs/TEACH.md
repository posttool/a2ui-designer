# Teaching sessions & the Teachings ledger — API contract

A teaching session is a slower, learning-oriented wizard: the designer leaves
many anchored observations per card, Gemini distills them into candidate rules
that are confirmed as you go, rules are generalized to other library scenarios
("where else does this apply?") and tried there ("Try it" → probe card), layout
templates are captured from examples, and Finish assembles the constitution
from the confirmed rules and runs Before vs. After as an effect study.

Every learned thing (a rule or a template) is a **teaching** with a stable id,
**phrasings** (versions, each with the reason it was rephrased) and **trials**
(outputs where it was expected to apply and whether the model followed it).
Trials come from the judge (`findings[].rule_id`), effect attribution
(`changes[].rule_ids` / `unapplied_rules`), designer observations about a
teaching, and a structural match for templates. Every constitution revision is
bound to teachings (`rule_bindings`), including revisions written in the
Studio editor or via chat, so rephrasings outside sessions are tracked too.

Backend: `sim/teach.py` (`TeachManager`), `sim/teach_ledger.py`
(`TeachLedger`), `core/data_links.py`. Prompts:
`llm/prompts/teach_{distill,applications,pattern,voice_turn}.md`.
UI: `web/views/{teach,teach_immersive,teachings}.js` (the workspace opens in immersive mode; `I` switches to Expert mode with the data / card / teaching panes; the choice is remembered in `localStorage a2uid.teach.mode`),
`web/components/{datatree,region_select,before_after,pattern_capture,voice}.js`.

## Objects

### Session (`session`)

```jsonc
{"id": "ts_…", "project_id": "p_…", "name": "Session 1", "status": "teaching|finishing|done",
 "brief": "", "groups": ["assistant"], "base_revision_id": "r_…", "revision_id": null,
 "baseline_run_id": "run_…", "after_run_id": null, "effect_id": null,
 "summary": {}, "created_by": "alice", "created_at": "…", "updated_at": "…"}
```

### Card (`cards[]`, ordered)

```jsonc
{"index": 0, "output_id": "o_…", "sample_id": "s_…", "run_id": "run_…",
 "origin": "baseline|added|probe", "teaching_id": null,          // probe: the teaching it tests
 "output": { /* RunManager.fmt_output(full=True): id, status, a2ui, screenshot_url,
               thumb_url, render_url, judge, judge_score, error, … */ },
 "sample": {"id", "title", "intent", "context", "payload", "genre", "group", "size_class"},
 "observation_count": 3}
```

### Observation

```jsonc
{"id": "ob_…", "session_id": "ts_…", "output_id": "o_…", "sample_id": "s_…",
 "polarity": "good|bad|note", "text": "…",
 "anchors": [
   {"kind": "component", "key": "price|/cart/0", "id": "price", "scope": "/cart/0", "component": "Text", "label": "Text \"$19.50\""},
   {"kind": "data", "path": "/draft_cart/0/price_usd", "value_preview": "19.5", "represented": "bound|literal|none"},
   {"kind": "relation", "path": "/totals/total_usd", "key": "t7|", "component": "Text"},
   {"kind": "region", "box": [x, y, w, h]}              // CSS px in the card's coordinate space
 ],
 "crop_url": "/files/teach/ob_….png" | null,
 "about_teaching_id": "t_…" | null, "status": "open|distilled|dismissed",
 "teaching_ids": ["t_…"], "source": "composer|voice|typed", "transcript": "",
 "created_by": "alice", "created_at": "…"}
```

Component anchors may also use a data path with `*` for "every row"
(`/draft_cart/*/price_usd`); the server keeps it as written.

### Teaching

```jsonc
{"id": "t_…", "project_id": "p_…", "session_id": "ts_…" | null,
 "kind": "rule|pattern", "label": "R3" | "T1" | "C2",           // R = confirmed rule in assembled order, T = template, C = candidate
 "title": "…", "body_md": "…", "section": "Actions", "polarity": "do|dont",
 "applies_when": "…", "data_signature": "…", "version": 2,
 "status": "candidate|confirmed|dismissed|merged|retired|escalated",
 "origin": "distill|user|revision|pattern", "confidence": 0.8,
 "source_observation_ids": ["ob_…"], "supersedes_id": null,
 "template_id": "tpl_…" | null, "template": { /* fmt_template incl. image_url */ } | null,
 "applications": {"applies": [{"sample_id", "title", "genre", "how_it_applies", "relevance", "probe_output_id"}],
                  "exceptions": [{"sample_id", "title", "why"}], "none_reason": "", "computed_at", "for_version"} | null,
 "applications_status": "none|ready|error",
 "theme_changes": [{"token": "radius_card", "value": "20", "reason": "…"}],
 "escalation_guideline_id": null,
 "suggestion": {"kind": "template|escalation", "reason": "…", "at": "…"} | null,   // from distill
 "refines": "t_…" | null,          // candidate that rewords / merges an existing teaching (confirming it adds a phrasing there)
 "reason": "…",                    // why the model proposed / reworded it
 "efficacy": {"trials": 4, "expected": 4, "followed": 0, "partial": 0, "not_followed": 4,
              "rate": 0.0, "phrasings": 2,
              "by_version": [{"version": 1, "trials": 2, "followed": 0, "rate": 0.0}, …],
              "signal": "untested|reliable|flaky|not_followed|rephrased_no_gain|retired|escalated|candidate",
              "summary": "followed 0 / 4 · 2 phrasings", "last_trial_at": "…"},
 "created_at": "…", "updated_at": "…"}
```

Signals (`TeachLedger.SIGNALS`): *reliable* ≥ 3 trials and ≥ 80 % followed;
*flaky* ≥ 3 trials, 30–80 %; *not_followed* ≥ 2 trials, < 30 %;
*rephrased_no_gain* ≥ 2 phrasings each with ≥ 2 trials and none ≥ 50 %
(takes precedence over *not_followed*). `partial` counts as half followed.

### Phrasing (`versions[]`) and trial (`trials[]`)

```jsonc
{"id": "tv_…", "teaching_id": "t_…", "number": 2, "title", "body_md", "section", "applies_when",
 "reason": "not followed in 3 trials → rephrased", "source": "distill|user|revision|chat|import|teach",
 "revision_id": "r_…" | null, "created_by", "created_at",
 "trials": 4, "followed": 1, "rate": 0.25}

{"id": "tt_…", "teaching_id": "t_…", "version": 2, "output_id": "o_…", "run_id", "run_kind",
 "sample_id", "revision_id", "expected": "yes|no|unsure", "followed": "yes|partial|no|unknown",
 "source": "judge|effect|designer|structure", "evidence": {…}, "created_at",
 "output": {"id", "thumb_url", "screenshot_url", "sample_title"}}
```

## Routes

All JSON; auth required. `state` = the full session state (below).

| Method & path | Body | Returns |
|---|---|---|
| `GET /api/projects/{pid}/teach` | — | `{sessions: [session + {cards: n, observations: n, confirmed: n}]}` |
| `POST /api/projects/{pid}/teach` | `{name?, brief?, groups?, count? (6, ≤8), sample_ids?}` | `state` (201) |
| `GET /api/teach/{sid}` | — | `state` |
| `DELETE /api/teach/{sid}` | — | `{ok}` (keeps runs, templates, ledger) |
| `POST /api/teach/{sid}/cards` | `{sample_ids}` or `{brief, count}` | `state` |
| `POST /api/teach/{sid}/observations` | `{output_id, polarity, text, anchors, about_teaching_id?, crop_data_url?, source?, transcript?}` | `{observation, state}` (201) |
| `PATCH /api/teach/observations/{obid}` | any of `polarity, text, anchors, about_teaching_id, status` | `{observation}` |
| `DELETE /api/teach/observations/{obid}` | — | `{ok}` |
| `POST /api/teach/{sid}/distill` | `{output_id?}` | `{result: {reply_md, created, updated, out_of_scope, theme_changes}, state}` |
| `POST /api/teach/{sid}/assemble` | `{polish?}` | `{constitution_md, diff: {unified, rows:[{type,a,b}]}, sections, rule_map: {R1: {t, v}}, bind_hint: {R1: tid}, theme_changes: [{token, value, reason}], guideline_preview: [{teaching_id, kind, title, body_md, output_id, crops:[url]}], polished, note}` |
| `POST /api/teach/{sid}/finish` | `{constitution_md, bind_hint, apply_theme: [token…], guideline_teaching_ids?}` | `state` |
| `POST /api/teach/{sid}/graduate` | — | `state` |
| `POST /api/teach/{sid}/voice_turn` | `{output_id, transcript, source: voice\|typed, pointed: [anchor], pending: {polarity,text,anchors}\|null, question: str\|null, capture?: bool}` | `{intent, observation, question, quick_replies, say, navigate, template_from, captured: observation\|null}` |
| `GET /api/projects/{pid}/teachings` | `?status=&kind=` | `{teachings, summary, thresholds}` |
| `GET /api/teachings/{tid}` | — | `{teaching, versions, trials, guidelines}` |
| `PATCH /api/teachings/{tid}` | any of `status, title, body_md, section, applies_when, polarity, reason` | `{teaching}` (text change → new phrasing) |
| `DELETE /api/teachings/{tid}` | — | `{ok}` |
| `POST /api/teachings/{tid}/applications` | — | `{teaching}` |
| `POST /api/teachings/{tid}/probe` | `{session_id, sample_id}` | `state` |
| `POST /api/teachings/{tid}/rephrase` | `{}` | `{suggestion: {body_md, applies_when, reason}}` (apply with PATCH) |
| `POST /api/teachings/{tid}/escalate` | `{note?}` | `{teaching, guideline}` |
| `POST /api/teachings/{tid}/split` | `{version}` | `{teaching, new_teaching}` |
| `POST /api/teach/patterns/draft` | `{output_id, selection: {key}\|{region:[x,y,w,h]}\|{whole:true}}` | `{draft: {name, description, structure_md, components, skeleton, data_paths, triggers:[{group, text, on}], when_to_use, outline}}` |
| `POST /api/teach/patterns/check` | `{session_id\|output_id\|project_id, name, structure_md, when_to_use, examples:[{sample_id, applies}], exclude_sample_ids?}` | `{applies, exceptions, none_reason, when_to_use}` |
| `POST /api/teach/patterns` | `{session_id?, output_id, selection, crop_data_url?, name, description, structure_md, components, skeleton, when_to_use, examples?, triggers?}` | `{template, teaching}` (201) |
| `GET /api/outputs/{oid}/links` | — | links (below) |

### Session state

```jsonc
{"session": {…}, "project": {…}, "revision": {…current…},
 "cards": [card…], "observations": [obs…], "teachings": [teaching…],
 "runs": {"run_…": {"id", "kind", "status", "counts"}},
 "effect": {"id", "status", "analysis_status", "pairs": {"<sample_id>|phone": {summary, overall, changes, unapplied_rules}}, "rules": [...]} | null,
 "busy": true}
```

`effect.pairs[key]` carries `control` / `treatment` (formatted outputs),
`verdict`, `changes[].attribution.{cause, rule_ids, explanation}`, `summary`,
`overall` and `unapplied_rules: [{rule_id, why}]`. Distill `out_of_scope` is
`[{observation_id, why, suggested_home}]`. Judge findings carry
`applies: yes|no`; only applicable findings become trials.

`busy` is true while any session run is queued/running or the effect analysis
is pending/running; poll `GET /api/teach/{sid}` every ~2.5 s only then.

### Links (`GET /api/outputs/{oid}/links`)

```jsonc
{"leaves": [{"path": "/draft_cart/0/price_usd", "key": "price_usd", "depth": 3, "kind": "text|number|bool|image|url|null|object|array",
             "value_preview": "19.5", "represented": "bound|literal|none", "components": ["cart-item-price|/cart/0"],
             "count": 4 /* arrays: length */}],
 "components": [{"key": "id|scope", "id", "scope", "type", "label", "reads": [paths], "literal": [paths],
                 "box": {"x", "y", "w", "h"} | null}],
 "unrepresented": ["/context/user_state"],
 "coverage": {"represented": 22, "total": 25}, "outline": "Card #root …",
 "height": 1147}
```

`leaves` lists containers too (`kind: object|array`, with `count` for arrays;
`represented: container` for objects, `partial` for arrays whose items are
shown) so the tree can be drawn directly in document order (`depth` = indent).
Paths are JSON pointers relative to the sample **payload**
(`/draft_cart/0/price_usd`); the sample context appears as extra leaves with
paths `context.<key>` (no leading slash). Every-row anchors may use `*`
(`/draft_cart/*/price_usd`). `box` uses the web render's CSS
px (same coordinate space as `render.html` and the 412-px-wide screenshot ×
device scale 2).

## Renderer messages (`web/render.html?inspect=1`)

`a2ui-select` / `a2ui-highlight` accept `ids: ["id|scope", "id"]` in addition
to `id`; an `id|scope` targets one template row (`"id|"` = the unscoped
instance). Implemented in `web/render.html`. `a2ui-click` / `a2ui-hover`
already post `{id, scope}`.

## Runs

Run kinds: `teach_baseline` (no judge), `teach_more`, `teach_probe`,
`teach_after` (judge on; `config.rule_map` maps `R1..`/`T1..` → teaching ids;
`teach_more`/`teach_probe` use `constitution_override` = the assembled
confirmed rules). They are hidden from the plain Runs list like wizard runs.

## Voice

`voice_turn` interprets one utterance (or typed line) with
`teach_voice_turn.md`. The model authors `intent`, `observation.polarity` and
`observation.anchors` (from the card's component keys and data paths); the
server validates anchors against the links and returns them as anchor
objects. `intent=capture` (or `capture: true`) persists the pending
observation with `source` and `transcript`. Speech recognition runs in the
browser (`SpeechRecognition`), which requires a secure context
(`./run.sh --https` or a `localhost` forward); typing works everywhere.
