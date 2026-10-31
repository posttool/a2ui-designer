"""Explicit 4-layer design system dependency graph & cross-layer validation.

Tracks relationships across:
  1. Tokens (`settings.theme` & `token_revisions`)
  2. Component Catalog (Basic 18 + `custom_components`)
  3. Templates (`templates` table)
  4. Constitution (`constitution_revisions` rules R1..Rn)
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from core import imagery as imagery_mod
from core import layout_diff
from core import settings as settings_mod
from core.a2ui import catalog as catalog_mod

_CSS_VAR_RE = re.compile(r"--a2ui-[a-z0-9-]+")
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
_IMG_STYLE_RE = re.compile(r"\bstyle=([a-z0-9][a-z0-9_-]*)", re.IGNORECASE)
_COMPONENT_SUFFIX_RE = re.compile(
    r"^[A-Z][A-Za-z0-9]*(?:Card|Banner|Badge|Tile|Header|Footer|Row|Bar|Button|"
    r"Chip|Pill|Carousel|Dialog|Sheet|Modal|Table|Chart|Stepper|Timeline|"
    r"Picker|Input|Field|Avatar|List|Grid|Nav|Tab|Panel|Widget|Item|Block|"
    r"Section|Gauge|Meter|Stat|Hero|Callout|Strip|Drawer|Popover|Tooltip)$"
)
ACTIVE_JOB_STAGES = (
    "queued",
    "extracting",
    "drafting",
    "rendering",
    "comparing",
    "repairing",
)
STAGE_LABELS = {
    "queued": "queued",
    "extracting": "extracting spec",
    "drafting": "drafting renderer",
    "rendering": "rendering variants",
    "comparing": "comparing to reference",
    "repairing": "auto-repairing",
    "error": "build error",
}


def build_ds_snapshot(
    store: Any,
    svc: Any,
    project_row: dict[str, Any],
    token_rev: dict[str, Any] | None = None,
) -> dict[str, Any]:
  """Builds a deterministic snapshot of the project's design system state."""
  pid = project_row["id"]
  s = settings_mod.merged(project_row.get("settings"))
  theme_hash = settings_mod.compute_theme_hash(s["theme"])

  if token_rev is None:
    rows = store.query(
        "SELECT id, number, theme_hash FROM token_revisions WHERE project_id=?"
        " ORDER BY number DESC LIMIT 1",
        [pid],
    )
    if rows:
      token_rev = rows[0]

  cat = svc.catalog_for(project_row)
  basic_all = set(catalog_mod.basic_names())
  enabled_basic = sorted(n for n in cat.enabled_components() if n in basic_all)
  disabled_components = sorted(cat.disabled)
  active_custom = sorted(cat.custom_defs.keys())

  catalog_payload = {
      "enabled_basic": enabled_basic,
      "disabled": disabled_components,
      "custom_defs": {k: cat.custom_defs[k] for k in active_custom},
  }
  catalog_hash = hashlib.sha256(
      json.dumps(catalog_payload, sort_keys=True, separators=(",", ":")).encode(
          "utf-8"
      )
  ).hexdigest()[:12]

  tpl_rows = store.query(
      "SELECT id, name, description, when_to_use, structure_md,"
      " components_json FROM templates WHERE project_id=? AND status='active'"
      " ORDER BY name",
      [pid],
  )
  active_templates = [r["name"] for r in tpl_rows if r.get("name")]
  tpl_payload = [
      {
          "name": r.get("name") or "",
          "when_to_use": r.get("when_to_use") or "",
          "structure_md": r.get("structure_md") or "",
          "components": list(r.get("components") or []),
      }
      for r in tpl_rows
  ]
  templates_hash = hashlib.sha256(
      json.dumps(tpl_payload, sort_keys=True, separators=(",", ":")).encode(
          "utf-8"
      )
  ).hexdigest()[:12]

  styles = imagery_mod.list_styles(store, pid)
  active_imagery_styles = sorted(
      st["slug"]
      for st in styles
      if st.get("slug")
      and st.get("enabled", True)
      and st.get("status", "active") == "active"
  )

  return {
      "token_revision_id": token_rev["id"] if token_rev else None,
      "token_revision_number": token_rev["number"] if token_rev else 1,
      "theme_hash": theme_hash,
      "catalog_hash": catalog_hash,
      "templates_hash": templates_hash,
      "enabled_basic": enabled_basic,
      "disabled_components": disabled_components,
      "active_custom": active_custom,
      "active_templates": active_templates,
      "active_imagery_styles": active_imagery_styles,
  }


def compute_revision_drift(
    bound_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
) -> dict[str, Any]:
  """Compares a constitution revision's saved ds_snapshot to current live state."""
  if not isinstance(bound_snapshot, dict) or not bound_snapshot:
    return {
        "has_drift": False,
        "token_drift": False,
        "catalog_drift": False,
        "templates_drift": False,
        "bound_token_revision_number": None,
        "current_token_revision_number": current_snapshot.get(
            "token_revision_number"
        ),
        "summary": "",
    }
  token_drift = bool(
      bound_snapshot.get("theme_hash")
      and bound_snapshot.get("theme_hash") != current_snapshot.get("theme_hash")
  )
  catalog_drift = bool(
      bound_snapshot.get("catalog_hash")
      and bound_snapshot.get("catalog_hash")
      != current_snapshot.get("catalog_hash")
  )
  templates_drift = bool(
      bound_snapshot.get("templates_hash")
      and bound_snapshot.get("templates_hash")
      != current_snapshot.get("templates_hash")
  )
  has_drift = token_drift or catalog_drift or templates_drift
  parts: list[str] = []
  b_tok = bound_snapshot.get("token_revision_number")
  c_tok = current_snapshot.get("token_revision_number")
  if token_drift:
    if b_tok and c_tok and b_tok != c_tok:
      parts.append(f"Tokens T{b_tok} \u2192 T{c_tok}")
    else:
      parts.append("Tokens modified")
  if catalog_drift:
    parts.append("Component catalog changed")
  if templates_drift:
    parts.append("Templates changed")
  return {
      "has_drift": has_drift,
      "token_drift": token_drift,
      "catalog_drift": catalog_drift,
      "templates_drift": templates_drift,
      "bound_token_revision_number": b_tok,
      "current_token_revision_number": c_tok,
      "summary": " \u00b7 ".join(parts),
  }


def analyze_project_dependencies(
    store: Any,
    svc: Any,
    project_row: dict[str, Any],
    revision_row: dict[str, Any] | None = None,
    text_md: str | None = None,
) -> dict[str, Any]:
  """Analyzes the 4-layer dependency graph and returns edges + issues."""
  pid = project_row["id"]
  svc.migrate_legacy_catalog(project_row)
  s = settings_mod.merged(project_row.get("settings"))
  theme = s["theme"]
  custom_tokens = (
      theme.get("custom_tokens")
      if isinstance(theme.get("custom_tokens"), dict)
      else {}
  )

  if revision_row is None:
    revision_row = svc.current_revision(pid)
  cons_text = (
      text_md
      if text_md is not None
      else ((revision_row or {}).get("text_md") or "")
  )

  # Parse constitution rules
  rules = layout_diff.parse_rules(cons_text)
  if not rules and cons_text.strip():
    rules = [{"id": "R1", "title": "Constitution", "text": cons_text.strip()}]

  # Known catalog components
  basic_names = list(catalog_mod.basic_names())
  basic_set = set(basic_names)
  disabled_set = set(s["catalog"].get("disabled_components") or [])

  custom_rows = store.query(
      "SELECT id, name, status, classification, token_refs_json, issues_json,"
      " job_json FROM custom_components WHERE project_id=? ORDER BY name",
      [pid],
  )
  custom_by_name: dict[str, dict[str, Any]] = {
      r["name"]: r for r in custom_rows if r.get("name")
  }

  # Known templates
  tpl_rows = store.query(
      "SELECT id, name, status, components_json, structure_md, when_to_use"
      " FROM templates WHERE project_id=? ORDER BY name",
      [pid],
  )
  tpl_by_name: dict[str, dict[str, Any]] = {
      r["name"]: r for r in tpl_rows if r.get("name")
  }
  tpl_lower_map: dict[str, str] = {
      r["name"].lower(): r["name"] for r in tpl_rows if r.get("name")
  }

  # Known imagery styles
  style_rows = store.query(
      "SELECT id, slug, name, enabled, status FROM imagery_styles WHERE"
      " project_id=? ORDER BY slug",
      [pid],
  )
  style_by_slug: dict[str, dict[str, Any]] = {
      r["slug"]: r for r in style_rows if r.get("slug")
  }

  # Known CSS vars
  defined_css_vars: set[str] = set(settings_mod.CSS_VAR_TO_TOKEN.keys())
  for k in settings_mod.CANONICAL_TOKEN_KEYS:
    defined_css_vars.add(
        settings_mod.TOKEN_TO_CSS_VAR.get(k, f"--a2ui-{k.replace('_', '-')}")
    )
  for slug in custom_tokens.keys():
    norm_slug = slug.lower().removeprefix("--a2ui-").replace("_", "-")
    defined_css_vars.add(f"--a2ui-{norm_slug}")

  # Tracking structures
  used_by_components: dict[str, dict[str, list[str]]] = {}
  used_by_templates: dict[str, dict[str, list[str]]] = {}
  used_by_tokens: dict[str, dict[str, list[str]]] = {}
  used_by_imagery: dict[str, dict[str, list[str]]] = {}

  def _ensure_comp_usage(name: str) -> dict[str, list[str]]:
    return used_by_components.setdefault(
        name, {"constitution_rules": [], "templates": []}
    )

  def _ensure_tpl_usage(name: str) -> dict[str, list[str]]:
    return used_by_templates.setdefault(name, {"constitution_rules": []})

  def _ensure_tok_usage(css_var: str) -> dict[str, list[str]]:
    return used_by_tokens.setdefault(
        css_var, {"components": [], "constitution_rules": []}
    )

  def _ensure_img_usage(slug: str) -> dict[str, list[str]]:
    return used_by_imagery.setdefault(slug, {"constitution_rules": []})

  by_rule: dict[str, dict[str, list[str]]] = {}
  cons_comp_rules: dict[str, list[str]] = {}
  cons_tpl_rules: dict[str, list[str]] = {}
  cons_tok_rules: dict[str, list[str]] = {}
  cons_img_rules: dict[str, list[str]] = {}

  # 1. Scan constitution rules
  all_known_comp_names = list(basic_set | set(custom_by_name.keys()))
  comp_patterns = {
      name: re.compile(rf"\b{re.escape(name)}\b")
      for name in all_known_comp_names
  }

  for r in rules:
    rid = r["id"]
    blob = f"{r.get('title') or ''}\n{r.get('text') or ''}"
    rule_comps: list[str] = []
    rule_tpls: list[str] = []
    rule_toks: list[str] = []
    rule_imgs: list[str] = []

    # Templates first so backticked template names aren't mistaken for unknown components
    blob_lower = blob.lower()
    for t_low, t_name in tpl_lower_map.items():
      if len(t_low) >= 3 and t_low in blob_lower:
        if t_name not in rule_tpls:
          rule_tpls.append(t_name)
        if rid not in cons_tpl_rules.setdefault(t_name, []):
          cons_tpl_rules[t_name].append(rid)
        u = _ensure_tpl_usage(t_name)
        if rid not in u["constitution_rules"]:
          u["constitution_rules"].append(rid)

    # Known components (Basic + Custom)
    for cname, pat in comp_patterns.items():
      if pat.search(blob):
        if cname not in rule_comps:
          rule_comps.append(cname)
        if rid not in cons_comp_rules.setdefault(cname, []):
          cons_comp_rules[cname].append(rid)
        u = _ensure_comp_usage(cname)
        if rid not in u["constitution_rules"]:
          u["constitution_rules"].append(rid)

    # Backticked tokens or unknown PascalCase components
    for bt in _BACKTICK_RE.findall(blob):
      token_cand = bt.strip()
      if not token_cand:
        continue
      if (
          token_cand in settings_mod.CANONICAL_TOKEN_KEYS
          and token_cand != "space"
      ):
        cvar = settings_mod.TOKEN_TO_CSS_VAR.get(
            token_cand, f"--a2ui-{token_cand.replace('_', '-')}"
        )
        if cvar not in rule_toks:
          rule_toks.append(cvar)
        if rid not in cons_tok_rules.setdefault(cvar, []):
          cons_tok_rules[cvar].append(rid)
        u = _ensure_tok_usage(cvar)
        if rid not in u["constitution_rules"]:
          u["constitution_rules"].append(rid)
      elif (
          _COMPONENT_SUFFIX_RE.match(token_cand)
          and token_cand not in basic_set
          and token_cand not in custom_by_name
          and token_cand.lower() not in tpl_lower_map
      ):
        if token_cand not in rule_comps:
          rule_comps.append(token_cand)
        if rid not in cons_comp_rules.setdefault(token_cand, []):
          cons_comp_rules[token_cand].append(rid)
        u = _ensure_comp_usage(token_cand)
        if rid not in u["constitution_rules"]:
          u["constitution_rules"].append(rid)

    # Explicit `--a2ui-*` CSS variables
    for cvar in _CSS_VAR_RE.findall(blob):
      if cvar not in rule_toks:
        rule_toks.append(cvar)
      if rid not in cons_tok_rules.setdefault(cvar, []):
        cons_tok_rules[cvar].append(rid)
      u = _ensure_tok_usage(cvar)
      if rid not in u["constitution_rules"]:
        u["constitution_rules"].append(rid)

    # Imagery style references (`style=slug` or known style slugs)
    for m in _IMG_STYLE_RE.findall(blob):
      slug = m.lower()
      if slug not in rule_imgs:
        rule_imgs.append(slug)
      if rid not in cons_img_rules.setdefault(slug, []):
        cons_img_rules[slug].append(rid)
      u = _ensure_img_usage(slug)
      if rid not in u["constitution_rules"]:
        u["constitution_rules"].append(rid)
    for slug in style_by_slug:
      if re.search(rf"\b{re.escape(slug)}\b", blob, re.IGNORECASE):
        if slug not in rule_imgs:
          rule_imgs.append(slug)
        if rid not in cons_img_rules.setdefault(slug, []):
          cons_img_rules[slug].append(rid)
        u = _ensure_img_usage(slug)
        if rid not in u["constitution_rules"]:
          u["constitution_rules"].append(rid)

    by_rule[rid] = {
        "title": r.get("title") or "",
        "components": sorted(rule_comps),
        "templates": sorted(rule_tpls),
        "tokens": sorted(rule_toks),
        "imagery_styles": sorted(rule_imgs),
    }

  # 2. Scan templates -> components
  issues: list[dict[str, Any]] = []
  for tpl in tpl_rows:
    tname = tpl.get("name") or ""
    tstatus = tpl.get("status") or "active"
    tcomps = list(tpl.get("components") or [])
    # Also scan structure_md for known component names
    s_md = tpl.get("structure_md") or ""
    for cname, pat in comp_patterns.items():
      if pat.search(s_md) and cname not in tcomps:
        tcomps.append(cname)
    for cname in tcomps:
      if not cname:
        continue
      u = _ensure_comp_usage(cname)
      if tstatus == "active" and tname not in u["templates"]:
        u["templates"].append(tname)
      if tstatus != "active":
        continue
      # Validate template -> component edge for active templates
      if cname in basic_set:
        if cname in disabled_set:
          issues.append({
              "kind": "template_disabled_component",
              "level": "warn",
              "target_kind": "component",
              "target_name": cname,
              "component": cname,
              "template_names": [tname],
              "rule_ids": [],
              "message": (
                  f"Template \u201c{tname}\u201d uses Basic component"
                  f" \u201c{cname}\u201d, which is currently disabled in the"
                  " catalog."
              ),
          })
      elif cname in custom_by_name:
        c_row = custom_by_name[cname]
        c_st = c_row.get("status") or "draft"
        if c_st == "draft":
          issues.append({
              "kind": "template_draft_component",
              "level": "warn",
              "target_kind": "component",
              "target_name": cname,
              "component": cname,
              "component_id": c_row["id"],
              "template_names": [tname],
              "rule_ids": [],
              "message": (
                  f"Template \u201c{tname}\u201d uses custom component"
                  f" \u201c{cname}\u201d, which is still a draft (not active)."
              ),
          })
        elif c_st == "archived":
          issues.append({
              "kind": "template_archived_component",
              "level": "error",
              "target_kind": "component",
              "target_name": cname,
              "component": cname,
              "component_id": c_row["id"],
              "template_names": [tname],
              "rule_ids": [],
              "message": (
                  f"Template \u201c{tname}\u201d uses custom component"
                  f" \u201c{cname}\u201d, which is archived."
              ),
          })
      else:
        issues.append({
            "kind": "template_unknown_component",
            "level": "warn",
            "target_kind": "component",
            "target_name": cname,
            "component": cname,
            "template_names": [tname],
            "rule_ids": [],
            "message": (
                f"Template \u201c{tname}\u201d references component"
                f" \u201c{cname}\u201d, which is not in the catalog."
            ),
        })

  # 3. Scan custom components -> tokens & build/validation state
  building_components: list[dict[str, Any]] = []
  for c_row in custom_rows:
    if c_row.get("status") == "archived":
      continue
    cname = c_row.get("name") or ""
    c_status = c_row.get("status") or "draft"
    job = c_row.get("job") if isinstance(c_row.get("job"), dict) else {}
    stage = str(job.get("stage") or "")
    c_rids = cons_comp_rules.get(cname, [])
    c_tnames = used_by_components.get(cname, {}).get("templates", [])
    if stage in ACTIVE_JOB_STAGES:
      building_components.append({
          "id": c_row["id"],
          "name": cname,
          "status": c_status,
          "stage": stage,
          "stage_label": STAGE_LABELS.get(stage, stage),
          "rule_ids": c_rids,
          "template_names": c_tnames,
      })
    elif stage == "error" and (c_status == "active" or c_rids or c_tnames):
      err_detail = str(job.get("error") or "Component build failed").strip()
      issues.append({
          "kind": "component_build_error",
          "level": "error",
          "target_kind": "component",
          "target_name": cname,
          "component": cname,
          "component_id": c_row["id"],
          "rule_ids": c_rids,
          "template_names": c_tnames,
          "message": (
              f"Custom component \u201c{cname}\u201d has a build error:"
              f" {err_detail}."
          ),
      })
    comp_issues = [str(x) for x in c_row.get("issues") or [] if x]
    if (
        comp_issues
        and stage not in ACTIVE_JOB_STAGES
        and (c_status == "active" or c_rids or c_tnames)
    ):
      issues.append({
          "kind": "component_validation_error",
          "level": "error",
          "target_kind": "component",
          "target_name": cname,
          "component": cname,
          "component_id": c_row["id"],
          "rule_ids": c_rids,
          "template_names": c_tnames,
          "message": (
              f"Custom component \u201c{cname}\u201d has validation errors:"
              f" {'; '.join(comp_issues[:2])}."
          ),
      })
    for tref in c_row.get("token_refs") or []:
      if not isinstance(tref, str) or not tref.startswith("--a2ui-"):
        continue
      u = _ensure_tok_usage(tref)
      if cname not in u["components"]:
        u["components"].append(cname)
      if tref not in defined_css_vars:
        issues.append({
            "kind": "component_missing_token",
            "level": "warn",
            "target_kind": "token",
            "target_name": tref,
            "component": cname,
            "component_id": c_row["id"],
            "template_names": [],
            "rule_ids": [],
            "message": (
                f"Component \u201c{cname}\u201d reads token {tref}, which is"
                " not defined in project tokens."
            ),
        })

  # 4. Build constitution dependency list + validate constitution edges
  cons_components: list[dict[str, Any]] = []
  for cname in sorted(cons_comp_rules.keys()):
    rids = cons_comp_rules[cname]
    is_building = False
    stage = ""
    if cname in basic_set:
      enabled = cname not in disabled_set
      status = "active" if enabled else "disabled"
      kind = "basic"
      cid = None
      if not enabled:
        issues.append({
            "kind": "constitution_disabled_component",
            "level": "error",
            "target_kind": "component",
            "target_name": cname,
            "component": cname,
            "rule_ids": rids,
            "template_names": [],
            "message": (
                f"Constitution ({', '.join(rids)}) references Basic component"
                f" \u201c{cname}\u201d, which is disabled in the catalog."
            ),
        })
    elif cname in custom_by_name:
      c_row = custom_by_name[cname]
      status = c_row.get("status") or "draft"
      kind = "custom"
      cid = c_row["id"]
      job = c_row.get("job") if isinstance(c_row.get("job"), dict) else {}
      stage = str(job.get("stage") or "")
      is_building = stage in ACTIVE_JOB_STAGES
      if status == "draft":
        stage_lbl = STAGE_LABELS.get(stage, stage)
        msg = (
            f"Constitution ({', '.join(rids)}) references custom component"
            f" \u201c{cname}\u201d, which is currently building ({stage_lbl})"
            " and still in draft status."
            if is_building
            else (
                f"Constitution ({', '.join(rids)}) references custom component"
                f" \u201c{cname}\u201d, which is still in draft status."
            )
        )
        issues.append({
            "kind": "constitution_draft_component",
            "level": "warn",
            "target_kind": "component",
            "target_name": cname,
            "component": cname,
            "component_id": cid,
            "building": is_building,
            "stage": stage,
            "rule_ids": rids,
            "template_names": [],
            "message": msg,
        })
      elif status == "archived":
        issues.append({
            "kind": "constitution_archived_component",
            "level": "error",
            "target_kind": "component",
            "target_name": cname,
            "component": cname,
            "component_id": cid,
            "rule_ids": rids,
            "template_names": [],
            "message": (
                f"Constitution ({', '.join(rids)}) references custom component"
                f" \u201c{cname}\u201d, which is archived."
            ),
        })
    else:
      status = "missing"
      kind = "unknown"
      cid = None
      issues.append({
          "kind": "constitution_unknown_component",
          "level": "warn",
          "target_kind": "component",
          "target_name": cname,
          "component": cname,
          "rule_ids": rids,
          "template_names": [],
          "message": (
              f"Constitution ({', '.join(rids)}) references component"
              f" \u201c{cname}\u201d, which does not exist in the catalog."
          ),
      })
    cons_components.append({
        "name": cname,
        "kind": kind,
        "status": status,
        "id": cid,
        "component_id": cid,
        "building": is_building,
        "stage": stage,
        "rules": rids,
        "rule_ids": rids,
    })

  cons_templates: list[dict[str, Any]] = []
  for tname in sorted(cons_tpl_rules.keys()):
    rids = cons_tpl_rules[tname]
    t_row = tpl_by_name.get(tname)
    status = (t_row.get("status") or "active") if t_row else "missing"
    tid = t_row["id"] if t_row else None
    if status == "archived":
      issues.append({
          "kind": "constitution_archived_template",
          "level": "warn",
          "target_kind": "template",
          "target_name": tname,
          "template_id": tid,
          "rule_ids": rids,
          "template_names": [tname],
          "message": (
              f"Constitution ({', '.join(rids)}) references template"
              f" \u201c{tname}\u201d, which is archived."
          ),
      })
    cons_templates.append({
        "name": tname,
        "status": status,
        "id": tid,
        "template_id": tid,
        "components": list((t_row or {}).get("components") or []),
        "rules": rids,
        "rule_ids": rids,
    })

  cons_tokens: list[dict[str, Any]] = []
  for cvar in sorted(cons_tok_rules.keys()):
    rids = cons_tok_rules[cvar]
    defined = cvar in defined_css_vars
    if not defined:
      issues.append({
          "kind": "constitution_missing_token",
          "level": "warn",
          "target_kind": "token",
          "target_name": cvar,
          "rule_ids": rids,
          "template_names": [],
          "message": (
              f"Constitution ({', '.join(rids)}) references token {cvar},"
              " which is not defined in project tokens."
          ),
      })
    cons_tokens.append({
        "token": cvar,
        "css_var": cvar,
        "defined": defined,
        "status": "defined" if defined else "undefined",
        "rules": rids,
        "rule_ids": rids,
    })

  cons_imagery: list[dict[str, Any]] = []
  for slug in sorted(cons_img_rules.keys()):
    rids = cons_img_rules[slug]
    st_row = style_by_slug.get(slug)
    if not st_row:
      status = "missing"
    elif st_row.get("status") == "archived" or not st_row.get("enabled", 1):
      status = "disabled"
    else:
      status = "active"
    if status != "active":
      issues.append({
          "kind": "constitution_missing_imagery_style",
          "level": "warn",
          "target_kind": "imagery_style",
          "target_name": slug,
          "rule_ids": rids,
          "template_names": [],
          "message": (
              f"Constitution ({', '.join(rids)}) references imagery style"
              f" \u201c{slug}\u201d, which is {status}."
          ),
      })
    cons_imagery.append({
        "slug": slug,
        "name": (st_row or {}).get("name") or slug,
        "status": status,
        "rules": rids,
        "rule_ids": rids,
    })

  layer_map = {
      "component": "components",
      "template": "templates",
      "token": "tokens",
      "imagery_style": "imagery",
  }
  for iss in issues:
    iss.setdefault("target", iss.get("target_name"))
    iss.setdefault(
        "layer", layer_map.get(iss.get("target_kind") or "", "components")
    )

  snapshot = build_ds_snapshot(store, svc, project_row)
  bound_snap = (revision_row or {}).get("ds_snapshot")
  revision_drift = compute_revision_drift(bound_snap, snapshot)

  return {
      "constitution_deps": {
          "components": cons_components,
          "templates": cons_templates,
          "tokens": cons_tokens,
          "imagery": cons_imagery,
          "imagery_styles": cons_imagery,
          "by_rule": by_rule,
      },
      "used_by": {
          "components": used_by_components,
          "templates": used_by_templates,
          "tokens": used_by_tokens,
          "imagery_styles": used_by_imagery,
      },
      "issues": issues,
      "building_components": building_components,
      "snapshot": snapshot,
      "bound_snapshot": bound_snap,
      "revision_drift": revision_drift,
  }
