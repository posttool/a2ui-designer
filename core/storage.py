"""SQLite storage: schema + thread-safe repository helpers.

One connection per thread (WAL). Rows come back as plain dicts with JSON
columns (suffix `_json`) decoded into keys without the suffix.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Any, Iterable

from core import util

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  ldap TEXT PRIMARY KEY, display_name TEXT, is_admin INTEGER DEFAULT 0,
  created_at TEXT, last_seen_at TEXT);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, owner_ldap TEXT, name TEXT, description TEXT,
  current_revision_id TEXT, settings_json TEXT, archived INTEGER DEFAULT 0,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS constitution_revisions (
  id TEXT PRIMARY KEY, project_id TEXT, number INTEGER, text_md TEXT,
  summary TEXT, source TEXT, parent_id TEXT, chat_message_id TEXT,
  author_ldap TEXT, created_at TEXT, ds_snapshot_json TEXT);
CREATE INDEX IF NOT EXISTS rev_proj ON constitution_revisions(project_id, number);
CREATE TABLE IF NOT EXISTS token_revisions (
  id TEXT PRIMARY KEY, project_id TEXT, number INTEGER, theme_hash TEXT,
  theme_json TEXT, diff_json TEXT, summary TEXT, source TEXT,
  author_ldap TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS token_rev_proj ON token_revisions(project_id, number);
CREATE TABLE IF NOT EXISTS chat_messages (
  id TEXT PRIMARY KEY, project_id TEXT, role TEXT, text_md TEXT,
  context_json TEXT, proposal_json TEXT, out_of_scope_json TEXT,
  proposal_status TEXT, accepted_revision_id TEXT, author_ldap TEXT,
  created_at TEXT);
CREATE INDEX IF NOT EXISTS chat_proj ON chat_messages(project_id, created_at);
CREATE TABLE IF NOT EXISTS samples (
  id TEXT PRIMARY KEY, owner_ldap TEXT, grp TEXT, genre TEXT, title TEXT,
  intent TEXT, context_json TEXT, payload_json TEXT, size_class TEXT,
  state TEXT, edge_cases_json TEXT, tags_json TEXT, source TEXT,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS sample_sets (
  id TEXT PRIMARY KEY, name TEXT, description TEXT, owner_ldap TEXT,
  sample_ids_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, project_id TEXT, revision_id TEXT, sample_set_id TEXT,
  sample_ids_json TEXT, viewports_json TEXT, renderers_json TEXT,
  config_json TEXT, status TEXT, created_by TEXT, created_at TEXT,
  started_at TEXT, finished_at TEXT);
CREATE INDEX IF NOT EXISTS runs_proj ON runs(project_id, created_at);
CREATE TABLE IF NOT EXISTS outputs (
  id TEXT PRIMARY KEY, run_id TEXT, project_id TEXT, revision_id TEXT,
  sample_id TEXT, viewport TEXT, status TEXT, a2ui_json TEXT,
  prompt_text TEXT, model TEXT, latency_ms INTEGER, repairs INTEGER,
  validation_json TEXT, screenshot_path TEXT, thumb_path TEXT,
  render_meta_json TEXT, renders_json TEXT, judge_json TEXT, judge_score REAL,
  human_verdict TEXT, human_note TEXT, reviewed_by TEXT, reviewed_at TEXT,
  error TEXT, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS out_run ON outputs(run_id);
CREATE TABLE IF NOT EXISTS guidelines (
  id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, title TEXT, body_md TEXT,
  tags_json TEXT, screenshots_json TEXT, output_id TEXT, sample_id TEXT,
  revision_id TEXT, renderer TEXT DEFAULT 'web', fixable TEXT, status TEXT,
  escalation_note TEXT, addressed_revision_id TEXT, created_by TEXT,
  created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS gl_proj ON guidelines(project_id);
CREATE TABLE IF NOT EXISTS bundles (
  id TEXT PRIMARY KEY, kind TEXT, project_ids_json TEXT,
  guideline_ids_json TEXT, path TEXT, size_bytes INTEGER, counts_json TEXT,
  created_by TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS effects (
  id TEXT PRIMARY KEY, project_id TEXT, revision_id TEXT,
  treatment_run_id TEXT, control_run_id TEXT, noise_run_id TEXT,
  options_json TEXT, status TEXT, analysis_json TEXT, error TEXT,
  created_by TEXT, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS eff_proj ON effects(project_id, created_at);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, kind TEXT, status TEXT, progress_json TEXT,
  message TEXT, result_json TEXT, error TEXT, created_by TEXT,
  created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS categories (
  id TEXT PRIMARY KEY, kind TEXT, parent_id TEXT, label TEXT,
  description TEXT, owner_ldap TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS image_cache (
  query TEXT PRIMARY KEY, results_json TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS connected_apps (
  id TEXT PRIMARY KEY, owner_ldap TEXT, name TEXT, description TEXT, url TEXT,
  headers_json TEXT, kind TEXT, server_info_json TEXT, tools_json TEXT,
  resources_json TEXT, status TEXT, last_error TEXT, last_sync_at TEXT,
  created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS apps_owner ON connected_apps(owner_ldap);
CREATE TABLE IF NOT EXISTS design_references (
  id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, title TEXT, status TEXT,
  error TEXT, source_json TEXT, images_json TEXT, facts_json TEXT, notes TEXT,
  analysis_json TEXT, chat_message_id TEXT, created_by TEXT, created_at TEXT,
  updated_at TEXT);
CREATE INDEX IF NOT EXISTS refs_proj ON design_references(project_id, created_at);
CREATE TABLE IF NOT EXISTS custom_components (
  id TEXT PRIMARY KEY, project_id TEXT, name TEXT, status TEXT,
  classification TEXT, definition_json TEXT, source_json TEXT,
  fidelity_json TEXT, job_json TEXT, issues_json TEXT, warnings_json TEXT,
  token_refs_json TEXT, token_suggestions_json TEXT,
  basic_equivalent_json TEXT, preview_file TEXT, reference_file TEXT,
  notes TEXT, author_ldap TEXT, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS cc_proj ON custom_components(project_id, status);
CREATE TABLE IF NOT EXISTS imagery_styles (
  id TEXT PRIMARY KEY, project_id TEXT, slug TEXT, name TEXT,
  medium TEXT, target_slot TEXT, incorporation TEXT, style_text TEXT,
  aspect_ratio TEXT, scrim TEXT, reference_images_json TEXT,
  preview_prompt TEXT, preview_url TEXT, enabled INTEGER DEFAULT 1,
  status TEXT DEFAULT 'active',
  author_ldap TEXT, created_at TEXT, updated_at TEXT,
  UNIQUE(project_id, slug));
CREATE INDEX IF NOT EXISTS img_style_proj ON imagery_styles(project_id, updated_at);
CREATE TABLE IF NOT EXISTS import_extractions (
  id TEXT PRIMARY KEY, import_id TEXT, project_id TEXT, kind TEXT,
  status TEXT, title TEXT, description TEXT, region_json TEXT,
  crop_file TEXT, confidence REAL, rationale TEXT, uses_json TEXT,
  question_json TEXT, answer_json TEXT, payload_json TEXT,
  merged_into_json TEXT, order_idx INTEGER, created_at TEXT,
  updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_import ON import_extractions(import_id, order_idx);
CREATE TABLE IF NOT EXISTS templates (
  id TEXT PRIMARY KEY, project_id TEXT, name TEXT, description TEXT,
  when_to_use TEXT, structure_md TEXT, components_json TEXT,
  image_file TEXT, source_json TEXT, status TEXT, author_ldap TEXT,
  created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS tpl_proj ON templates(project_id, status);
CREATE TABLE IF NOT EXISTS teach_sessions (
  id TEXT PRIMARY KEY, project_id TEXT, name TEXT, status TEXT,
  brief TEXT, groups_json TEXT, cards_json TEXT,
  baseline_run_id TEXT, after_run_id TEXT, effect_id TEXT,
  base_revision_id TEXT, revision_id TEXT, summary_json TEXT,
  last_distilled_at TEXT, created_by TEXT, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS teach_proj ON teach_sessions(project_id, created_at);
CREATE TABLE IF NOT EXISTS observations (
  id TEXT PRIMARY KEY, session_id TEXT, project_id TEXT, output_id TEXT,
  sample_id TEXT, polarity TEXT, text TEXT, anchors_json TEXT,
  crop_file TEXT, about_teaching_id TEXT, status TEXT,
  teaching_ids_json TEXT, source TEXT, transcript TEXT, order_idx INTEGER,
  created_by TEXT, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS obs_session ON observations(session_id, output_id);
CREATE TABLE IF NOT EXISTS teachings (
  id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT, kind TEXT,
  title TEXT, body_md TEXT, section TEXT, polarity TEXT,
  applies_when TEXT, data_signature TEXT, version INTEGER,
  status TEXT, origin TEXT, confidence REAL,
  source_observation_ids_json TEXT, template_id TEXT, supersedes_id TEXT,
  applications_json TEXT, applications_status TEXT,
  theme_changes_json TEXT, escalation_guideline_id TEXT, extra_json TEXT,
  order_idx INTEGER,
  created_by TEXT, created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS teachings_proj ON teachings(project_id, status);
CREATE TABLE IF NOT EXISTS teaching_versions (
  id TEXT PRIMARY KEY, teaching_id TEXT, number INTEGER, title TEXT,
  body_md TEXT, section TEXT, applies_when TEXT, reason TEXT, source TEXT,
  revision_id TEXT, created_by TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS tv_teaching ON teaching_versions(teaching_id, number);
CREATE TABLE IF NOT EXISTS teaching_trials (
  id TEXT PRIMARY KEY, teaching_id TEXT, version INTEGER, project_id TEXT,
  output_id TEXT, run_id TEXT, run_kind TEXT, sample_id TEXT,
  revision_id TEXT, expected TEXT, followed TEXT, source TEXT,
  evidence_json TEXT, created_at TEXT,
  UNIQUE(teaching_id, output_id, source));
CREATE INDEX IF NOT EXISTS trials_teaching ON teaching_trials(teaching_id, created_at);
CREATE INDEX IF NOT EXISTS trials_output ON teaching_trials(output_id);
CREATE TABLE IF NOT EXISTS rule_bindings (
  revision_id TEXT, rule_id TEXT, teaching_id TEXT, version INTEGER,
  text TEXT, section TEXT, how TEXT,
  PRIMARY KEY (revision_id, rule_id));
CREATE TABLE IF NOT EXISTS bug_reports (
  id TEXT PRIMARY KEY, issue_id TEXT, issue_url TEXT, component_id INTEGER,
  title TEXT, priority TEXT, type TEXT, description_md TEXT,
  reporter_ldap TEXT, cc TEXT, project_id TEXT, run_id TEXT, output_id TEXT,
  status TEXT, error TEXT, prefilled_url TEXT,
  context_json TEXT, attachments_json TEXT, created_at TEXT);
CREATE INDEX IF NOT EXISTS bugs_created ON bug_reports(created_at DESC);
"""


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
  if row is None:
    return None
  out: dict[str, Any] = {}
  for k in row.keys():
    v = row[k]
    if k.endswith("_json"):
      out[k[:-5]] = util.loads(v, None)
    else:
      out[k] = v
  return out


def _encode(fields: dict[str, Any], json_cols: Iterable[str]) -> dict[str, Any]:
  out = {}
  jc = set(json_cols)
  for k, v in fields.items():
    if k in jc:
      out[k + "_json"] = util.dumps(v)
    else:
      out[k] = v
  return out


JSON_COLS = {
    "users": ["figma_sources"],
    "projects": ["settings"],
    "constitution_revisions": ["ds_snapshot"],
    "token_revisions": ["theme", "diff"],
    "chat_messages": ["context", "proposal", "out_of_scope"],
    "samples": ["context", "payload", "edge_cases", "tags", "meta"],
    "sample_sets": ["sample_ids"],
    "runs": ["sample_ids", "viewports", "renderers", "config"],
    "outputs": ["a2ui", "validation", "render_meta", "renders", "judge"],
    "guidelines": ["tags", "screenshots"],
    "bundles": ["project_ids", "guideline_ids", "counts"],
    "jobs": ["progress", "result"],
    "effects": ["options", "analysis"],
    "image_cache": ["results"],
    "connected_apps": ["headers", "server_info", "tools", "resources"],
    "design_references": [
        "source",
        "images",
        "facts",
        "analysis",
        "questions",
        "merge_plan",
        "merge_log",
        "progress",
    ],
    "import_extractions": [
        "region",
        "uses",
        "question",
        "answer",
        "payload",
        "merged_into",
    ],
    "templates": ["components", "source"],
    "custom_components": [
        "definition",
        "source",
        "fidelity",
        "job",
        "issues",
        "warnings",
        "token_refs",
        "token_suggestions",
        "basic_equivalent",
    ],
    "imagery_styles": ["reference_images"],
    "teach_sessions": ["groups", "cards", "summary"],
    "observations": ["anchors", "teaching_ids"],
    "teachings": [
        "source_observation_ids",
        "applications",
        "theme_changes",
        "extra",
    ],
    "teaching_trials": ["evidence"],
    "bug_reports": ["context", "attachments"],
}


class Store:
  """Thread-safe SQLite store."""

  def __init__(self, data_dir: str):
    self.data_dir = data_dir
    os.makedirs(data_dir, exist_ok=True)
    for sub in (
        "screens",
        "thumbs",
        "guidelines",
        "bundles",
        "references",
        "imagery",
        "gen",
        "components",
        "templates",
        "teach",
        "bugs",
    ):
      os.makedirs(os.path.join(data_dir, sub), exist_ok=True)
    self.path = os.path.join(data_dir, "app.db")
    self._local = threading.local()
    self._write_lock = threading.RLock()
    with self._write_lock:
      self.conn().executescript(SCHEMA)
      self._ensure_columns()
      self.conn().commit()

  def _ensure_columns(self) -> None:
    migrations = [
        ("runs", "renderers_json", "TEXT"),
        ("outputs", "renders_json", "TEXT"),
        ("guidelines", "renderer", "TEXT DEFAULT 'web'"),
        ("runs", "kind", "TEXT"),
        ("runs", "effect_id", "TEXT"),
        ("samples", "meta_json", "TEXT"),
        ("outputs", "stage", "TEXT DEFAULT 'result'"),
        ("users", "figma_token", "TEXT"),
        ("users", "figma_sources_json", "TEXT"),
        ("users", "stitch_api_key", "TEXT"),
        ("imagery_styles", "status", "TEXT DEFAULT 'active'"),
        ("design_references", "phase", "TEXT"),
        ("design_references", "origin", "TEXT"),
        ("design_references", "questions_json", "TEXT"),
        ("design_references", "merge_plan_json", "TEXT"),
        ("design_references", "merge_log_json", "TEXT"),
        ("design_references", "progress_json", "TEXT"),
        ("constitution_revisions", "ds_snapshot_json", "TEXT"),
        # Teaching sessions: columns added after the tables first shipped.
        ("teachings", "extra_json", "TEXT"),
        ("teachings", "escalation_guideline_id", "TEXT"),
        ("teachings", "supersedes_id", "TEXT"),
        ("observations", "source", "TEXT"),
        ("observations", "transcript", "TEXT"),
        ("observations", "about_teaching_id", "TEXT"),
        ("teach_sessions", "effect_id", "TEXT"),
        ("teach_sessions", "summary_json", "TEXT"),
    ]
    c = self.conn()
    for table, col, col_type in migrations:
      cols = {
          r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()
      }
      if col not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")

  def conn(self) -> sqlite3.Connection:
    c = getattr(self._local, "conn", None)
    if c is None:
      c = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
      c.row_factory = sqlite3.Row
      c.execute("PRAGMA journal_mode=WAL")
      c.execute("PRAGMA synchronous=NORMAL")
      self._local.conn = c
    return c

  def file_path(self, *parts: str) -> str:
    return os.path.join(self.data_dir, *parts)

  # ------------------------------------------------------------ generic
  def insert(self, table: str, fields: dict[str, Any]) -> dict[str, Any]:
    enc = _encode(fields, JSON_COLS.get(table, []))
    cols = ",".join(enc)
    qs = ",".join("?" for _ in enc)
    with self._write_lock:
      self.conn().execute(
          f"INSERT INTO {table} ({cols}) VALUES ({qs})", list(enc.values())
      )
      self.conn().commit()
    return self.get(table, fields.get("id") or fields.get("ldap"))

  def update(
      self, table: str, key: str, fields: dict[str, Any], key_col: str = "id"
  ) -> dict[str, Any] | None:
    if fields:
      enc = _encode(fields, JSON_COLS.get(table, []))
      sets = ",".join(f"{k}=?" for k in enc)
      with self._write_lock:
        self.conn().execute(
            f"UPDATE {table} SET {sets} WHERE {key_col}=?", [*enc.values(), key]
        )
        self.conn().commit()
    return self.get(table, key, key_col)

  def delete(self, table: str, key: str, key_col: str = "id") -> None:
    with self._write_lock:
      self.conn().execute(f"DELETE FROM {table} WHERE {key_col}=?", [key])
      self.conn().commit()

  def execute(self, sql: str, args: Iterable[Any] = ()) -> None:
    with self._write_lock:
      self.conn().execute(sql, list(args))
      self.conn().commit()

  def get(
      self, table: str, key: Any, key_col: str | None = None
  ) -> dict[str, Any] | None:
    if key is None:
      return None
    kc = key_col or ("ldap" if table == "users" else "id")
    row = (
        self.conn()
        .execute(f"SELECT * FROM {table} WHERE {kc}=?", [key])
        .fetchone()
    )
    return _decode(row)

  def query(self, sql: str, args: Iterable[Any] = ()) -> list[dict[str, Any]]:
    return [_decode(r) for r in self.conn().execute(sql, list(args)).fetchall()]

  def scalar(self, sql: str, args: Iterable[Any] = ()) -> Any:
    row = self.conn().execute(sql, list(args)).fetchone()
    return row[0] if row else None
