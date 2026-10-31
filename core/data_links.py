"""Links a sample's input data to the A2UI components that present it.

Used by Teaching sessions: the data pane shows, for every leaf of the sample
payload, whether (and where) it ended up on the card, and observations can be
anchored to data paths and data↔component relations.

The generator usually copies (and often renames/restructures) the payload into
the surface data model and binds components to it, or writes values as literal
text. So a payload leaf is linked to a component when either

  * bound:   the component reads a data-model pointer (`{"@path"}` or a
             `${/x}` inside `formatString`) whose resolved value equals the
             leaf's value — ties broken by key name and array index; or
  * literal: the leaf's value appears in the component's resolved text
             (strings ≥ 3 chars, word-bounded when short; numbers in common
             formats such as 19.5 / 19.50 / $19.50 / 1,234).

Ambiguity is reported, never hidden: a leaf may list several components.
Payload paths are JSON pointers relative to the payload (`/draft_cart/0/price`);
sample context leaves use `context.<key>`.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from core import layout_diff
from core.layout_diff import Node, Tree

MAX_LEAVES = 1500
_IMG_RE = re.compile(r"^(/img/|/gen/|/api/gen/|https?://|data:image/)")


# ------------------------------------------------------------------ leaves
def _esc(seg: Any) -> str:
  return str(seg).replace("~", "~0").replace("/", "~1")


def _kind(v: Any) -> str:
  if v is None:
    return "null"
  if isinstance(v, bool):
    return "bool"
  if isinstance(v, (int, float)):
    return "number"
  if isinstance(v, str):
    if _IMG_RE.match(v.strip()):
      return "image" if not v.startswith("http") or _looks_image(v) else "url"
    return "text"
  if isinstance(v, list):
    return "array"
  if isinstance(v, dict):
    return "object"
  return "text"


def _looks_image(v: str) -> bool:
  low = v.lower().split("?")[0]
  return low.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg")) or (
      "portraits" in low or "upload.wikimedia" in low or "images." in low
  )


def _preview(v: Any) -> str:
  if isinstance(v, list):
    return f"[{len(v)}]"
  if isinstance(v, dict):
    return "{…}"
  s = layout_diff._display(v)  # pylint: disable=protected-access
  if isinstance(v, str):
    s = v
  s = re.sub(r"\s+", " ", s).strip()
  return s if len(s) <= 80 else s[:79] + "…"


def _walk(
    v: Any, path: str, key: str, depth: int, out: list[dict[str, Any]]
) -> None:
  if len(out) >= MAX_LEAVES:
    return
  k = _kind(v)
  item = {
      "path": path,
      "key": key,
      "depth": depth,
      "kind": k,
      "value": v if k not in ("object", "array") else None,
      "value_preview": _preview(v),
  }
  if k == "array":
    item["count"] = len(v)
  out.append(item)
  if k == "array":
    for i, x in enumerate(v):
      _walk(x, f"{path}/{i}", str(i), depth + 1, out)
  elif k == "object":
    for kk, x in v.items():
      _walk(x, f"{path}/{_esc(kk)}", str(kk), depth + 1, out)


def leaves_of(sample: dict[str, Any]) -> list[dict[str, Any]]:
  """Document-order nodes of the payload (+ context.*), containers included."""
  out: list[dict[str, Any]] = []
  payload = sample.get("payload")
  if isinstance(payload, dict):
    for kk, x in payload.items():
      _walk(x, f"/{_esc(kk)}", str(kk), 0, out)
  elif isinstance(payload, list):
    _walk(payload, "", "payload", 0, out)
  ctx = sample.get("context") or {}
  if isinstance(ctx, dict):
    for kk, x in ctx.items():
      if isinstance(x, (str, int, float, bool)) and str(x).strip():
        out.append({
            "path": f"context.{kk}",
            "key": str(kk),
            "depth": 0,
            "kind": _kind(x),
            "value": x,
            "value_preview": _preview(x),
        })
  return out


# --------------------------------------------------------------- matching
def _norm_text(s: str) -> str:
  return layout_diff._norm(s)  # pylint: disable=protected-access


def _num_forms(v: float) -> list[str]:
  forms = set()
  if isinstance(v, bool):
    return []
  if float(v).is_integer():
    iv = int(v)
    forms.update({str(iv), f"{iv:,}"})
    if abs(iv) < 10:
      return []  # too ambiguous as a substring; exact-text match only
  else:
    forms.update({
        repr(float(v)).rstrip("0").rstrip("."),
        f"{v:.2f}",
        f"{v:,.2f}",
        f"{v:.1f}",
    })
  return sorted(forms, key=len, reverse=True)


def _appears(leaf: dict[str, Any], text: str, norm: str) -> bool:
  v = leaf.get("value")
  k = leaf["kind"]
  if k in ("null", "bool", "object", "array"):
    return False
  if k == "number":
    if not text:
      return False
    if float(v).is_integer() and abs(int(v)) < 10:
      if norm == str(int(v)):
        return True
      # A small count is only linked when the field's own name sits next to
      # it ("4 attendees" for `attendees: 4`) — structural, not semantic.
      words = [
          x
          for x in re.split(r"[_\W]+", str(leaf.get("key") or "").lower())
          if len(x) >= 3
      ]
      for wd in words:
        stem = wd[:-1] if wd.endswith("s") and len(wd) > 3 else wd
        if re.search(
            rf"(?<![\d.]){int(v)}\s+{re.escape(stem)}", norm
        ) or re.search(
            rf"{re.escape(stem)}\w*\s*[:·-]?\s*{int(v)}(?![\d.])", norm
        ):
          return True
      return False
    for f in _num_forms(v):
      if re.search(rf"(?<![\d.,]){re.escape(f)}(?![\d])", text):
        return True
    return False
  if k in ("image", "url"):
    return False  # handled through media urls
  s = _norm_text(str(v))
  if len(s) < 3 or not norm:
    return False
  if len(s) <= 6:
    return re.search(rf"(?<!\w){re.escape(s)}(?!\w)", norm) is not None
  return s in norm


def _same(a: Any, b: Any) -> bool:
  if isinstance(a, bool) or isinstance(b, bool):
    return a is b
  if isinstance(a, (int, float)) and isinstance(b, (int, float)):
    return abs(float(a) - float(b)) < 1e-9
  if isinstance(a, str) and isinstance(b, str):
    return a.strip() == b.strip() and a.strip() != ""
  return False


_IDX = re.compile(r"/(\d+)(?=/|$)")


def _match_value(
    leaves: list[dict[str, Any]], dm_path: str, value: Any
) -> list[str]:
  if isinstance(value, (list, dict)):
    if isinstance(value, list):
      cands = [
          l
          for l in leaves
          if l["kind"] == "array" and l.get("count") == len(value)
      ]
      last = dm_path.rstrip("/").split("/")[-1]
      keyed = [l for l in cands if l["key"] == last]
      if keyed:
        cands = keyed
      return [l["path"] for l in cands[:1]] if len(cands) == 1 else []
    return []
  if value is None or (isinstance(value, str) and not value.strip()):
    return []
  cands = [l for l in leaves if _same(l.get("value"), value)]
  if len(cands) <= 1:
    return [l["path"] for l in cands]
  last = dm_path.rstrip("/").split("/")[-1]
  keyed = [l for l in cands if l["key"] == last]
  if keyed:
    cands = keyed
  if len(cands) > 1:
    idx = _IDX.findall(dm_path)
    if idx:
      tail = idx[-1]
      byidx = [
          l for l in cands if (_IDX.findall(l["path"]) or [None])[-1] == tail
      ]
      if byidx:
        cands = byidx
  return [l["path"] for l in cands]


def _pointers_read(comp: dict[str, Any], scope: str) -> list[str]:
  out: list[str] = []

  def rec(v: Any, key: str = "") -> None:
    if isinstance(v, dict):
      bound = v.get("@path", v.get("path"))
      if isinstance(bound, str) and "componentId" not in v:
        out.append(layout_diff.resolve_path(scope, bound))
      if v.get("@call", v.get("call")) == "formatString":
        tpl = (v.get("args") or {}).get("value")
        if isinstance(tpl, str):
          for m in re.finditer(r"\$\{([^}]*)\}", tpl):
            out.append(layout_diff.resolve_path(scope, m.group(1).strip()))
      for kk, x in v.items():
        if kk in ("path", "@path"):
          continue
        rec(x, kk)
    elif isinstance(v, list):
      for x in v:
        rec(x, key)

  for k, v in comp.items():
    if (
        k in ("id", "component", "catalogId")
        or k in layout_diff.CHILD_KEYS
        and isinstance(v, (str, list))
    ):
      continue
    if k in layout_diff.CHILD_KEYS and isinstance(v, dict):
      # Template children: the list itself is bound to the array.
      if isinstance(v.get("path"), str):
        out.append(layout_diff.resolve_path(scope, v["path"]))
      continue
    rec(v, k)
  return list(dict.fromkeys(out))


def _node_text(tree: Tree, n: Node) -> str:
  c = n.comp
  if n.type == "Text":
    return layout_diff._display(tree.resolve(c.get("text"), n.scope))  # pylint: disable=protected-access
  if n.type == "Button":
    return tree.text_of(n)
  if n.type in layout_diff.INPUTS:
    bits = [
        layout_diff._display(tree.resolve(c.get(k), n.scope))  # pylint: disable=protected-access
        for k in ("label", "value", "placeholder")
    ]
    return " ".join(b for b in bits if b)
  if n.type == "Icon":
    return layout_diff._display(tree.resolve(c.get("name"), n.scope))  # pylint: disable=protected-access
  return ""


def _label(tree: Tree, n: Node) -> str:
  txt = ""
  if n.type in ("Text", "Button") or n.type in layout_diff.INPUTS:
    txt = layout_diff._clip(_node_text(tree, n), 40)  # pylint: disable=protected-access
  elif n.type == "List":
    txt = f"×{len(n.children)}"
  elif n.type == "Icon":
    txt = _node_text(tree, n)
  return (
      f'{n.type} "{txt}"'
      if txt and n.type != "List"
      else (f"{n.type} {txt}".strip())
  )


# ------------------------------------------------------------------- main
def compute_links(
    messages: Any,
    sample: dict[str, Any],
    boxes: list[dict[str, Any]] | None = None,
    height: int | None = None,
) -> dict[str, Any]:
  """See module docstring. `sample` is a formatted sample (payload, context)."""
  tree = Tree(messages or [])
  leaves = leaves_of(sample)
  scalar_leaves = [l for l in leaves if l["kind"] not in ("object",)]
  box_by_key = {
      b.get("k"): {
          "x": b.get("x"),
          "y": b.get("y"),
          "w": b.get("w"),
          "h": b.get("h"),
      }
      for b in boxes or []
      if isinstance(b, dict) and b.get("k")
  }
  by_leaf: dict[str, dict[str, set[str]]] = {}
  comps: list[dict[str, Any]] = []
  for n in tree.nodes:
    reads: list[str] = []
    for ptr in _pointers_read(n.comp, n.scope):
      val = layout_diff._get(tree.data, ptr)  # pylint: disable=protected-access
      reads.extend(_match_value(scalar_leaves, ptr, val))
    reads = list(dict.fromkeys(reads))
    literal: list[str] = []
    text = _node_text(tree, n)
    norm = _norm_text(text)
    if text:
      for l in scalar_leaves:
        if l["path"] not in reads and _appears(l, text, norm):
          literal.append(l["path"])
    if n.type in layout_diff.MEDIA:
      url = layout_diff._display(tree.resolve(n.comp.get("url"), n.scope)).strip()  # pylint: disable=protected-access
      if url:
        for l in scalar_leaves:
          if l["kind"] in ("image", "url") and str(l["value"]).strip() == url:
            if l["path"] not in reads:
              reads.append(l["path"])
    comps.append({
        "key": n.key,
        "id": n.id,
        "scope": n.scope,
        "type": n.type,
        "label": _label(tree, n),
        "reads": reads,
        "literal": literal,
        "box": box_by_key.get(n.key),
        "depth": n.depth,
    })
    for p in reads:
      by_leaf.setdefault(p, {}).setdefault("bound", set()).add(n.key)
    for p in literal:
      by_leaf.setdefault(p, {}).setdefault("literal", set()).add(n.key)

  out_leaves = []
  represented = total = 0
  for l in leaves:
    hit = by_leaf.get(l["path"], {})
    comps_for = sorted(hit.get("bound", set()) | hit.get("literal", set()))
    rep = (
        "bound"
        if hit.get("bound")
        else ("literal" if hit.get("literal") else "none")
    )
    if l["kind"] == "object":
      rep = "container"
    elif l["kind"] == "array" and rep == "none":
      # An array counts as represented when any of its items is.
      prefix = l["path"] + "/"
      if any(p.startswith(prefix) for p in by_leaf):
        rep = "partial"
    item = {k: v for k, v in l.items() if k != "value"}
    item.update({"represented": rep, "components": comps_for})
    out_leaves.append(item)
    if l["kind"] not in ("object", "array", "null") and not (
        l["kind"] == "text" and not str(l.get("value") or "").strip()
    ):
      total += 1
      if rep in ("bound", "literal"):
        represented += 1
  unrep = [
      l["path"]
      for l in out_leaves
      if l["represented"] == "none"
      and l["kind"] not in ("object", "array", "null", "image")
  ]
  return {
      "leaves": out_leaves,
      "components": comps,
      "unrepresented": unrep,
      "coverage": {"represented": represented, "total": total},
      "outline": tree.outline(),
      "skeleton": tree.skeleton(3),
      "height": height,
  }


def subtree_keys(messages: Any, key: str) -> list[str]:
  """Instance keys of the subtree rooted at `key` (id|scope)."""
  tree = Tree(messages or [])
  root = tree.by_key.get(key)
  if root is None:
    return []
  out, stack = [], [root]
  while stack:
    n = stack.pop()
    out.append(n.key)
    stack.extend(reversed(n.children))
  return out


def subtree_outline(messages: Any, key: str, limit: int = 60) -> dict[str, Any]:
  """Outline + skeleton of one instance subtree (for template capture)."""
  tree = Tree(messages or [])
  root = tree.by_key.get(key) if key else tree.root
  if root is None:
    return {"outline": "", "skeleton": "", "components": []}
  lines: list[str] = []
  types: list[str] = []

  def rec(n: Node, d: int) -> None:
    if len(lines) >= limit:
      return
    lab = _label(tree, n)
    lines.append("  " * d + f"{lab} #{n.id}")
    types.append(n.type)
    seen: dict[str, int] = {}
    for c in n.children:
      seen[c.id] = seen.get(c.id, 0) + 1
      if seen[c.id] == 2:
        total = sum(1 for x in n.children if x.id == c.id)
        lines.append("  " * (d + 1) + f"… ×{total - 1} more {c.type} #{c.id}")
      if seen[c.id] >= 2:
        continue
      rec(c, d + 1)

  rec(root, 0)
  return {
      "outline": "\n".join(lines),
      "skeleton": _skel(root),
      "components": list(dict.fromkeys(types)),
  }


def _skel(n: Node, d: int = 0) -> str:
  if d >= 3 or not n.children:
    return n.type
  kids = [_skel(c, d + 1) for c in n.children]
  parts = []
  for k, grp in layout_diff._runs(kids):  # pylint: disable=protected-access
    parts.append(k if grp == 1 else f"{k}×{grp}")
  return f"{n.type}[{', '.join(parts)}]"


def skeleton_match(messages: Any, skeleton: str) -> str:
  """'exact' | 'similar' | 'none': is a subtree with this skeleton present?

  Repeat counts are ignored (a 3-row and a 4-row list match).
  """
  if not skeleton:
    return "none"
  tree = Tree(messages or [])
  target = re.sub(r"×\d+", "×n", skeleton)
  best = 0.0
  for n in tree.nodes:
    if not n.children:
      continue
    s = re.sub(r"×\d+", "×n", _skel(n))
    if s == target:
      return "exact"
    best = max(best, difflib.SequenceMatcher(None, s, target).ratio())
  return "similar" if best >= 0.8 else "none"
