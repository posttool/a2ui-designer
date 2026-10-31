"""Purpose-built validator for A2UI v1.0 agent-to-renderer messages.

Not a general JSON-schema engine: checks the envelope, the adjacency list
(root, unique UAX #31 ids, resolvable references), per-component catalog
resolution (component `catalogId` → surface default → error), catalog
membership, required and unknown properties, enum values, dynamic-value shapes
(`{"@path"}`, `{"@call"}`, `@index`, `@@` escaping), function names and args,
actions and check rules. Errors use JSON-pointer paths into the message list
(e.g. `/1/updateComponents/components/3/variant`).

Design-system lint (components disabled for the project) is reported
separately in `ValidationResult.lint`; it does not make a payload invalid
A2UI, but generators treat it as something to repair.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from core.a2ui.catalog import Catalog, ComponentSpec, PROTOCOL_VERSION

MESSAGE_KINDS = (
    "createSurface",
    "updateComponents",
    "updateDataModel",
    "deleteSurface",
    "callRendererFunction",
    "agentFunctionResponse",
)
IGNORED_KINDS = ("callRendererFunction", "agentFunctionResponse")
V08_KINDS = {"beginRendering", "surfaceUpdate", "dataModelUpdate"}
CREATE_KEYS = {
    "surfaceId",
    "catalogId",
    "sendDataModel",
    "components",
    "dataModel",
    "metadata",
}
COMMON_ALLOWED = {"id", "component", "catalogId", "accessibility", "weight",
                  "metadata"}
ACCESSIBILITY_KEYS = {"label", "description", "live", "hidden"}
FUNCTION_KEYS = {"@call", "args", "catalogId"}
REF_TYPES = {"ComponentId", "Child"}
LIST_REF_TYPES = {"ChildList"}
V09_HINT = (
    'A2UI v0.9 is not supported; this app speaks A2UI v1.0: set "version":'
    ' "v1.0", bind data with {"@path": "/x"}, call functions with'
    ' {"@call": "name", "args": {...}} (no returnType), use Markdown'
    ' headings in Text instead of h1-h5 variants, and drop'
    " createSurface.theme"
)
_IDENT_RE = re.compile(r"^[^\W\d]\w*$")


def is_identifier(s: Any) -> bool:
  """UAX #31-style identifier: letter/underscore then letters/digits/_."""
  return isinstance(s, str) and bool(_IDENT_RE.match(s))


@dataclasses.dataclass
class ValidationResult:
  ok: bool
  errors: list[dict[str, str]]
  warnings: list[dict[str, str]]
  stats: dict[str, Any]
  # Design-system lint (e.g. disabled components); not A2UI errors.
  lint: list[dict[str, str]] = dataclasses.field(default_factory=list)

  @property
  def clean(self) -> bool:
    """Valid A2UI and no design-system lint."""
    return self.ok and not self.lint

  def to_dict(self) -> dict[str, Any]:
    return dataclasses.asdict(self)

  def summary(self, limit: int = 20) -> str:
    items = self.errors + self.lint
    lines = [f"{e['path']}: {e['message']}" for e in items[:limit]]
    if len(items) > limit:
      lines.append(f"... and {len(items) - limit} more")
    return "\n".join(lines)


class _V:

  def __init__(self, catalog: Catalog):
    self.catalog = catalog
    self.enabled = catalog.enabled_components()
    self.members = catalog.catalog_members()
    self.fcatalogs = catalog.function_catalogs()
    self.errors: list[dict[str, str]] = []
    self.warnings: list[dict[str, str]] = []
    self.lint: list[dict[str, str]] = []
    self.surface_catalog: str | None = None

  def err(self, path: str, msg: str) -> None:
    self.errors.append({"path": path, "message": msg})

  def warn(self, path: str, msg: str) -> None:
    self.warnings.append({"path": path, "message": msg})

  # ------------------------------------------------------------ values
  def _functions_for(self, v: dict[str, Any], path: str):
    cid = v.get("catalogId")
    if cid is not None and not isinstance(cid, str):
      self.err(path + "/catalogId", "catalogId must be a string")
      cid = None
    cid = cid or self.surface_catalog
    if not cid:
      self.err(
          path,
          "cannot resolve the function's catalog: set createSurface.catalogId"
          " or the call's catalogId",
      )
      return None
    funcs = self.fcatalogs.get(cid)
    if funcs is None:
      self.err(path, f"unknown catalogId '{cid}' for function call")
    return funcs

  def function_call(self, v: dict[str, Any], path: str) -> None:
    name = v.get("@call")
    if not isinstance(name, str) or not name:
      self.err(path + "/@call", "function call needs a string '@call'")
      return
    if "returnType" in v:
      self.err(
          path + "/returnType",
          "returnType is catalog metadata in v1.0; do not send it on the wire",
      )
    extra = set(v) - FUNCTION_KEYS - {"returnType"}
    if extra:
      self.err(path, f"unexpected keys in function call: {sorted(extra)}")
    args = v.get("args")
    if name == "@index":
      if "catalogId" in v:
        self.err(path + "/catalogId", "@index is a system function; no catalogId")
      if args is not None:
        if not isinstance(args, dict) or set(args) - {"offset"}:
          self.err(path + "/args", "@index accepts only an optional 'offset'")
        elif "offset" in args:
          self.dynamic(args["offset"], path + "/args/offset", "number")
      return
    if name.startswith("@"):
      self.err(path + "/@call", f"unknown system function '{name}'")
      return
    funcs = self._functions_for(v, path)
    if funcs is None:
      return
    if name not in funcs:
      self.err(path + "/@call", f"unknown function '{name}'")
      return
    fschema = funcs.get(name) or {}
    aschema = (fschema.get("properties") or {}).get("args") or {}
    allowed = aschema.get("properties") or {}
    required = aschema.get("required") or []
    if args is None:
      if required:
        self.err(path, f"{name} requires args {required}")
      return
    if not isinstance(args, dict):
      self.err(path + "/args", "args must be an object")
      return
    if allowed:
      unknown = sorted(set(args) - set(allowed))
      if unknown:
        self.err(
            path + "/args",
            f"{name} does not accept {unknown}; "
            f"allowed args: {sorted(allowed)}",
        )
    missing = [r for r in required if r not in args]
    if missing:
      self.err(path + "/args", f"{name} is missing required args {missing}")
    any_of = aschema.get("anyOf")
    if isinstance(any_of, list) and any_of:
      groups = [a.get("required") or [] for a in any_of if isinstance(a, dict)]
      if groups and not any(all(r in args for r in g) for g in groups):
        alts = " or ".join("+".join(g) for g in groups)
        self.err(path + "/args", f"{name} needs {alts}")
    for k, a in args.items():
      self.arg(a, allowed.get(k) or {}, f"{path}/args/{k}")

  def arg(self, a: Any, schema: dict[str, Any], path: str) -> None:
    ref = str(schema.get("$ref") or "")
    if ref:
      self.typed(a, ref.rsplit("/", 1)[-1], path)
      return
    t = schema.get("type")
    if t == "array":
      if isinstance(a, list):
        items = schema.get("items") or {}
        for i, x in enumerate(a):
          self.arg(x, items, f"{path}/{i}")
        if schema.get("minItems") and len(a) < schema["minItems"]:
          self.err(path, f"needs at least {schema['minItems']} items")
      elif isinstance(a, dict) and ("@path" in a or "@call" in a):
        self.dynamic(a, path, "any")
      else:
        self.err(path, "expected an array")
      return
    if isinstance(schema.get("oneOf"), list):
      if isinstance(a, dict):
        if "@path" in a or "@call" in a:
          self.dynamic(a, path, "any")
        else:
          self.err(path, "expected a literal, data binding or function call")
      elif any(
          isinstance(o, dict) and o.get("format") == "uri"
          for o in schema["oneOf"]
      ):
        if not isinstance(a, str) or not re.match(r"^[A-Za-z][\w+.-]*:", a):
          self.err(path, "expected an absolute URI")
      return
    if t in ("integer", "number"):
      if (
          not isinstance(a, (int, float))
          or isinstance(a, bool)
          or (t == "integer" and isinstance(a, float) and not a.is_integer())
      ):
        self.err(path, f"expected a literal {t}")
      elif "minimum" in schema and a < schema["minimum"]:
        self.err(path, f"must be >= {schema['minimum']}")
      return
    if t == "string":
      if not isinstance(a, str):
        self.err(path, "expected a literal string")
      return
    if t == "boolean":
      if not isinstance(a, bool):
        self.err(path, "expected a literal boolean")
      return
    self.dynamic(a, path, "any")

  def literal_object(self, v: dict[str, Any], path: str) -> None:
    """Plain objects: single-'@' keys are reserved (escape as '@@')."""
    for k, x in v.items():
      if k.startswith("@") and not k.startswith("@@"):
        self.err(
            f"{path}/{k}",
            f"unknown reserved key '{k}'; literal keys starting with '@' must"
            " be escaped as '@@'",
        )
      if isinstance(x, dict):
        self.literal_object(x, f"{path}/{k}")
      elif isinstance(x, list):
        for i, y in enumerate(x):
          if isinstance(y, dict):
            self.literal_object(y, f"{path}/{k}/{i}")

  def dynamic(self, v: Any, path: str, kind: str) -> None:
    """kind: string|number|boolean|stringlist|any."""
    if isinstance(v, dict):
      if "@path" in v:
        if not isinstance(v["@path"], str):
          self.err(path + "/@path", "@path must be a string")
        extra = set(v) - {"@path"}
        if extra:
          self.err(
              path, f"data binding allows only '@path' (got {sorted(extra)})"
          )
        return
      if "@call" in v:
        self.function_call(v, path)
        return
      if kind != "any" and ("path" in v or "call" in v):
        self.err(
            path,
            "v0.9 binding syntax: use {\"@path\": ...} / {\"@call\": ...} in"
            " A2UI v1.0",
        )
        return
      if any(str(k).startswith("literal") for k in v):
        self.err(
            path,
            "v0.8 literal* wrappers are not valid in v1.0; use a plain literal",
        )
        return
      if kind == "any":
        self.literal_object(v, path)
        if set(v) == {"path"} and isinstance(v["path"], str):
          self.warn(
              path,
              'a literal object {"path": ...} here is NOT a binding in v1.0;'
              ' use {"@path": ...} to bind',
          )
        return
      self.err(path, f"expected {kind}, data binding or function call")
      return
    ok = {
        "string": isinstance(v, str),
        "number": isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": isinstance(v, bool),
        "stringlist": (
            isinstance(v, list) and all(isinstance(x, str) for x in v)
        ),
        "any": True,
    }.get(kind, True)
    if not ok:
      self.err(path, f"expected {kind}, got {type(v).__name__}")
    elif kind == "any" and isinstance(v, list):
      for i, x in enumerate(v):
        if isinstance(x, dict):
          self.dynamic(x, f"{path}/{i}", "any")

  def typed(self, v: Any, t: str, path: str, refs=None) -> None:
    """Validates `v` against a common_types name."""
    kind = {
        "DynamicString": "string",
        "DynamicNumber": "number",
        "DynamicBoolean": "boolean",
        "DynamicStringList": "stringlist",
        "DynamicValue": "any",
    }.get(t)
    if kind:
      self.dynamic(v, path, kind)
    elif t == "DataBinding":
      if not isinstance(v, dict) or set(v) != {"@path"}:
        self.err(path, 'expected a data binding {"@path": "/..."}')
    elif t == "FunctionCall":
      if not isinstance(v, dict) or "@call" not in v:
        self.err(path, 'expected a function call {"@call": ...}')
      else:
        self.function_call(v, path)
    elif t == "Action":
      self.action(v, path)
    elif t == "CheckRule":
      self.check_rule(v, path)
    elif t == "AccessibilityAttributes":
      self.accessibility(v, path)
    elif t in REF_TYPES:
      if not isinstance(v, str):
        self.err(path, "expected a component id string")
      elif refs is not None:
        refs.append((v, path))
    elif t in LIST_REF_TYPES:
      self.child_list(v, path, refs)

  def metadata(self, v: Any, path: str) -> None:
    if not isinstance(v, dict):
      self.err(path, "metadata must be an object")
      return
    extra = set(v) - {"extensions"}
    if extra:
      self.err(path, f"unexpected metadata keys {sorted(extra)}")
    ext = v.get("extensions")
    if ext is not None:
      if not isinstance(ext, dict):
        self.err(path + "/extensions", "extensions must be an object")
      else:
        for k in ext:
          if not is_identifier(k):
            self.err(
                f"{path}/extensions/{k}",
                f"extension key '{k}' must be a UAX #31 identifier",
            )

  def accessibility(self, v: Any, path: str) -> None:
    if not isinstance(v, dict):
      self.err(path, "accessibility must be an object")
      return
    extra = set(v) - ACCESSIBILITY_KEYS
    if extra:
      self.err(path, f"unknown accessibility keys {sorted(extra)}")
    for k in ("label", "description"):
      if k in v:
        self.dynamic(v[k], f"{path}/{k}", "string")
    if "hidden" in v:
      self.dynamic(v["hidden"], path + "/hidden", "boolean")
    if "live" in v and v["live"] not in ("off", "polite", "assertive"):
      self.err(path + "/live", "live must be one of off|polite|assertive")

  def action(self, v: Any, path: str) -> None:
    if not isinstance(v, dict):
      self.err(path, "action must be an object")
      return
    keys = set(v)
    if keys == {"event"}:
      ev = v["event"]
      if not isinstance(ev, dict) or not isinstance(ev.get("name"), str):
        self.err(path + "/event", "event needs a string 'name'")
        return
      extra = set(ev) - {"name", "context", "userMessage"}
      if extra:
        self.err(path + "/event", f"unexpected event keys {sorted(extra)}")
      if "userMessage" in ev:
        self.dynamic(ev["userMessage"], path + "/event/userMessage", "string")
      if "context" in ev:
        if not isinstance(ev["context"], dict):
          self.err(path + "/event/context", "context must be an object")
        else:
          for k, x in ev["context"].items():
            self.dynamic(x, f"{path}/event/context/{k}", "any")
    elif keys == {"functionCall"}:
      fc = v["functionCall"]
      if isinstance(fc, dict) and "@call" in fc:
        self.function_call(fc, path + "/functionCall")
      elif isinstance(fc, dict) and "call" in fc:
        self.err(
            path + "/functionCall",
            'v0.9 function call syntax: use {"@call": ...} in A2UI v1.0',
        )
      else:
        self.err(path + "/functionCall", "functionCall needs '@call'")
    else:
      self.err(path, "action needs exactly one of 'event' or 'functionCall'")

  def check_rule(self, c: Any, path: str) -> None:
    if not isinstance(c, dict) or "condition" not in c:
      self.err(path, "check needs a 'condition'")
      return
    extra = set(c) - {"condition", "message"}
    if extra:
      self.err(path, f"unexpected check keys {sorted(extra)}")
    cond = c["condition"]
    if not isinstance(cond, dict) or not ("@path" in cond or "@call" in cond):
      self.err(
          path + "/condition",
          "check condition must be a data binding or function call",
      )
    else:
      self.dynamic(cond, path + "/condition", "boolean")
    if "message" in c and not isinstance(c["message"], str):
      self.err(path + "/message", "check message must be a string")

  def child_list(self, v: Any, path: str, refs) -> None:
    if isinstance(v, list):
      for i, c in enumerate(v):
        if not isinstance(c, str):
          self.err(f"{path}/{i}", "child id must be a string")
        elif refs is not None:
          refs.append((c, f"{path}/{i}"))
    elif isinstance(v, dict):
      if "@path" in v and "path" not in v:
        self.err(
            path,
            "template children use {\"componentId\": ..., \"path\": ...}"
            " (plain 'path', not '@path')",
        )
        return
      if not isinstance(v.get("componentId"), str) or not isinstance(
          v.get("path"), str
      ):
        self.err(path, "template children need 'componentId' and 'path'")
        return
      extra = set(v) - {"componentId", "path"}
      if extra:
        self.err(path, f"unexpected template keys {sorted(extra)}")
      if refs is not None:
        refs.append((v["componentId"], path + "/componentId"))
    else:
      self.err(path, "children must be an id array or a template object")

  # ------------------------------------------------------------ props
  def prop(
      self,
      spec: ComponentSpec,
      name: str,
      v: Any,
      path: str,
      refs: list[tuple[str, str]],
  ) -> None:
    ps = spec.props[name]
    t = ps.type
    if name == "checks":
      if not isinstance(v, list):
        self.err(path, "checks must be an array")
        return
      for i, c in enumerate(v):
        self.check_rule(c, f"{path}/{i}")
      return
    if t in REF_TYPES or t in LIST_REF_TYPES or t in (
        "Action",
        "DataBinding",
        "FunctionCall",
        "AccessibilityAttributes",
    ):
      self.typed(v, t, path, refs)
      return
    if t.startswith("array<ComponentId> | ") or (
        name == "children" and not spec.custom
    ):
      self.child_list(v, path, refs)
      return
    if name == "tabs" and spec.name == "Tabs":
      if not isinstance(v, list) or not v:
        self.err(path, "tabs must be a non-empty array")
        return
      for i, tab in enumerate(v):
        tp = f"{path}/{i}"
        if not isinstance(tab, dict):
          self.err(tp, "tab must be an object")
          continue
        extra = set(tab) - {"title", "child"}
        if extra:
          self.err(tp, f"unexpected tab keys {sorted(extra)}")
        if "title" not in tab:
          self.err(tp, "tab needs 'title'")
        else:
          self.dynamic(tab["title"], tp + "/title", "string")
        if not isinstance(tab.get("child"), str):
          self.err(tp, "tab needs 'child' id")
        else:
          refs.append((tab["child"], tp + "/child"))
      return
    if name == "options" and spec.name == "ChoicePicker":
      if not isinstance(v, list):
        self.err(path, "options must be an array")
        return
      for i, o in enumerate(v):
        if not isinstance(o, dict) or "label" not in o or "value" not in o:
          self.err(f"{path}/{i}", "option needs 'label' and 'value'")
          continue
        self.dynamic(o["label"], f"{path}/{i}/label", "string")
        if not isinstance(o["value"], str):
          self.err(f"{path}/{i}/value", "option value must be a string")
        extra = set(o) - {"label", "value"}
        if extra:
          self.err(f"{path}/{i}", f"unexpected option keys {sorted(extra)}")
      return
    if name == "svg" and spec.name == "SvgIcon":
      if not isinstance(v, str) or "<svg" not in v.lower():
        self.err(path, "svg must be inline <svg> markup")
      return
    if t in (
        "DynamicString",
        "DynamicNumber",
        "DynamicBoolean",
        "DynamicStringList",
        "DynamicValue",
    ):
      self.typed(v, t, path, refs)
      return
    if ps.enum:
      if isinstance(v, dict) and "|" in t:
        # Icon.name: enum | {svgPath} | DataBinding
        if "@path" in v:
          self.dynamic(v, path, "any")
        elif "svgPath" in v:
          if set(v) != {"svgPath"}:
            self.err(path, "svgPath object allows only 'svgPath'")
          self.dynamic(v["svgPath"], f"{path}/svgPath", "string")
        elif "path" in v:
          self.err(path, 'v0.9 binding syntax: use {"@path": ...} in v1.0')
        elif "url" in v or "svg" in v:
          self.err(
              path,
              "Icon takes a named icon, {svgPath} or a binding; image/SVG"
              " icons are the SvgIcon extension component",
          )
        else:
          self.err(path, "expected an icon name, {'svgPath'} or {'@path'}")
        return
      if not isinstance(v, str) or v not in ps.enum:
        hint = ""
        if spec.name == "Text" and name == "variant":
          hint = (
              " (v1.0 Text has only caption|body; write headings as Markdown,"
              ' e.g. "text": "# Title")'
          )
        self.err(path, f"'{v}' is not one of {ps.enum}{hint}")
      return
    if spec.custom and t.startswith("array"):
      if not isinstance(v, list) and not (
          isinstance(v, dict) and isinstance(v.get("@path"), str)
      ):
        self.err(path, 'expected an array or a {"@path"} data binding')
      return
    if t in ("number", "integer"):
      if not isinstance(v, (int, float)) or isinstance(v, bool):
        self.err(path, f"expected {t}")
      else:
        if ps.minimum is not None and v < ps.minimum:
          self.err(path, f"must be >= {ps.minimum}")
        if ps.maximum is not None and v > ps.maximum:
          self.err(path, f"must be <= {ps.maximum}")
    elif t == "boolean" and not isinstance(v, bool):
      self.err(path, "expected boolean")
    elif t == "string" and not isinstance(v, str):
      self.err(path, "expected string")

  def resolve_catalog(
      self, comp: dict[str, Any], name: str, path: str
  ) -> str | None:
    cid = comp.get("catalogId")
    if cid is not None and not isinstance(cid, str):
      self.err(path + "/catalogId", "catalogId must be a string")
      return None
    cid = cid or self.surface_catalog
    if not cid:
      self.err(
          path,
          f"cannot resolve the catalog for '{name}': set createSurface"
          ".catalogId or the component's catalogId",
      )
      return None
    members = self.members.get(cid)
    if members is None:
      self.err(
          path + ("/catalogId" if comp.get("catalogId") else ""),
          f"unknown catalogId '{cid}'",
      )
      return None
    if name not in members:
      home = self.catalog.catalog_id_for(name)
      if home:
        self.err(
            path + "/component",
            f"'{name}' is not in catalog '{cid}'; set \"catalogId\":"
            f' "{home}" on the component',
        )
      else:
        self.err(path + "/component", f"'{name}' is not in the catalog")
      return None
    return cid

  def component(
      self, comp: Any, path: str, refs: list[tuple[str, str]]
  ) -> str | None:
    if not isinstance(comp, dict):
      self.err(path, "component must be an object")
      return None
    cid = comp.get("id")
    if not isinstance(cid, str) or not cid:
      self.err(path + "/id", "component needs a string 'id'")
      cid = None
    elif not is_identifier(cid):
      self.err(
          path + "/id",
          f"component id '{cid}' must be a UAX #31 identifier (letters,"
          " digits, underscores; no hyphens; not starting with a digit)",
      )
    name = comp.get("component")
    if isinstance(name, dict):
      self.err(
          path + "/component",
          'v0.8 shape ({"Text": {...}}) is not '
          'valid in v1.0; use "component": "Text" with flat props',
      )
      return cid
    if not isinstance(name, str):
      self.err(path + "/component", "missing 'component' type name")
      return cid
    if name == "Surface":
      self.err(path + "/component", "'Surface' is reserved")
      return cid
    spec = self.catalog.components.get(name)
    if spec is None:
      self.err(path + "/component", f"'{name}' is not in the catalog")
      return cid
    if self.resolve_catalog(comp, name, path) is None:
      return cid
    if name not in self.enabled:
      self.lint.append({
          "path": path + "/component",
          "message": f"'{name}' is disabled in this project's catalog",
      })
    for req in spec.required:
      if req not in comp:
        self.err(path, f"{name} is missing required property '{req}'")
    for group in spec.one_of:
      present = [g for g in group if g in comp]
      if len(present) != 1:
        self.err(path, f"{name} needs exactly one of {group}")
    for k, v in comp.items():
      if k in COMMON_ALLOWED:
        if k == "weight" and (
            not isinstance(v, (int, float)) or isinstance(v, bool)
        ):
          self.err(f"{path}/weight", "weight must be a number")
        elif k == "accessibility":
          self.accessibility(v, f"{path}/accessibility")
        elif k == "metadata":
          self.metadata(v, f"{path}/metadata")
        continue
      if k not in spec.props:
        self.err(f"{path}/{k}", f"unknown property '{k}' for {name}")
        continue
      self.prop(spec, k, v, f"{path}/{k}", refs)
    return cid


def _call_renderer_function(v: _V, body: dict[str, Any], bp: str) -> None:
  if not isinstance(body.get("functionCallId"), str):
    v.err(bp + "/functionCallId", "functionCallId is required")
  fc = body.get("callFunction")
  if not isinstance(fc, dict):
    v.err(bp + "/callFunction", "callFunction is required")
    return
  if not isinstance(fc.get("catalogId"), str):
    v.err(bp + "/callFunction/catalogId", "callFunction needs a catalogId")
    return
  v.surface_catalog = None
  v.function_call(fc, bp + "/callFunction")


def _components(v: _V, comps: Any, bp: str, s: dict[str, Any], i: int) -> int:
  if not isinstance(comps, list) or not comps:
    v.err(bp, "components must be a non-empty array")
    return 0
  seen: set[str] = set()
  for j, comp in enumerate(comps):
    cp = f"{bp}/{j}"
    refs: list[tuple[str, str]] = []
    cid = v.component(comp, cp, refs)
    if cid:
      if cid in seen:
        v.err(cp + "/id", f"duplicate component id '{cid}'")
      seen.add(cid)
      s["ids"][cid] = (cp, i)
    s["refs"].extend(refs)
  return len(comps)


def validate(
    messages: Any, catalog: Catalog, *, complete: bool = True
) -> ValidationResult:
  """Validates a message stream.

  `complete=False` validates messages individually (as the upstream schema
  tests do): no root/reference/createSurface checks across the stream.
  """
  v = _V(catalog)
  surfaces: dict[str, dict[str, Any]] = {}
  n_components = 0
  if not isinstance(messages, list) or not messages:
    v.err("", "expected a non-empty array of A2UI messages")
    return ValidationResult(False, v.errors, v.warnings, {})
  v09_reported = False
  for i, msg in enumerate(messages):
    p = f"/{i}"
    if not isinstance(msg, dict):
      v.err(p, "message must be an object")
      continue
    ver = msg.get("version")
    if ver != PROTOCOL_VERSION:
      if isinstance(ver, str) and ver.startswith("v0."):
        if not v09_reported:
          v.err(p + "/version", V09_HINT)
          v09_reported = True
        continue
      v.err(p + "/version", f'every message needs "version": "{PROTOCOL_VERSION}"')
    kinds = [k for k in MESSAGE_KINDS if k in msg]
    extra = set(msg) - set(MESSAGE_KINDS) - {"version"}
    if extra:
      v.err(
          p,
          f"unexpected keys {sorted(extra)} (v0.8 message names like "
          "beginRendering/surfaceUpdate are not valid in v1.0)"
          if extra & V08_KINDS
          else f"unexpected keys {sorted(extra)}",
      )
    if len(kinds) != 1:
      if not extra:
        v.err(p, f"message must contain exactly one of {list(MESSAGE_KINDS)}")
      continue
    kind = kinds[0]
    body = msg[kind]
    bp = f"{p}/{kind}"
    if not isinstance(body, dict):
      v.err(bp, "message body must be an object")
      continue
    if kind == "callRendererFunction":
      _call_renderer_function(v, body, bp)
      continue  # tolerated; nothing to render
    if kind in IGNORED_KINDS:
      continue
    sid = body.get("surfaceId")
    if not isinstance(sid, str) or not sid:
      v.err(bp + "/surfaceId", "surfaceId is required")
      continue
    s = surfaces.setdefault(
        sid,
        {"created": False, "ids": {}, "refs": [], "catalog": None},
    )
    v.surface_catalog = s["catalog"]
    if kind == "createSurface":
      if s["created"]:
        v.err(bp, f"surface '{sid}' was already created")
      s["created"] = True
      if "theme" in body:
        v.err(bp + "/theme", "createSurface.theme was removed in A2UI v1.0")
      unknown = set(body) - CREATE_KEYS - {"theme"}
      if unknown:
        v.err(bp, f"unexpected createSurface keys {sorted(unknown)}")
      cat_id = body.get("catalogId")
      if cat_id is not None:
        if not isinstance(cat_id, str):
          v.err(bp + "/catalogId", "catalogId must be a string")
        elif cat_id not in v.members:
          v.err(bp + "/catalogId", f"unknown catalogId '{cat_id}'")
        else:
          s["catalog"] = cat_id
      v.surface_catalog = s["catalog"]
      if "sendDataModel" in body and not isinstance(
          body["sendDataModel"], bool
      ):
        v.err(bp + "/sendDataModel", "sendDataModel must be a boolean")
      if "dataModel" in body and not isinstance(body["dataModel"], dict):
        v.err(bp + "/dataModel", "dataModel must be an object")
      if "metadata" in body:
        v.metadata(body["metadata"], bp + "/metadata")
      if "components" in body:
        n_components += _components(
            v, body["components"], bp + "/components", s, i
        )
    elif kind == "updateComponents":
      unknown = set(body) - {"surfaceId", "components"}
      if unknown:
        v.err(bp, f"unexpected updateComponents keys {sorted(unknown)}")
      n_components += _components(
          v, body.get("components"), bp + "/components", s, i
      )
    elif kind == "updateDataModel":
      unknown = set(body) - {"surfaceId", "path", "value"}
      if unknown:
        v.err(bp, f"unexpected updateDataModel keys {sorted(unknown)}")
      if "value" not in body:
        v.err(
            bp + "/value",
            "updateDataModel needs 'value' (use null to delete the key)",
        )
      path = body.get("path")
      if path is not None and (
          not isinstance(path, str) or (path and not path.startswith("/"))
      ):
        v.err(bp + "/path", "path must be a JSON pointer starting with '/'")
    elif kind == "deleteSurface":
      unknown = set(body) - {"surfaceId"}
      if unknown:
        v.err(bp, f"unexpected deleteSurface keys {sorted(unknown)}")
  for sid, s in surfaces.items():
    if not complete:
      break
    if not s["created"]:
      v.err("", f"surface '{sid}' has no createSurface message")
    if s["ids"] and "root" not in s["ids"]:
      v.err("", f"surface '{sid}' has no component with id 'root'")
    for ref, rp in s["refs"]:
      if ref not in s["ids"]:
        v.err(rp, f"reference to undefined component '{ref}'")
    referenced = {r for r, _ in s["refs"]}
    for cid, (cp, _) in s["ids"].items():
      if cid != "root" and cid not in referenced:
        v.warn(cp, f"component '{cid}' is never referenced (unreachable)")
  stats = {"surfaces": len(surfaces), "components": n_components}
  return ValidationResult(not v.errors, v.errors, v.warnings, stats, v.lint)
