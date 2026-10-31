"""Purpose-built validator for A2UI v0.9 server-to-client messages.

Not a general JSON-schema engine: checks the envelope, the adjacency list
(root, unique ids, resolvable references), catalog membership, required and
unknown properties, enum values, dynamic-value shapes and function names.
Errors use JSON-pointer paths into the message list (e.g.
`/1/updateComponents/components/3/variant`).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from core.a2ui.catalog import Catalog, ComponentSpec

MESSAGE_KINDS = (
    "createSurface",
    "updateComponents",
    "updateDataModel",
    "deleteSurface",
)
COMMON_ALLOWED = {"id", "component", "accessibility", "weight"}
RETURN_TYPES = {"string", "number", "boolean", "array", "object", "any", "void"}


@dataclasses.dataclass
class ValidationResult:
  ok: bool
  errors: list[dict[str, str]]
  warnings: list[dict[str, str]]
  stats: dict[str, Any]

  def to_dict(self) -> dict[str, Any]:
    return dataclasses.asdict(self)

  def summary(self, limit: int = 20) -> str:
    lines = [f"{e['path']}: {e['message']}" for e in self.errors[:limit]]
    if len(self.errors) > limit:
      lines.append(f"... and {len(self.errors) - limit} more")
    return "\n".join(lines)


class _V:

  def __init__(self, catalog: Catalog):
    self.catalog = catalog
    self.enabled = catalog.enabled_components()
    self.errors: list[dict[str, str]] = []
    self.warnings: list[dict[str, str]] = []

  def err(self, path: str, msg: str) -> None:
    self.errors.append({"path": path, "message": msg})

  def warn(self, path: str, msg: str) -> None:
    self.warnings.append({"path": path, "message": msg})

  # ------------------------------------------------------------ values
  def function_call(self, v: dict[str, Any], path: str) -> None:
    name = v.get("call")
    if not isinstance(name, str) or not name:
      self.err(path + "/call", "function call needs a string 'call'")
      return
    if name not in self.catalog.functions:
      self.err(path + "/call", f"unknown function '{name}'")
    rt = v.get("returnType")
    if rt is not None and rt not in RETURN_TYPES:
      self.err(path + "/returnType", f"invalid returnType '{rt}'")
    args = v.get("args")
    fschema = self.catalog.functions.get(name) or {}
    aschema = (fschema.get("properties") or {}).get("args") or {}
    allowed = set((aschema.get("properties") or {}).keys())
    if args is None and aschema.get("required"):
      self.err(path, f"{name} requires args {aschema['required']}")
    if args is not None:
      if not isinstance(args, dict):
        self.err(path + "/args", "args must be an object")
      else:
        if allowed:
          unknown = sorted(set(args) - allowed)
          if unknown:
            self.err(
                path + "/args",
                f"{name} does not accept {unknown}; "
                f"allowed args: {sorted(allowed)}",
            )
          missing = [r for r in aschema.get("required") or [] if r not in args]
          if missing:
            self.err(
                path + "/args", f"{name} is missing required args {missing}"
            )
        for k, a in args.items():
          if isinstance(a, dict) and ("path" in a or "call" in a):
            self.dynamic(a, f"{path}/args/{k}", "any")
          elif isinstance(a, list):
            for i, x in enumerate(a):
              if isinstance(x, dict) and ("path" in x or "call" in x):
                self.dynamic(x, f"{path}/args/{k}/{i}", "any")
    extra = set(v) - {"call", "args", "returnType"}
    if extra:
      self.err(path, f"unexpected keys in function call: {sorted(extra)}")

  def dynamic(self, v: Any, path: str, kind: str) -> None:
    """kind: string|number|boolean|stringlist|any."""
    if isinstance(v, dict):
      if "path" in v:
        if not isinstance(v["path"], str):
          self.err(path + "/path", "path must be a string")
        extra = set(v) - {"path"}
        if extra:
          self.err(
              path, f"data binding allows only 'path' (got {sorted(extra)})"
          )
        return
      if "call" in v:
        self.function_call(v, path)
        return
      if any(k.startswith("literal") for k in v):
        self.err(
            path,
            "v0.8 literal* wrappers are not valid in v0.9; use a plain literal",
        )
        return
      if kind == "any":
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

  def action(self, v: Any, path: str) -> None:
    if not isinstance(v, dict):
      self.err(path, "action must be an object")
      return
    if "event" in v:
      ev = v["event"]
      if not isinstance(ev, dict) or not isinstance(ev.get("name"), str):
        self.err(path + "/event", "event needs a string 'name'")
      elif "context" in ev and not isinstance(ev["context"], dict):
        self.err(path + "/event/context", "context must be an object")
    elif "functionCall" in v:
      fc = v["functionCall"]
      if isinstance(fc, dict):
        self.function_call(fc, path + "/functionCall")
      else:
        self.err(path + "/functionCall", "functionCall must be an object")
    else:
      self.err(path, "action needs 'event' or 'functionCall'")

  def checks(self, v: Any, path: str) -> None:
    if not isinstance(v, list):
      self.err(path, "checks must be an array")
      return
    for i, c in enumerate(v):
      p = f"{path}/{i}"
      if not isinstance(c, dict) or "condition" not in c:
        self.err(p, "check needs 'condition' and 'message'")
        continue
      self.dynamic(c["condition"], p + "/condition", "boolean")
      if not isinstance(c.get("message"), str):
        self.err(p + "/message", "check message must be a string")

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
      self.checks(v, path)
      return
    if t == "ComponentId":
      if not isinstance(v, str):
        self.err(path, "expected a component id string")
      else:
        refs.append((v, path))
      return
    if (
        t.startswith("array<ComponentId> | ")
        or name == "children"
        or t == "ChildList"
    ):
      if isinstance(v, list):
        for i, c in enumerate(v):
          if not isinstance(c, str):
            self.err(f"{path}/{i}", "child id must be a string")
          else:
            refs.append((c, f"{path}/{i}"))
      elif isinstance(v, dict):
        if not isinstance(v.get("componentId"), str) or not isinstance(
            v.get("path"), str
        ):
          self.err(path, "template children need 'componentId' and 'path'")
        else:
          refs.append((v["componentId"], path + "/componentId"))
      else:
        self.err(path, "children must be an id array or a template object")
      return
    if t == "Action":
      self.action(v, path)
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
        else:
          self.dynamic(o["label"], f"{path}/{i}/label", "string")
      return
    if name == "backgroundImage" and spec.name == "Card":
      if isinstance(v, dict) and "path" not in v and "call" not in v:
        if "url" not in v:
          self.err(path, "Card.backgroundImage object needs 'url'")
        else:
          self.dynamic(v["url"], f"{path}/url", "string")
        if v.get("scrim") not in (None, "auto", "dark", "light", "none"):
          self.err(
              f"{path}/scrim",
              "scrim must be one of ['auto', 'dark', 'light', 'none']",
          )
        if v.get("fit") not in (None, "cover", "contain"):
          self.err(f"{path}/fit", "fit must be 'cover' or 'contain'")
      else:
        self.dynamic(v, path, "string")
      return
    kind = {
        "DynamicString": "string",
        "DynamicNumber": "number",
        "DynamicBoolean": "boolean",
        "DynamicStringList": "stringlist",
        "DynamicValue": "any",
    }.get(t)
    if kind:
      self.dynamic(v, path, kind)
      return
    if ps.enum:
      if isinstance(v, dict) and "|" in t:
        # e.g. Icon.name: enum | {svgPath} | {url} | {svg} | DataBinding
        if "path" in v or "call" in v:
          self.dynamic(v, path, "any")
        elif "url" in v:
          self.dynamic(v["url"], f"{path}/url", "string")
        elif "svgPath" in v:
          if not isinstance(v["svgPath"], str) or not v["svgPath"].strip():
            self.err(f"{path}/svgPath", "svgPath must be a non-empty string")
        elif "svg" in v:
          if not isinstance(v["svg"], str) or "<svg" not in v["svg"].lower():
            self.err(f"{path}/svg", "svg must be an <svg> string")
        else:
          self.err(
              path,
              "expected an object with 'svgPath', 'url', 'svg', or 'path'",
          )
        return
      if (
          spec.name == "Icon"
          and isinstance(v, str)
          and v.startswith(("/", "http://", "https://", "data:image/"))
      ):
        return
      if not isinstance(v, str) or v not in ps.enum:
        self.err(path, f"'{v}' is not one of {ps.enum}")
      return
    if spec.custom and t.startswith("array"):
      if not isinstance(v, list) and not (
          isinstance(v, dict) and isinstance(v.get("path"), str)
      ):
        self.err(path, "expected an array or a {path} data binding")
      return
    if t in ("number", "integer") and (
        not isinstance(v, (int, float)) or isinstance(v, bool)
    ):
      self.err(path, f"expected {t}")
    elif t == "boolean" and not isinstance(v, bool):
      self.err(path, "expected boolean")
    elif t == "string" and not isinstance(v, str):
      self.err(path, "expected string")

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
    name = comp.get("component")
    if isinstance(name, dict):
      self.err(
          path + "/component",
          'v0.8 shape ({"Text": {...}}) is not '
          'valid in v0.9; use "component": "Text" with flat props',
      )
      return cid
    if not isinstance(name, str):
      self.err(path + "/component", "missing 'component' type name")
      return cid
    spec = self.catalog.components.get(name)
    if spec is None:
      self.err(path + "/component", f"'{name}' is not in the catalog")
      return cid
    if name not in self.enabled:
      self.err(
          path + "/component", f"'{name}' is disabled in this project's catalog"
      )
    for req in spec.required:
      if req not in comp:
        self.err(path, f"{name} is missing required property '{req}'")
    for k, v in comp.items():
      if k in COMMON_ALLOWED:
        if k == "weight" and (
            not isinstance(v, (int, float)) or isinstance(v, bool)
        ):
          self.err(f"{path}/weight", "weight must be a number")
        continue
      if k not in spec.props:
        self.err(f"{path}/{k}", f"unknown property '{k}' for {name}")
        continue
      self.prop(spec, k, v, f"{path}/{k}", refs)
    return cid


def validate(messages: Any, catalog: Catalog) -> ValidationResult:
  v = _V(catalog)
  surfaces: dict[str, dict[str, Any]] = {}
  n_components = 0
  if not isinstance(messages, list) or not messages:
    v.err("", "expected a non-empty array of A2UI messages")
    return ValidationResult(False, v.errors, v.warnings, {})
  for i, msg in enumerate(messages):
    p = f"/{i}"
    if not isinstance(msg, dict):
      v.err(p, "message must be an object")
      continue
    if msg.get("version") != "v0.9":
      v.err(p + "/version", 'every message needs "version": "v0.9"')
    kinds = [k for k in MESSAGE_KINDS if k in msg]
    extra = set(msg) - set(MESSAGE_KINDS) - {"version"}
    if extra:
      v.err(
          p,
          f"unexpected keys {sorted(extra)} (v0.8 message names like "
          "beginRendering/surfaceUpdate are not valid in v0.9)"
          if extra & {"beginRendering", "surfaceUpdate", "dataModelUpdate"}
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
    sid = body.get("surfaceId")
    if not isinstance(sid, str) or not sid:
      v.err(bp + "/surfaceId", "surfaceId is required")
      continue
    s = surfaces.setdefault(
        sid, {"created": False, "ids": {}, "refs": [], "created_at": None}
    )
    if kind == "createSurface":
      s["created"] = True
      s["created_at"] = i
      if not isinstance(body.get("catalogId"), str):
        v.err(bp + "/catalogId", "catalogId is required")
      theme = body.get("theme")
      if theme is not None and not isinstance(theme, dict):
        v.err(bp + "/theme", "theme must be an object")
    elif kind == "updateComponents":
      comps = body.get("components")
      if not isinstance(comps, list):
        v.err(bp + "/components", "components must be an array")
        continue
      for j, comp in enumerate(comps):
        cp = f"{bp}/components/{j}"
        refs: list[tuple[str, str]] = []
        cid = v.component(comp, cp, refs)
        n_components += 1
        if cid:
          if cid in s["ids"] and s["ids"][cid][1] == i:
            v.err(cp + "/id", f"duplicate component id '{cid}'")
          s["ids"][cid] = (cp, i)
        s["refs"].extend(refs)
    elif kind == "updateDataModel":
      path = body.get("path")
      if path is not None and (
          not isinstance(path, str) or (path and not path.startswith("/"))
      ):
        v.err(bp + "/path", "path must be a JSON pointer starting with '/'")
  for sid, s in surfaces.items():
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
  return ValidationResult(not v.errors, v.errors, v.warnings, stats)
