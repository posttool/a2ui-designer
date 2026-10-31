"""Custom component web implementations: template/CSS sanitizer + checks.

A custom component's web implementation is a declarative HTML template plus
scoped CSS (see docs/CONTRACT.md "Design system"). No JavaScript: templates
are restricted to an allowlist of tags/attributes and a tiny binding syntax,
and the renderer mounts them in a shadow root. The renderer re-sanitizes
client-side; this module is the authoritative server-side check.
"""

from __future__ import annotations

import html
import html.parser
import re
from typing import Any

ALLOWED_TAGS = frozenset(
    "div span p h1 h2 h3 h4 h5 h6 ul ol li img figure figcaption section"
    " article header footer strong em small hr br slot"
    " svg g path circle rect line polyline polygon ellipse defs"
    " lineargradient radialgradient stop clippath".split()
)
SVG_ATTRS = frozenset(
    "viewbox xmlns d fill stroke stroke-width stroke-linecap stroke-linejoin"
    " opacity fill-opacity stroke-opacity cx cy r rx ry x y x1 y1 x2 y2"
    " width height points transform offset stop-color stop-opacity"
    " gradientunits gradienttransform id clip-path".split()
)
VOID_TAGS = frozenset({"img", "hr", "br"})
INLINE_PHRASE_TAGS = frozenset({"strong", "em", "small"})
# Elements whose whole subtree is dropped.
DROP_SUBTREE = frozenset({
    "script",
    "style",
    "template",
    "iframe",
    "object",
    "embed",
    "foreignobject",
    "math",
    "noscript",
    "textarea",
    "select",
    "link",
    "meta",
    "base",
    "title",
    "head",
})
DIRECTIVES = (
    "data-if",
    "data-unless",
    "data-each",
    "data-action",
    "data-icon",
    "data-bg",
)
INTERP_ATTRS = ("src", "alt", "title", "class", "d", "fill", "stroke")
EXPR_RE = re.compile(r"^[A-Za-z_]\w*(?:\.\w+)*$")
BIND_RE = re.compile(r"\{\{\s*([A-Za-z_][\w]*(?:\.[\w]+)*)\s*\}\}")
EACH_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s+as\s+([A-Za-z_]\w*)\s*$")
COND_RE = re.compile(r"^\s*([A-Za-z_]\w*(?:\.\w+)*)\s*(?::\s*([\w .-]*?))?\s*$")
STYLE_DECL_RE = re.compile(r"^\s*(--[A-Za-z0-9_-]+)\s*:\s*(.+?)\s*$")
VAR_RE = re.compile(r"var\(\s*(--a2ui-[A-Za-z0-9_-]+)")
NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9]{1,59}$")

SLOT_TYPES = ("ComponentId", "ChildList")
MAX_TEMPLATE = 20000
MAX_CSS = 30000


def _attr_ok(name: str) -> bool:
  return (
      name in INTERP_ATTRS
      or name in DIRECTIVES
      or name in SVG_ATTRS
      or name in ("style", "name", "role")
      or name.startswith("aria-")
      or (name.startswith("data-") and re.match(r"^data-[a-z0-9-]+$", name))
  )


class _Sanitizer(html.parser.HTMLParser):

  def __init__(self):
    super().__init__(convert_charrefs=True)
    self.out: list[str] = []
    self.errors: list[str] = []
    self.bindings: list[str] = []
    self.slots: list[str] = []
    self.actions: list[str] = []
    self.loop_vars: list[set[str]] = []
    self._drop_depth = 0
    self._stack: list[str] = []
    self._each_stack: list[bool] = []

  # ---- helpers
  def _ref(self, expr: str) -> None:
    root = expr.split(".", 1)[0]
    if any(root in s for s in self.loop_vars):
      return
    if root not in self.bindings:
      self.bindings.append(root)

  def _interp(self, value: str) -> None:
    for m in BIND_RE.finditer(value):
      self._ref(m.group(1))

  def _err(self, msg: str) -> None:
    if msg not in self.errors and len(self.errors) < 40:
      self.errors.append(msg)

  # ---- parser callbacks
  def handle_starttag(self, tag, attrs):
    self._open(tag, attrs, selfclosing=False)

  def handle_startendtag(self, tag, attrs):
    self._open(tag, attrs, selfclosing=True)

  def _open(self, tag, attrs, selfclosing):
    tag = tag.lower()
    if self._drop_depth:
      if tag not in VOID_TAGS and not selfclosing:
        self._drop_depth += 1
      return
    if tag in DROP_SUBTREE:
      self._err(f"<{tag}> is not allowed (removed)")
      if (
          tag not in VOID_TAGS
          and not selfclosing
          and tag not in ("link", "meta", "base")
      ):
        self._drop_depth = 1
      return
    if tag not in ALLOWED_TAGS:
      # Unknown/disallowed element: keep its children, drop the element.
      self._err(f"<{tag}> is not allowed (unwrapped)")
      if tag not in VOID_TAGS and not selfclosing:
        self._stack.append("#unwrapped")
        self._each_stack.append(False)
      return
    kept: list[tuple[str, str]] = []
    pushed_loop = False
    # data-each first so the loop variable is in scope for sibling attrs.
    attrs = sorted(attrs, key=lambda a: (a[0] or "").lower() != "data-each")
    for name, value in attrs:
      name = (name or "").lower()
      value = value if value is not None else ""
      if name.startswith("on"):
        self._err(f"event handler attribute '{name}' removed")
        continue
      if not _attr_ok(name):
        self._err(f"attribute '{name}' on <{tag}> removed")
        continue
      low = value.strip().lower()
      if low.startswith("javascript:") or low.startswith("vbscript:"):
        self._err(f"script URL in '{name}' removed")
        continue
      if name == "src":
        if not (
            BIND_RE.fullmatch(value.strip())
            or low.startswith("data:image/")
            or low.startswith("https://")
            or low.startswith("/img/")
            or low.startswith("/gen/")
            or low.startswith("/api/")
        ):
          self._err("img src must be a {{prop}} binding, https: or data:image")
          continue
      if name == "style":
        decls = []
        for part in value.split(";"):
          if not part.strip():
            continue
          m = STYLE_DECL_RE.match(part)
          if not m or "url(" in m.group(2).lower():
            self._err(
                "style attribute may only set custom properties (--x: …);"
                " put other styles in the CSS"
            )
            continue
          decls.append(f"{m.group(1)}: {m.group(2)}")
          self._interp(m.group(2))
        if not decls:
          continue
        value = "; ".join(decls)
      elif name == "data-each":
        m = EACH_RE.match(value)
        if not m:
          self._err(f"data-each must be 'prop as item' (got '{value}')")
          continue
        self._ref(m.group(1))
        self.loop_vars.append({m.group(2)})
        pushed_loop = True
      elif name in ("data-if", "data-unless"):
        m = COND_RE.match(value)
        if not m:
          self._err(f"{name} must be 'prop' or 'prop:value' (got '{value}')")
          continue
        self._ref(m.group(1))
      elif name == "data-action":
        prop = value.strip()
        if not EXPR_RE.match(prop):
          self._err(f"data-action must name an Action prop (got '{value}')")
          continue
        root = prop.split(".", 1)[0]
        in_loop = any(root in s for s in self.loop_vars)
        self._ref(prop)
        if not in_loop and "." not in prop and prop not in self.actions:
          self.actions.append(prop)
      elif name == "name" and tag == "slot":
        prop = value.strip()
        if not re.match(r"^[A-Za-z_]\w*$", prop):
          self._err(f"<slot name> must be a prop name (got '{value}')")
          continue
        self._ref(prop)
        if prop not in self.slots:
          self.slots.append(prop)
      elif name == "name":
        self._err(f"attribute 'name' on <{tag}> removed")
        continue
      else:
        self._interp(value)
      kept.append((name, value))
    if tag == "slot" and not any(n == "name" for n, _ in kept):
      self._err('<slot> needs name="<prop>"')
    attr_txt = "".join(f' {n}="{html.escape(v, quote=True)}"' for n, v in kept)
    self.out.append(f"<{tag}{attr_txt}>")
    if tag in VOID_TAGS or selfclosing and tag != "slot":
      if pushed_loop:
        self.loop_vars.pop()
      if selfclosing and tag not in VOID_TAGS:
        self.out.append(f"</{tag}>")
      return
    if selfclosing:  # <slot ... />
      self.out.append(f"</{tag}>")
      if pushed_loop:
        self.loop_vars.pop()
      return
    self._stack.append(tag)
    self._each_stack.append(pushed_loop)

  def handle_endtag(self, tag):
    tag = tag.lower()
    if self._drop_depth:
      if tag not in VOID_TAGS:
        self._drop_depth -= 1
      return
    if tag in VOID_TAGS:
      return
    # Pop to the matching open element (tolerant of sloppy markup).
    for i in range(len(self._stack) - 1, -1, -1):
      t = self._stack[i]
      if t == tag or (t == "#unwrapped" and tag not in ALLOWED_TAGS):
        while len(self._stack) > i:
          top = self._stack.pop()
          loop = self._each_stack.pop()
          if top != "#unwrapped":
            self.out.append(f"</{top}>")
          if loop:
            self.loop_vars.pop()
        return

  def handle_data(self, data):
    if self._drop_depth:
      return
    self._interp(data)
    self.out.append(html.escape(data, quote=False))

  def handle_comment(self, data):
    return

  def close_all(self) -> str:
    self.close()
    while self._stack:
      top = self._stack.pop()
      loop = self._each_stack.pop()
      if top != "#unwrapped":
        self.out.append(f"</{top}>")
      if loop:
        self.loop_vars.pop()
    return "".join(self.out).strip()


class _HtmlFormatter(html.parser.HTMLParser):
  """Lightweight HTML tree builder for pretty-printing component templates."""

  def __init__(self):
    super().__init__(convert_charrefs=True)
    self.root: dict[str, Any] = {"type": "root", "children": []}
    self._stack: list[dict[str, Any]] = [self.root]

  def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
    self._open(tag, attrs, selfclosing=False)

  def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]):
    self._open(tag, attrs, selfclosing=True)

  def _open(
      self, tag: str, attrs: list[tuple[str, str | None]], selfclosing: bool
  ) -> None:
    tag = tag.lower()
    attr_txt = "".join(
        f' {(k or "").lower()}="{html.escape(v if v is not None else "", quote=True)}"'
        for k, v in attrs
        if k
    )
    is_void = tag in VOID_TAGS
    node: dict[str, Any] = {
        "type": "el",
        "tag": tag,
        "attr_txt": attr_txt,
        "void": is_void,
        "children": [],
    }
    self._stack[-1]["children"].append(node)
    if not is_void and not selfclosing:
      self._stack.append(node)

  def handle_endtag(self, tag: str):
    tag = tag.lower()
    if tag in VOID_TAGS:
      return
    for i in range(len(self._stack) - 1, 0, -1):
      if self._stack[i]["tag"] == tag:
        del self._stack[i:]
        return

  def handle_data(self, data: str):
    if not data:
      return
    escaped = html.escape(data, quote=False)
    children = self._stack[-1]["children"]
    if children and children[-1]["type"] == "text":
      children[-1]["text"] += escaped
    else:
      children.append({"type": "text", "text": escaped})


def _can_inline_children(children: list[dict[str, Any]]) -> tuple[bool, str]:
  """Returns (True, inline_html) when children are empty or short inline text."""
  if not children:
    return True, ""
  parts: list[str] = []
  for c in children:
    if c["type"] == "text":
      parts.append(re.sub(r"\s+", " ", c["text"]))
    elif (
        c["type"] == "el"
        and c["tag"] in INLINE_PHRASE_TAGS
        and not c["void"]
        and all(gc["type"] == "text" for gc in c["children"])
    ):
      inner = "".join(
          re.sub(r"\s+", " ", gc["text"]) for gc in c["children"]
      ).strip()
      parts.append(f'<{c["tag"]}{c["attr_txt"]}>{inner}</{c["tag"]}>')
    else:
      return False, ""
  joined = "".join(parts).strip()
  if len(joined) <= 90 and "\n" not in joined:
    return True, joined
  return False, ""


def _render_formatted_el(node: dict[str, Any], depth: int) -> str:
  pad = "  " * depth
  tag = node["tag"]
  attr_txt = node["attr_txt"]
  if node["void"]:
    return f"{pad}<{tag}{attr_txt}>"
  ok_inline, inline_str = _can_inline_children(node["children"])
  if ok_inline:
    return f"{pad}<{tag}{attr_txt}>{inline_str}</{tag}>"
  lines: list[str] = []
  for c in node["children"]:
    if c["type"] == "text":
      t = re.sub(r"\s+", " ", c["text"]).strip()
      if t:
        lines.append(f"{pad}  {t}")
    else:
      lines.append(_render_formatted_el(c, depth + 1))
  if not lines:
    return f"{pad}<{tag}{attr_txt}></{tag}>"
  return f"{pad}<{tag}{attr_txt}>\n" + "\n".join(lines) + f"\n{pad}</{tag}>"


def format_template(template: str) -> str:
  """Pretty-prints an HTML template across multiple lines with 2-space indent."""
  raw = str(template or "").strip()
  if not raw:
    return ""
  fmt = _HtmlFormatter()
  try:
    fmt.feed(raw)
    fmt.close()
  except Exception:  # pylint: disable=broad-except
    return raw
  lines: list[str] = []
  for c in fmt.root["children"]:
    if c["type"] == "text":
      t = re.sub(r"\s+", " ", c["text"]).strip()
      if t:
        lines.append(t)
    else:
      lines.append(_render_formatted_el(c, 0))
  return "\n".join(lines).strip()


def _split_css_top(text: str, sep: str) -> list[str]:
  """Splits CSS text on `sep` outside strings, comments, and parentheses."""
  parts: list[str] = []
  cur: list[str] = []
  i = 0
  n = len(text)
  paren = 0
  quote = ""
  while i < n:
    ch = text[i]
    if quote:
      cur.append(ch)
      if ch == "\\" and i + 1 < n:
        i += 1
        cur.append(text[i])
      elif ch == quote:
        quote = ""
      i += 1
      continue
    if ch == "/" and i + 1 < n and text[i + 1] == "*":
      end = text.find("*/", i + 2)
      if end < 0:
        cur.append(text[i:])
        break
      cur.append(text[i : end + 2])
      i = end + 2
      continue
    if ch in ("'", '"'):
      quote = ch
      cur.append(ch)
      i += 1
      continue
    if ch == "(":
      paren += 1
    elif ch == ")" and paren > 0:
      paren -= 1
    if paren == 0 and ch == sep:
      parts.append("".join(cur))
      cur = []
      i += 1
      continue
    cur.append(ch)
    i += 1
  if cur:
    parts.append("".join(cur))
  return parts


def format_css(css: str, indent: str = "") -> str:
  """Pretty-prints scoped CSS rules across multiple lines with 2-space indent."""
  raw = str(css or "").strip()
  if not raw:
    return ""
  out_blocks: list[str] = []
  i = 0
  n = len(raw)
  while i < n:
    while i < n and raw[i].isspace():
      i += 1
    if i >= n:
      break
    if raw.startswith("/*", i):
      end = raw.find("*/", i + 2)
      if end < 0:
        out_blocks.append(f"{indent}{raw[i:].strip()}")
        break
      out_blocks.append(f"{indent}{raw[i:end + 2].strip()}")
      i = end + 2
      continue
    # Find next '{' outside strings/comments/parens.
    sel_start = i
    paren = 0
    quote = ""
    brace_pos = -1
    while i < n:
      ch = raw[i]
      if quote:
        if ch == "\\" and i + 1 < n:
          i += 2
          continue
        if ch == quote:
          quote = ""
        i += 1
        continue
      if ch == "/" and i + 1 < n and raw[i + 1] == "*":
        end = raw.find("*/", i + 2)
        i = (end + 2) if end >= 0 else n
        continue
      if ch in ("'", '"'):
        quote = ch
        i += 1
        continue
      if ch == "(":
        paren += 1
      elif ch == ")" and paren > 0:
        paren -= 1
      elif ch == "{" and paren == 0:
        brace_pos = i
        break
      i += 1
    if brace_pos < 0:
      rem = raw[sel_start:].strip()
      if rem:
        out_blocks.append(f"{indent}{rem}")
      break
    selector = re.sub(r"\s+", " ", raw[sel_start:brace_pos]).strip()
    # Find matching '}'
    i = brace_pos + 1
    body_start = i
    depth = 1
    while i < n and depth > 0:
      ch = raw[i]
      if quote:
        if ch == "\\" and i + 1 < n:
          i += 2
          continue
        if ch == quote:
          quote = ""
        i += 1
        continue
      if ch == "/" and i + 1 < n and raw[i + 1] == "*":
        end = raw.find("*/", i + 2)
        i = (end + 2) if end >= 0 else n
        continue
      if ch in ("'", '"'):
        quote = ch
        i += 1
        continue
      if ch == "{":
        depth += 1
      elif ch == "}":
        depth -= 1
        if depth == 0:
          break
      i += 1
    body = raw[body_start:i].strip()
    if i < n and raw[i] == "}":
      i += 1
    if not body:
      out_blocks.append(f"{indent}{selector} {{}}")
      continue
    if "{" in body and selector.startswith("@"):
      nested = format_css(body, indent=indent + "  ")
      out_blocks.append(f"{indent}{selector} {{\n{nested}\n{indent}}}")
      continue
    decls = []
    for part in _split_css_top(body, ";"):
      p = part.strip()
      if not p:
        continue
      if ":" in p and not p.startswith("/*"):
        k, v = p.split(":", 1)
        decls.append(
            f"{indent}  {k.strip()}: {re.sub(r'\\s+', ' ', v).strip()};"
        )
      else:
        decls.append(f"{indent}  {p};")
    if decls:
      out_blocks.append(
          f"{indent}{selector} {{\n" + "\n".join(decls) + f"\n{indent}}}"
      )
    else:
      out_blocks.append(f"{indent}{selector} {{}}")
  return "\n\n".join(out_blocks) if not indent else "\n".join(out_blocks)


def sanitize_template(template: str) -> dict[str, Any]:
  """→ {html, errors, bindings, slots, actions} (errors = what was removed)."""
  template = str(template or "")
  errors: list[str] = []
  if len(template) > MAX_TEMPLATE:
    errors.append(f"template longer than {MAX_TEMPLATE} characters (truncated)")
    template = template[:MAX_TEMPLATE]
  p = _Sanitizer()
  p.feed(template)
  clean = p.close_all()
  return {
      "html": clean,
      "errors": errors + p.errors,
      "bindings": p.bindings,
      "slots": p.slots,
      "actions": p.actions,
  }


def sanitize_css(css: str) -> dict[str, Any]:
  """→ {css, errors, token_refs} — drops @import, external url(), expressions."""
  css = str(css or "")
  errors: list[str] = []
  if len(css) > MAX_CSS:
    errors.append(f"CSS longer than {MAX_CSS} characters (truncated)")
    css = css[:MAX_CSS]
  css = re.sub(r"<\s*/?\s*style[^>]*>", "", css, flags=re.I)
  if re.search(r"@import", css, re.I):
    errors.append("@import is not allowed (removed)")
    css = re.sub(r"@import[^;]*;?", "", css, flags=re.I)

  def _url(m: re.Match) -> str:
    inner = m.group(1).strip().strip("'\"")
    if inner.lower().startswith("data:"):
      return m.group(0)
    errors.append(
        f"url({inner[:60]}) is not allowed; only data: URLs (removed)"
    )
    return "none"

  css = re.sub(r"url\(\s*([^)]*)\)", _url, css, flags=re.I)
  for bad in ("expression(", "behavior:", "-moz-binding", "javascript:"):
    if bad in css.lower():
      errors.append(f"'{bad}' is not allowed (removed)")
      css = re.sub(re.escape(bad), "", css, flags=re.I)
  refs: list[str] = []
  for m in VAR_RE.finditer(css):
    if m.group(1) not in refs:
      refs.append(m.group(1))
  return {"css": css.strip(), "errors": errors, "token_refs": refs}


_LITERAL_COLOR_RE = re.compile(
    r"(?<![\w-])(#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\()"
)


def literal_style_warnings(css: str) -> list[str]:
  """Hard-coded colors outside var() fallbacks (tokens should be used)."""
  stripped = re.sub(r"var\([^()]*(\([^()]*\)[^()]*)*\)", "", css or "")
  hits = _LITERAL_COLOR_RE.findall(stripped)
  if not hits:
    return []
  return [
      f"{len(hits)} hard-coded color value(s) in CSS; use var(--a2ui-*) tokens"
      " (or propose a custom token)"
  ]


def _prop_kind(schema: dict[str, Any]) -> str:
  ref = str(schema.get("$ref") or "")
  if ref:
    return ref.rsplit("/", 1)[-1]
  t = schema.get("type")
  if t == "array":
    return "array"
  if t == "string" and schema.get("enum"):
    return "enum"
  return str(t or "any")


def _collect_composition_bindings(val: Any, out: list[str]) -> None:
  if isinstance(val, str):
    for m in BIND_RE.finditer(val):
      root = m.group(1).split(".", 1)[0]
      if root not in out:
        out.append(root)
  elif isinstance(val, list):
    for item in val:
      _collect_composition_bindings(item, out)
  elif isinstance(val, dict):
    for v in val.values():
      _collect_composition_bindings(v, out)


def check_composition(
    composition: dict[str, Any] | None,
    properties: dict[str, Any],
    basic_names: set[str] | frozenset[str] = frozenset(),
) -> tuple[list[str], list[str], list[str]]:
  """Validates a Basic-catalog composition macro (`{root, components}`).

  Returns `(issues, warnings, bindings)`.
  """
  issues: list[str] = []
  warnings: list[str] = []
  bindings: list[str] = []
  if not isinstance(composition, dict):
    return issues, warnings, bindings
  comps = composition.get("components")
  root = str(composition.get("root") or "").strip()
  if not comps and not root:
    return issues, warnings, bindings
  if not isinstance(comps, list) or not comps:
    issues.append("composition: 'components' must be a non-empty list")
    return issues, warnings, bindings
  if not root:
    issues.append("composition: 'root' component id is required")
  seen_ids: set[str] = set()
  for idx, c in enumerate(comps):
    if not isinstance(c, dict):
      issues.append(f"composition: component[{idx}] must be an object")
      continue
    cid = str(c.get("id") or "").strip()
    cname = str(c.get("component") or "").strip()
    if not cid:
      issues.append(f"composition: component[{idx}] is missing 'id'")
    elif cid in seen_ids:
      issues.append(f"composition: duplicate component id '{cid}'")
    else:
      seen_ids.add(cid)
    if not cname:
      issues.append(
          f"composition: component '{cid or idx}' is missing 'component'"
      )
    elif basic_names and cname not in basic_names:
      issues.append(f"composition: '{cname}' is not a Basic catalog component")
    for k, v in c.items():
      if k not in ("id", "component"):
        _collect_composition_bindings(v, bindings)
  if root and seen_ids and root not in seen_ids:
    issues.append(f"composition: root '{root}' is not in components")
  for b in bindings:
    if b not in properties:
      issues.append(f"composition uses '{b}' which is not a declared prop")
  return issues, warnings, bindings


def check_definition(
    defn: dict[str, Any], basic_names: set[str] | frozenset[str] = frozenset()
) -> dict[str, Any]:
  """Validates a custom component definition.

  Returns {issues:[str], warnings:[str], token_refs:[str], template, css}
  where template/css are the sanitized versions.
  """
  issues: list[str] = []
  warnings: list[str] = []
  name = str(defn.get("name") or "")
  if not NAME_RE.match(name):
    issues.append(
        "name must be PascalCase letters/digits (2-60 chars), e.g. ProductTile"
    )
  if name in basic_names:
    issues.append(f"'{name}' is a Basic catalog component name")
  if not str(defn.get("description") or "").strip():
    issues.append("description is empty")
  props = defn.get("props") or {}
  if not isinstance(props, dict) or not isinstance(
      props.get("properties", {}), dict
  ):
    issues.append("props must be a JSON schema object with 'properties'")
    props = {}
  properties: dict[str, Any] = props.get("properties") or {}
  for pname in properties:
    if pname in ("id", "component", "weight", "accessibility", "children"):
      if pname != "children":
        issues.append(f"prop '{pname}' is reserved")
    if not re.match(r"^[A-Za-z_]\w*$", pname):
      issues.append(f"prop name '{pname}' is not an identifier")
  for r in props.get("required") or []:
    if r not in properties:
      issues.append(f"required prop '{r}' is not declared")
  web = defn.get("web") or {}
  t = sanitize_template(web.get("template") or "")
  c = sanitize_css(web.get("css") or "")
  composition = (
      defn.get("composition")
      if isinstance(defn.get("composition"), dict)
      else {}
  )
  has_comp = bool(composition.get("root") and composition.get("components"))
  comp_issues, comp_warns, comp_bindings = check_composition(
      composition, properties, basic_names
  )
  issues.extend(comp_issues)
  warnings.extend(comp_warns)
  if not t["html"] and not has_comp:
    issues.append("web template is empty")
  if t["html"]:
    issues.extend(f"template: {e}" for e in t["errors"])
    issues.extend(f"css: {e}" for e in c["errors"])
    for b in t["bindings"]:
      if b not in properties:
        issues.append(f"template uses '{b}' which is not a declared prop")
    for s in t["slots"]:
      kind = _prop_kind(properties.get(s) or {})
      if s in properties and kind not in SLOT_TYPES:
        issues.append(
            f'<slot name="{s}"> needs a ComponentId or ChildList prop'
        )
    for a in t["actions"]:
      kind = _prop_kind(properties.get(a) or {})
      if a in properties and kind != "Action":
        issues.append(f'data-action="{a}" needs an Action prop')
    for pname, ps in properties.items():
      if (
          _prop_kind(ps) in SLOT_TYPES
          and pname not in t["slots"]
          and pname not in comp_bindings
      ):
        warnings.append(f"slot prop '{pname}' has no <slot name=\"{pname}\">")
    warnings.extend(literal_style_warnings(c["css"]))
  sample = defn.get("sample") or {}
  sprops = sample.get("props") if isinstance(sample, dict) else None
  if isinstance(sprops, dict):
    for r in props.get("required") or []:
      if r not in sprops:
        issues.append(f"sample props miss required '{r}'")
  return {
      "issues": issues,
      "warnings": warnings,
      "token_refs": c["token_refs"],
      "template": t["html"],
      "css": c["css"],
  }


VALID_STRATEGIES = (
    "auto",
    "web_template",
    "basic_composition",
)


def normalize_definition(raw: dict[str, Any]) -> dict[str, Any]:
  """Coerces a (possibly LLM-produced) definition into the stored shape."""
  raw = raw if isinstance(raw, dict) else {}
  props = raw.get("props")
  if not isinstance(props, dict):
    props = {}
  props = dict(props)
  props.setdefault("type", "object")
  if not isinstance(props.get("properties"), dict):
    props["properties"] = {}
  req = props.get("required")
  props["required"] = (
      [r for r in req if isinstance(r, str)] if isinstance(req, list) else []
  )
  web = raw.get("web") if isinstance(raw.get("web"), dict) else {}
  sample = raw.get("sample") if isinstance(raw.get("sample"), dict) else {}
  out: dict[str, Any] = {
      "name": re.sub(r"[^A-Za-z0-9]", "", str(raw.get("name") or ""))[:60],
      "description": str(raw.get("description") or "").strip()[:600],
      "usage": str(raw.get("usage") or "").strip()[:3000],
      "props": props,
      "web": {
          "template": str(web.get("template") or ""),
          "css": str(web.get("css") or ""),
      },
      "sample": {
          "props": (
              sample.get("props")
              if isinstance(sample.get("props"), dict)
              else {}
          ),
          "data": (
              sample.get("data")
              if isinstance(sample.get("data"), (dict, list))
              else {}
          ),
          "components": [
              c for c in sample.get("components") or [] if isinstance(c, dict)
          ],
      },
  }
  strategy = str(raw.get("strategy") or "").strip().lower()
  if strategy in VALID_STRATEGIES:
    out["strategy"] = strategy
  comp_raw = raw.get("composition")
  if isinstance(comp_raw, dict):
    c_root = str(comp_raw.get("root") or "").strip()
    c_list = [
        c for c in comp_raw.get("components") or [] if isinstance(c, dict)
    ]
    if c_root or c_list:
      out["composition"] = {"root": c_root, "components": c_list}
  return out


def is_definition(v: Any) -> bool:
  """New-style definition (vs a legacy raw props JSON schema)."""
  return isinstance(v, dict) and (
      isinstance(v.get("web"), dict)
      or isinstance(v.get("composition"), dict)
      or ("props" in v and isinstance(v.get("props"), dict))
  )
