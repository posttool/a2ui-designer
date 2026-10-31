"""Structural layout diff between two A2UI v0.9 outputs for the same sample.

Used by the Constitution effect view: control (no constitution) vs treatment
(current constitution). Works on the *instance* tree (templates expanded over
the data model, keyed `id|scope` exactly like the web renderer's
`data-a2ui-id` / `data-a2ui-scope`), so every change can be outlined on the
web screenshots via the boxes captured at render time.

Pipeline:
  1. build_tree(): resolve the data model and expand templates.
  2. Anchors: content-bearing leaves (Text, Image, Icon, media, inputs and
     Buttons, which anchor on their label). Each anchor gets a content key
     derived from the *resolved* value, so a literal "Ibuprofen" and a
     binding to "/meds/0/name" that resolves to "Ibuprofen" match.
  3. Match anchors A↔B (exact key, then fuzzy text).
  4. Compare matched anchors (component swap, emphasis), containers
     (container swap, new / dissolved groupings), order (LIS) and unmatched
     content (added / removed).
"""

from __future__ import annotations

import collections
import difflib
import re
from typing import Any

CHILD_KEYS = ("children", "child", "trigger", "content")
CONTAINERS = {"Row", "Column", "List", "Card", "Tabs", "Modal"}
GROUPING = {"List", "Card", "Tabs", "Modal", "Row", "Column"}
MEDIA = {"Image", "Video", "AudioPlayer"}
INPUTS = {"TextField", "CheckBox", "ChoicePicker", "Slider", "DateTimeInput"}
MAX_NODES = 4000

KIND_LABELS = {
    "container_swap": "Container changed",
    "regrouped": "New grouping",
    "ungrouped": "Grouping removed",
    "component_swap": "Component changed",
    "emphasis": "Hierarchy changed",
    "reordered": "Reordered",
    "added": "Content added",
    "removed": "Content dropped",
}


# ------------------------------------------------------------------ pointers
def _parse_pointer(p: str) -> list[str]:
  if not p or p == "/":
    return []
  return [
      s.replace("~1", "/").replace("~0", "~") for s in p.lstrip("/").split("/")
  ]


def _join_pointer(segs: list[str]) -> str:
  if not segs:
    return ""
  return "/" + "/".join(s.replace("~", "~0").replace("/", "~1") for s in segs)


def resolve_path(scope: str, path: Any) -> str:
  """Mirror of web/renderer/model.js resolvePath."""
  if path is None:
    return scope or ""
  path = str(path).strip()
  if path.startswith("/"):
    return "" if path == "/" else path
  segs = _parse_pointer(scope or "")
  for part in path.split("/"):
    if part in ("", "."):
      continue
    if part == "..":
      if segs:
        segs.pop()
      continue
    segs.append(part.replace("~1", "/").replace("~0", "~"))
  return _join_pointer(segs)


def _get(data: Any, pointer: str) -> Any:
  cur = data
  for seg in _parse_pointer(pointer):
    if isinstance(cur, dict):
      cur = cur.get(seg)
    elif isinstance(cur, list):
      try:
        cur = cur[int(seg)]
      except (ValueError, IndexError):
        return None
    else:
      return None
  return cur


def _set(data: dict[str, Any], pointer: str, value: Any) -> dict[str, Any]:
  segs = _parse_pointer(pointer)
  if not segs:
    return value if isinstance(value, dict) else {"": value}
  cur: Any = data
  for seg in segs[:-1]:
    nxt = cur.get(seg) if isinstance(cur, dict) else None
    if not isinstance(nxt, (dict, list)):
      nxt = {}
      if isinstance(cur, dict):
        cur[seg] = nxt
    cur = nxt
  if isinstance(cur, dict):
    cur[segs[-1]] = value
  elif isinstance(cur, list):
    try:
      cur[int(segs[-1])] = value
    except (ValueError, IndexError):
      pass
  return data


# ---------------------------------------------------------------- the tree
class Node:
  __slots__ = (
      "key",
      "id",
      "scope",
      "type",
      "comp",
      "children",
      "parent",
      "depth",
      "content",
      "anchor",
      "order",
  )

  def __init__(self, cid: str, scope: str, comp: dict[str, Any], depth: int):
    self.id = cid
    self.scope = scope
    self.key = f"{cid}|{scope}"
    self.comp = comp
    self.type = str(comp.get("component") or "?")
    self.children: list[Node] = []
    self.parent: Node | None = None
    self.depth = depth
    self.content: str | None = None
    self.anchor = False
    self.order = 0

  def variant(self) -> str:
    v = self.comp.get("variant")
    return v if isinstance(v, str) else ""


class Tree:

  def __init__(self, messages: Any):
    self.components: dict[str, dict[str, Any]] = {}
    self.data: dict[str, Any] = {}
    for msg in messages if isinstance(messages, list) else []:
      if not isinstance(msg, dict):
        continue
      uc = msg.get("updateComponents")
      if isinstance(uc, dict):
        for c in uc.get("components") or []:
          if isinstance(c, dict) and isinstance(c.get("id"), str):
            self.components[c["id"]] = c
      ud = msg.get("updateDataModel")
      if isinstance(ud, dict):
        p = ud.get("path") or "/"
        if p in ("/", ""):
          v = ud.get("value")
          if isinstance(v, dict):
            self.data = v
        else:
          self.data = _set(self.data, p, ud.get("value"))
    self.nodes: list[Node] = []
    self.by_key: dict[str, Node] = {}
    self.root: Node | None = None
    if "root" in self.components:
      self.root = self._build("root", "", 0, set())
    for i, n in enumerate(self.nodes):
      n.order = i
    for n in self.nodes:
      n.content = self._content(n)
      n.anchor = n.content is not None
    # Anchors inside a Button / Modal trigger belong to that control.
    for n in self.nodes:
      if n.anchor and n.type not in ("Button",):
        p = n.parent
        while p is not None:
          if p.type == "Button":
            n.anchor = False
            break
          p = p.parent

  # ---------------------------------------------------------- expansion
  def _child_refs(
      self, comp: dict[str, Any], scope: str
  ) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for k in CHILD_KEYS:
      v = comp.get(k)
      if isinstance(v, str):
        out.append((v, scope))
      elif isinstance(v, list):
        out.extend((x, scope) for x in v if isinstance(x, str))
      elif isinstance(v, dict) and isinstance(v.get("componentId"), str):
        base = resolve_path(scope, v.get("path") or "")
        lst = _get(self.data, base)
        if isinstance(lst, list):
          out.extend((v["componentId"], f"{base}/{i}") for i in range(len(lst)))
        elif isinstance(lst, dict):
          for key in lst:
            esc = str(key).replace("~", "~0").replace("/", "~1")
            out.append((v["componentId"], f"{base}/{esc}"))
    tabs = comp.get("tabs")
    if isinstance(tabs, list):
      for t in tabs:
        if isinstance(t, dict) and isinstance(t.get("child"), str):
          out.append((t["child"], scope))
    return out

  def _build(
      self, cid: str, scope: str, depth: int, stack: set[str]
  ) -> Node | None:
    comp = self.components.get(cid)
    if comp is None or cid in stack or len(self.nodes) >= MAX_NODES:
      return None
    node = Node(cid, scope, comp, depth)
    self.nodes.append(node)
    self.by_key.setdefault(node.key, node)
    stack = stack | {cid}
    for ref, sc in self._child_refs(comp, scope):
      ch = self._build(ref, sc, depth + 1, stack)
      if ch is not None:
        ch.parent = node
        node.children.append(ch)
    return node

  # ----------------------------------------------------------- content
  def resolve(self, v: Any, scope: str) -> Any:
    if isinstance(v, dict):
      if "path" in v and isinstance(v["path"], str):
        return _get(self.data, resolve_path(scope, v["path"]))
      if v.get("call") == "formatString":
        tpl = (v.get("args") or {}).get("value")
        if isinstance(tpl, str):
          return re.sub(
              r"\$\{([^}]*)\}",
              lambda m: _display(
                  _get(self.data, resolve_path(scope, m.group(1).strip()))
              ),
              tpl,
          )
      if "call" in v:
        args = v.get("args") or {}
        vals = (
            [self.resolve(a, scope) for a in args.values()]
            if isinstance(args, dict)
            else []
        )
        return " ".join(_display(x) for x in vals if x is not None)
    return v

  def text_of(self, n: Node) -> str:
    """Concatenated display text of a subtree (used for Button labels)."""
    parts = []
    stack = [n]
    while stack:
      x = stack.pop()
      if x.type == "Text":
        parts.append(_display(self.resolve(x.comp.get("text"), x.scope)))
      stack.extend(reversed(x.children))
    return " ".join(p for p in parts if p)

  def _content(self, n: Node) -> str | None:
    c = n.comp
    t = n.type
    if t == "Text":
      s = _norm(_display(self.resolve(c.get("text"), n.scope)))
      return f"t:{s}" if s else None
    if t in MEDIA:
      s = _display(self.resolve(c.get("url"), n.scope)).strip()
      return f"m:{s}" if s else f"m:{t}:{n.id}"
    if t == "Icon":
      name = self.resolve(c.get("name"), n.scope)
      return f"i:{_display(name)}"
    if t == "Button":
      return f"b:{_norm(self.text_of(n))}"
    if t in INPUTS:
      lbl = self.resolve(c.get("label"), n.scope)
      return f"f:{t}:{_norm(_display(lbl))}"
    return None

  # ---------------------------------------------------------- summaries
  def anchors(self) -> list[Node]:
    return [n for n in self.nodes if n.anchor]

  def stats(self) -> dict[str, Any]:
    counts = collections.Counter(n.type for n in self.nodes)
    root_children = len(self.root.children) if self.root else 0
    return {
        "components": len(self.nodes),
        "anchors": sum(1 for n in self.nodes if n.anchor),
        "depth": max((n.depth for n in self.nodes), default=0),
        "top_level_sections": root_children,
        "by_type": dict(counts.most_common()),
        "skeleton": self.skeleton(2),
    }

  def skeleton(self, max_depth: int = 2) -> str:
    def rec(n: Node, d: int) -> str:
      if d >= max_depth or not n.children:
        return n.type
      kids = [rec(c, d + 1) for c in n.children]
      out = []
      for k, grp in _runs(kids):
        out.append(k if grp == 1 else f"{k}×{grp}")
      return f"{n.type}[{', '.join(out)}]"

    return rec(self.root, 0) if self.root else "(empty)"

  def outline(self, limit: int = 140) -> str:
    """Indented outline for LLM prompts; repeated template rows collapse."""
    lines: list[str] = []

    def label(n: Node) -> str:
      extra = ""
      if n.type == "Text":
        txt = _display(self.resolve(n.comp.get("text"), n.scope))
        v = n.variant()
        extra = f"({v}) " if v else " "
        extra += f'"{_clip(txt, 60)}"'
      elif n.type == "Button":
        extra = f' "{_clip(self.text_of(n), 40)}"'
      elif n.type == "Icon":
        extra = f" {_display(self.resolve(n.comp.get('name'), n.scope))}"
      elif n.type in ("Row", "Column"):
        bits = [
            f"{k}={n.comp[k]}"
            for k in ("justify", "align")
            if isinstance(n.comp.get(k), str)
        ]
        extra = f" ({', '.join(bits)})" if bits else ""
      return f"{n.type} #{n.id}{extra}"

    def rec(n: Node, d: int) -> None:
      if len(lines) >= limit:
        return
      lines.append("  " * d + label(n))
      seen: dict[str, int] = {}
      for c in n.children:
        seen[c.id] = seen.get(c.id, 0) + 1
        if seen[c.id] == 2:
          total = sum(1 for x in n.children if x.id == c.id)
          lines.append(
              "  " * (d + 1) + f"… ×{total - 1} more {c.type} #{c.id} rows"
          )
        if seen[c.id] >= 2:
          continue
        rec(c, d + 1)

    if self.root:
      rec(self.root, 0)
    if len(lines) >= limit:
      lines.append("… (truncated)")
    return "\n".join(lines)


def _runs(items: list[str]):
  prev, n = None, 0
  for it in items:
    if it == prev:
      n += 1
      continue
    if prev is not None:
      yield prev, n
    prev, n = it, 1
  if prev is not None:
    yield prev, n


def _display(v: Any) -> str:
  if v is None:
    return ""
  if isinstance(v, bool):
    return "true" if v else "false"
  if isinstance(v, (int, float, str)):
    return str(v)
  if isinstance(v, list):
    return ", ".join(_display(x) for x in v[:8])
  return ""


_MD = re.compile(r"[*_`#>\[\]()~|]+")
_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
  s = _MD.sub(" ", s or "")
  return _WS.sub(" ", s).strip().lower()[:120]


def _clip(s: str, n: int) -> str:
  s = _WS.sub(" ", s or "").strip()
  return s if len(s) <= n else s[: n - 1] + "…"


# ----------------------------------------------------------------- matching
def match_anchors(a: Tree, b: Tree) -> dict[str, str]:
  """Returns {a_key: b_key} for content-equivalent anchors."""
  aa, bb = a.anchors(), b.anchors()
  pool: dict[str, collections.deque] = collections.defaultdict(
      collections.deque
  )
  for n in bb:
    pool[n.content].append(n)
  out: dict[str, str] = {}
  used: set[str] = set()
  for n in aa:
    q = pool.get(n.content)
    if q:
      m = q.popleft()
      out[n.key] = m.key
      used.add(m.key)
  # Fuzzy pass for rewritten text / labels.
  ra = [
      n
      for n in aa
      if n.key not in out and n.content and n.content[:2] in ("t:", "b:")
  ]
  rb = [
      n
      for n in bb
      if n.key not in used and n.content and n.content[:2] in ("t:", "b:")
  ]
  cands = []
  for x in ra:
    for y in rb:
      sx, sy = x.content[2:], y.content[2:]
      if not sx or not sy:
        continue
      short, long_ = (sx, sy) if len(sx) <= len(sy) else (sy, sx)
      if len(short) >= 4 and short in long_ and len(short) >= 0.3 * len(long_):
        cands.append((0.75 + 0.2 * len(short) / len(long_), x.key, y.key))
        continue
      if abs(len(sx) - len(sy)) > max(len(sx), len(sy)) * 0.6:
        continue
      r = difflib.SequenceMatcher(None, sx, sy).ratio()
      if r >= 0.72:
        cands.append((r, x.key, y.key))
  cands.sort(reverse=True)
  taken_a, taken_b = set(), set()
  for _, ka, kb in cands:
    if ka in taken_a or kb in taken_b:
      continue
    out[ka] = kb
    taken_a.add(ka)
    taken_b.add(kb)
  return out


def _anchor_sets(t: Tree) -> dict[str, frozenset[str]]:
  """Container key -> set of anchor keys below it."""
  res: dict[str, set[str]] = {}

  def rec(n: Node) -> set[str]:
    s: set[str] = {n.key} if n.anchor else set()
    for c in n.children:
      s |= rec(c)
    if n.type in CONTAINERS or n.children:
      res[n.key] = s
    return s

  if t.root:
    rec(t.root)
  return {k: frozenset(v) for k, v in res.items()}


def _lis_keep(seq: list[int]) -> set[int]:
  """Indices (into seq) of one longest increasing subsequence."""
  if not seq:
    return set()
  import bisect

  tails: list[int] = []
  tails_idx: list[int] = []
  prev = [-1] * len(seq)
  for i, v in enumerate(seq):
    j = bisect.bisect_left(tails, v)
    if j == len(tails):
      tails.append(v)
      tails_idx.append(i)
    else:
      tails[j] = v
      tails_idx[j] = i
    prev[i] = tails_idx[j - 1] if j else -1
  keep = set()
  k = tails_idx[-1]
  while k >= 0:
    keep.add(k)
    k = prev[k]
  return keep


def _content_label(t: Tree, n: Node) -> str:
  if n.type == "Text":
    return _clip(_display(t.resolve(n.comp.get("text"), n.scope)), 48)
  if n.type == "Button":
    return f"[{_clip(t.text_of(n), 32)}]"
  if n.type == "Icon":
    return f"icon {_display(t.resolve(n.comp.get('name'), n.scope))}"
  return n.type


# --------------------------------------------------------------------- diff
def diff(a_messages: Any, b_messages: Any) -> dict[str, Any]:
  """Structural diff from A (control) to B (treatment)."""
  a, b = Tree(a_messages), Tree(b_messages)
  out: dict[str, Any] = {
      "a": a.stats() if a.root else None,
      "b": b.stats() if b.root else None,
      "changes": [],
      "magnitude": 0,
      "changed": False,
  }
  if not a.root or not b.root:
    out["changed"] = bool(a.root) != bool(b.root)
    out["magnitude"] = 100 if out["changed"] else 0
    return out

  m = match_anchors(a, b)
  rev = {v: k for k, v in m.items()}
  changes: list[dict[str, Any]] = []

  def add(
      kind: str,
      label: str,
      a_keys=(),
      b_keys=(),
      anchors=(),
      detail=None,
      a_focus=None,
      b_focus=None,
  ):
    changes.append({
        "kind": kind,
        "kind_label": KIND_LABELS[kind],
        "label": label,
        "a_keys": sorted(set(a_keys)),
        "b_keys": sorted(set(b_keys)),
        # What to outline on screenshots (containers for structural changes).
        "a_focus": sorted(set(a_keys if a_focus is None else a_focus)),
        "b_focus": sorted(set(b_keys if b_focus is None else b_focus)),
        "anchor_contents": sorted({x for x in anchors if x})[:40],
        "anchor_contents_all": {x for x in anchors if x},
        "detail": detail or {},
    })

  # 1. Matched anchors: component swaps + emphasis.
  emph_a, emph_b, emph_desc, emph_anchor = [], [], collections.Counter(), []
  for ka, kb in m.items():
    na, nb = a.by_key[ka], b.by_key[kb]
    if na.type != nb.type:
      add(
          "component_swap",
          f"{na.type} → {nb.type}: {_content_label(b, nb)}",
          [ka],
          [kb],
          [nb.content],
          {"from": na.type, "to": nb.type},
      )
    elif na.type == "Text" and na.variant() != nb.variant():
      emph_a.append(ka)
      emph_b.append(kb)
      emph_anchor.append(nb.content)
      emph_desc[
          f"{na.variant() or 'default'} → {nb.variant() or 'default'}"
      ] += 1
  if emph_a:
    top = ", ".join(
        f"{k} ×{v}" if v > 1 else k for k, v in emph_desc.most_common(3)
    )
    add(
        "emphasis",
        f"{len(emph_a)} text{'s' if len(emph_a) != 1 else ''} changed weight"
        f" ({top})",
        emph_a,
        emph_b,
        emph_anchor,
        {"variants": dict(emph_desc)},
    )

  # 2. Containers.
  sa, sb = _anchor_sets(a), _anchor_sets(b)
  # Express B's anchor sets in A-key space.
  sb_in_a = {k: frozenset(rev[x] for x in v if x in rev) for k, v in sb.items()}

  def best_match(
      target: frozenset[str], cands: dict[str, frozenset[str]], tree: Tree
  ):
    best, best_j = None, 0.0
    for k, s in cands.items():
      if not s:
        continue
      inter = len(target & s)
      if not inter:
        continue
      j = inter / len(target | s)
      if j > best_j or (
          j == best_j
          and best is not None
          and tree.by_key[k].depth > tree.by_key[best].depth
      ):
        best, best_j = k, j
    return best, best_j

  sa_in_b = {k: frozenset(m[x] for x in v if x in m) for k, v in sa.items()}
  n_anchors_a = max(1, len(a.anchors()))
  n_anchors_b = max(1, len(b.anchors()))

  def mostly_matched(own: frozenset[str], mapped: frozenset[str]) -> bool:
    return len(own) >= 2 and len(mapped) >= max(2, int(len(own) * 0.5))

  def has_same_type(
      n: Node,
      mapped: frozenset[str],
      other: dict[str, frozenset[str]],
      tree: Tree,
  ) -> bool:
    for k, s in other.items():
      if tree.by_key[k].type != n.type or not s:
        continue
      inter = len(mapped & s)
      if inter and inter / len(mapped | s) >= 0.6:
        return True
    return False

  # 2a. Container swaps: one per A container. A container is "the same
  # group" as every B container whose anchor set overlaps ≥ 60 %; if none of
  # those has A's type, the closest one is a swap.
  by_a: dict[str, list[tuple[float, int, str]]] = collections.defaultdict(list)
  for kb, s_a_space in sb_in_a.items():
    nb = b.by_key[kb]
    if nb.type not in CONTAINERS or not mostly_matched(sb[kb], s_a_space):
      continue
    best = None
    for ka, s in sa.items():
      na_ = a.by_key[ka]
      if na_.type not in CONTAINERS or not s:
        continue
      inter = len(s_a_space & s)
      if not inter:
        continue
      j = inter / len(s_a_space | s)
      if j < 0.6:
        continue
      rank = (j, na_.type == nb.type, na_.depth)
      if best is None or rank > best[0]:
        best = (rank, ka)
    if best is not None:
      by_a[best[1]].append((best[0][0], nb.depth, kb))
  swapped_b: set[str] = set()
  swapped_a: set[str] = set()
  for ka, cands in by_a.items():
    na = a.by_key[ka]
    # Wrapper chains (Card > Column) share one anchor set: only the
    # innermost A container of a chain is compared.
    if any(
        c.type in CONTAINERS and sa.get(c.key) == sa[ka] for c in na.children
    ):
      continue
    if any(b.by_key[kb].type == na.type for _, _, kb in cands):
      continue
    _, _, kb = max(cands)
    nb = b.by_key[kb]
    if any(
        c.type in CONTAINERS and sb.get(c.key) == sb[kb] for c in nb.children
    ):
      # Pick the innermost B container of its chain too.
      inner = nb
      while True:
        nxt = [
            c
            for c in inner.children
            if c.type in CONTAINERS and sb.get(c.key) == sb[kb]
        ]
        if not nxt:
          break
        inner = nxt[0]
      nb = inner
      kb = nb.key
      if nb.type == na.type:
        continue
    if kb in swapped_b:
      continue
    swapped_a.add(ka)
    swapped_b.add(kb)
    cnt = len(sb[kb])
    add(
        "container_swap",
        f"{na.type} → {nb.type} around {cnt} item{'s' if cnt != 1 else ''}"
        f" ({_preview(b, sb[kb])})",
        [ka],
        [kb],
        [b.by_key[x].content for x in sb[kb]],
        {"from": na.type, "to": nb.type, "depth": nb.depth, "size": cnt},
    )

  # 2b. New groupings in B. Strong groupings (Card/List/Tabs/Modal) count
  # when A has no same-type group over the same items; Row/Column only when
  # A has no group at all over them. Page-level wrappers are ignored.
  def grouping_changes(
      src: Tree,
      own_sets,
      mapped_sets,
      other_sets,
      other_tree,
      n_total: int,
      swapped: set[str],
  ):
    found: list[tuple[Node, frozenset[str]]] = []
    for k, mapped in mapped_sets.items():
      n = src.by_key[k]
      own = own_sets[k]
      if (
          n.type not in GROUPING
          or k in swapped
          or not mostly_matched(own, mapped)
      ):
        continue
      if len(own) >= 0.8 * n_total and n.depth <= 1:
        continue
      if n.type in ("Row", "Column"):
        _, j = best_match(mapped, other_sets, other_tree)
        if j >= 0.6:
          continue
      elif has_same_type(n, mapped, other_sets, other_tree):
        continue
      found.append((n, own))
    found.sort(key=lambda t: t[0].depth)
    kept: list[tuple[Node, frozenset[str]]] = []
    for n, s in found:
      if any(s <= r and n.type == rn.type for rn, r in kept):
        continue
      if any(s == r for _, r in kept):
        continue
      kept.append((n, s))
    return kept

  for nb, s in grouping_changes(b, sb, sb_in_a, sa, a, n_anchors_b, swapped_b):
    a_keys = {rev[x] for x in s if x in rev}
    add(
        "regrouped",
        f"New {nb.type} around {len(s)} items ({_preview(b, s)})",
        a_keys,
        [nb.key] + sorted(s),
        [b.by_key[x].content for x in s],
        {"type": nb.type, "depth": nb.depth, "size": len(s)},
        b_focus=[nb.key],
    )
  for na, s in grouping_changes(a, sa, sa_in_b, sb, b, n_anchors_a, swapped_a):
    add(
        "ungrouped",
        f"{na.type} around {len(s)} items removed ({_preview(a, s)})",
        [na.key] + sorted(s),
        {m[x] for x in s if x in m},
        [a.by_key[x].content for x in s],
        {"type": na.type, "depth": na.depth, "size": len(s)},
        a_focus=[na.key],
    )

  # 3. Order of matched anchors.
  b_order = sorted((b.by_key[kb].order, ka, kb) for ka, kb in m.items())
  a_rank = {ka: a.by_key[ka].order for ka in m}
  seq = [a_rank[ka] for _, ka, _ in b_order]
  keep = _lis_keep(seq)
  moved = [(ka, kb) for i, (_, ka, kb) in enumerate(b_order) if i not in keep]
  if moved and len(m) >= 3:
    names = ", ".join(_content_label(b, b.by_key[kb]) for _, kb in moved[:3])
    add(
        "reordered",
        f"{len(moved)} item{'s' if len(moved) != 1 else ''} moved ({names}"
        f"{'…' if len(moved) > 3 else ''})",
        [x for x, _ in moved],
        [y for _, y in moved],
        [b.by_key[y].content for _, y in moved],
        {"moved": len(moved), "matched": len(m)},
    )

  # 4. Unmatched content.
  added_b = [n for n in b.anchors() if n.key not in rev]
  removed_a = [n for n in a.anchors() if n.key not in m]
  if added_b:
    add(
        "added",
        f"{len(added_b)} new: "
        + ", ".join(_content_label(b, n) for n in added_b[:4])
        + ("…" if len(added_b) > 4 else ""),
        (),
        [n.key for n in added_b],
        [n.content for n in added_b],
        {"count": len(added_b)},
    )
  if removed_a:
    add(
        "removed",
        f"{len(removed_a)} dropped: "
        + ", ".join(_content_label(a, n) for n in removed_a[:4])
        + ("…" if len(removed_a) > 4 else ""),
        [n.key for n in removed_a],
        (),
        [n.content for n in removed_a],
        {"count": len(removed_a)},
    )

  changes = _collapse_repeats(changes)
  # Magnitude: share of all content (union of both sides' anchors) touched by
  # any change; container-level changes count every anchor inside them.
  # Emphasis-only changes count half.
  all_contents = {n.content for n in a.anchors()} | {
      n.content for n in b.anchors()
  }
  touched_full: set[str] = set()
  touched_half: set[str] = set()
  for c in changes:
    target = touched_half if c["kind"] == "emphasis" else touched_full
    target.update(c["anchor_contents_all"])
  touched_half -= touched_full
  total = max(1, len(all_contents))
  mag = min(
      100, round(100 * (len(touched_full) + 0.5 * len(touched_half)) / total)
  )
  if a.skeleton(2) != b.skeleton(2):
    mag = max(mag, 10)
  for i, c in enumerate(changes):
    c["index"] = i
    del c["anchor_contents_all"]
  out.update({
      "changes": changes,
      "magnitude": mag,
      "changed": bool(changes) and mag >= 5,
      "matched": len(m),
  })
  return out


def _collapse_repeats(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
  """Merges the same change repeated across template rows into one (×N)."""

  def sig(c: dict[str, Any]):
    d = c.get("detail") or {}
    if c["kind"] == "component_swap":
      return (c["kind"], d.get("from"), d.get("to"))
    # Same structural move at the same depth over same-sized groups: the
    # repeated rows of a template or a hand-unrolled list.
    return (
        c["kind"],
        d.get("from"),
        d.get("to"),
        d.get("type"),
        d.get("depth"),
        d.get("size"),
    )

  merged: dict[Any, dict[str, Any]] = {}
  order: list[Any] = []
  for c in changes:
    if c["kind"] not in (
        "container_swap",
        "regrouped",
        "ungrouped",
        "component_swap",
    ):
      s = (id(c),)
    else:
      s = sig(c)
    if s not in merged:
      merged[s] = dict(c, repeat=1)
      order.append(s)
      continue
    m = merged[s]
    m["repeat"] += 1
    m["a_keys"] = sorted(set(m["a_keys"]) | set(c["a_keys"]))
    m["b_keys"] = sorted(set(m["b_keys"]) | set(c["b_keys"]))
    m["a_focus"] = sorted(set(m["a_focus"]) | set(c["a_focus"]))
    m["b_focus"] = sorted(set(m["b_focus"]) | set(c["b_focus"]))
    m["anchor_contents"] = sorted(
        set(m["anchor_contents"]) | set(c["anchor_contents"])
    )[:60]
    m["anchor_contents_all"] = (
        m["anchor_contents_all"] | c["anchor_contents_all"]
    )
  out = []
  for s in order:
    c = merged[s]
    if c["repeat"] > 1:
      c["label"] = f"{c['label']} — ×{c['repeat']} similar"
    out.append(c)
  return out


def _preview(t: Tree, keys: frozenset[str] | set[str], n: int = 2) -> str:
  nodes = sorted(
      (t.by_key[k] for k in keys if k in t.by_key), key=lambda x: x.order
  )
  labels = [_content_label(t, x) for x in nodes[:n]]
  more = len(nodes) - n
  return ", ".join(labels) + (f", +{more}" if more > 0 else "")


def same_change(c1: dict[str, Any], c2: dict[str, Any]) -> bool:
  """True if two changes (from different output pairs) are the same effect."""
  if c1["kind"] != c2["kind"]:
    return False
  d1, d2 = c1.get("detail") or {}, c2.get("detail") or {}
  if c1["kind"] in ("container_swap", "component_swap"):
    if (d1.get("from"), d1.get("to")) != (d2.get("from"), d2.get("to")):
      return False
  s1, s2 = set(c1["anchor_contents"]), set(c2["anchor_contents"])
  if not s1 and not s2:
    return True
  return len(s1 & s2) / max(1, len(s1 | s2)) >= 0.5


def parse_rules(md: str) -> list[dict[str, str]]:
  """Splits a constitution into addressable rules R1..Rn (bullets/paragraphs).

  Purely structural (markdown list items and paragraphs under headings);
  semantics are left to the attribution model.
  """
  rules: list[dict[str, str]] = []
  section = ""
  buf: list[str] = []

  def flush():
    text = " ".join(x.strip() for x in buf).strip()
    buf.clear()
    if text:
      rules.append(
          {"id": f"R{len(rules) + 1}", "section": section, "text": text}
      )

  for raw in (md or "").splitlines():
    line = raw.rstrip()
    st = line.strip()
    if not st:
      flush()
      continue
    if st.startswith("#"):
      flush()
      section = st.lstrip("#").strip()
      continue
    if re.match(r"^([-*+]|\d+[.)])\s+", st):
      if raw[: len(raw) - len(raw.lstrip())].__len__() >= 2 and buf:
        # Nested bullet: keep it with its parent rule.
        buf.append(re.sub(r"^([-*+]|\d+[.)])\s+", "— ", st))
        continue
      flush()
      buf.append(re.sub(r"^([-*+]|\d+[.)])\s+", "", st))
      continue
    buf.append(st)
  flush()
  return rules
