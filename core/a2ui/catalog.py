"""A2UI v0.9 catalog loading: Basic Catalog + project toggles + custom comps."""

from __future__ import annotations

import dataclasses
import json
import os
from typing import Any

from core.a2ui import custom_web

SEEDS = os.path.join(
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
    "seeds",
)
BASIC_CATALOG_ID = (
    "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
)
COMMON_TYPES_ID = "https://a2ui.org/specification/v0_9/common_types.json"
COMMON_PROPS = {"id", "accessibility", "weight", "component"}


@dataclasses.dataclass
class PropSpec:
  name: str
  type: str
  required: bool = False
  enum: list[str] | None = None
  default: Any = None
  description: str = ""

  def to_dict(self) -> dict[str, Any]:
    d = {
        "name": self.name,
        "type": self.type,
        "required": self.required,
        "description": self.description,
    }
    if self.enum:
      d["enum"] = self.enum
    if self.default is not None:
      d["default"] = self.default
    return d


@dataclasses.dataclass
class ComponentSpec:
  name: str
  description: str
  props: dict[str, PropSpec]
  checkable: bool = False
  custom: bool = False
  usage: str = ""
  has_web: bool = False
  has_composition: bool = False
  strategy: str = ""
  targets: list[str] = dataclasses.field(default_factory=list)

  @property
  def required(self) -> list[str]:
    return [p.name for p in self.props.values() if p.required]

  def to_dict(self, enabled: bool = True) -> dict[str, Any]:
    return {
        "name": self.name,
        "description": self.description,
        "props": [p.to_dict() for p in self.props.values()],
        "checkable": self.checkable,
        "enabled": enabled,
        "custom": self.custom,
        "usage": self.usage,
        "has_web": self.has_web,
        "has_composition": self.has_composition,
        "strategy": self.strategy,
        "targets": list(self.targets),
    }


@dataclasses.dataclass
class Catalog:
  catalog_id: str
  components: dict[str, ComponentSpec]
  functions: dict[str, dict[str, Any]]
  disabled: list[str]
  # name → custom component Definition (web implementation + sample).
  custom_defs: dict[str, dict[str, Any]] = dataclasses.field(
      default_factory=dict
  )

  def enabled_components(self) -> dict[str, ComponentSpec]:
    return {k: v for k, v in self.components.items() if k not in self.disabled}


_BASIC_CACHE: dict[str, Any] = {}


def _load_basic() -> dict[str, Any]:
  if "raw" not in _BASIC_CACHE:
    with open(
        os.path.join(SEEDS, "a2ui_basic_catalog_v0_9.json"), encoding="utf-8"
    ) as fh:
      _BASIC_CACHE["raw"] = json.load(fh)
  return _BASIC_CACHE["raw"]


def _type_label(schema: dict[str, Any]) -> str:
  ref = schema.get("$ref", "")
  if ref:
    return ref.rsplit("/", 1)[-1]
  if "oneOf" in schema or "anyOf" in schema:
    opts = schema.get("oneOf") or schema.get("anyOf")
    return " | ".join(_type_label(o) for o in opts)
  t = schema.get("type", "any")
  if t == "array":
    return f"array<{_type_label(schema.get('items', {}))}>"
  if t == "object" and schema.get("properties"):
    inner = ", ".join(
        f"{k}: {_type_label(v)}" for k, v in schema["properties"].items()
    )
    return "{" + inner + "}"
  return str(t)


def _parse_component(
    name: str, schema: dict[str, Any], custom: bool = False
) -> ComponentSpec:
  props: dict[str, PropSpec] = {}
  required: set[str] = set()
  description = schema.get("description", "")
  checkable = False
  parts = schema.get("allOf") or [schema]
  for part in parts:
    ref = part.get("$ref", "")
    if ref.endswith("/Checkable"):
      checkable = True
      continue
    if ref:
      continue
    description = part.get("description", description)
    for pname, pschema in (part.get("properties") or {}).items():
      if pname in COMMON_PROPS:
        continue
      enum = pschema.get("enum")
      if not enum and isinstance(pschema.get("oneOf"), list):
        for o in pschema["oneOf"]:
          if o.get("enum"):
            enum = o["enum"]
      props[pname] = PropSpec(
          name=pname,
          type=_type_label(pschema),
          enum=enum,
          default=pschema.get("default"),
          description=(pschema.get("description") or "").strip(),
      )
    required.update(part.get("required") or [])
  for r in required:
    if r in props:
      props[r].required = True
  if checkable:
    props["checks"] = PropSpec(
        name="checks",
        type="array<CheckRule>",
        description="Validation rules: [{condition: DynamicBoolean, message}]",
    )
  return ComponentSpec(
      name=name,
      description=description,
      props=props,
      checkable=checkable,
      custom=custom,
  )


def basic_components() -> dict[str, ComponentSpec]:
  raw = _load_basic()
  return {
      name: _parse_component(name, schema)
      for name, schema in raw["components"].items()
  }


def basic_functions() -> dict[str, dict[str, Any]]:
  return dict(_load_basic().get("functions") or {})


def basic_names() -> frozenset[str]:
  return frozenset(_load_basic()["components"])


def build_catalog(
    settings_catalog: dict[str, Any] | None,
    custom_defs: dict[str, dict[str, Any]] | None = None,
) -> Catalog:
  """Basic catalog + project toggles + custom components.

  `settings_catalog.custom_components` may hold legacy raw props schemas or
  (in run snapshots) full Definitions; `custom_defs` (active rows of the
  custom_components table) wins on name clashes.
  """
  cfg = settings_catalog or {}
  comps = basic_components()
  basic = set(comps)
  defs: dict[str, dict[str, Any]] = {}
  merged_custom = dict(cfg.get("custom_components") or {})
  merged_custom.update(custom_defs or {})
  for name, schema in merged_custom.items():
    if not isinstance(schema, dict) or not name or name in basic:
      continue
    if custom_web.is_definition(schema):
      spec = _parse_component(name, schema.get("props") or {}, custom=True)
      if schema.get("description"):
        spec.description = str(schema["description"])
      spec.usage = str(schema.get("usage") or "")
      spec.has_web = bool((schema.get("web") or {}).get("template"))
      comp_def = schema.get("composition") or {}
      spec.has_composition = bool(
          isinstance(comp_def, dict)
          and comp_def.get("root")
          and comp_def.get("components")
      )
      spec.strategy = str(schema.get("strategy") or "")
      targets_def = schema.get("targets") or {}
      if isinstance(targets_def, dict):
        spec.targets = sorted(
            k
            for k, v in targets_def.items()
            if isinstance(v, dict) and (v.get("code") or "").strip()
        )
      defs[name] = schema
    else:
      spec = _parse_component(name, schema, custom=True)
    comps[name] = spec
  disabled = [d for d in cfg.get("disabled_components") or [] if d in comps]
  return Catalog(
      catalog_id=str(cfg.get("catalog_id") or "") or BASIC_CATALOG_ID,
      components=comps,
      functions=basic_functions(),
      disabled=disabled,
      custom_defs=defs,
  )


def _ref_common(schema: Any) -> Any:
  """Rewrites short common_types refs to the canonical absolute URI."""
  if isinstance(schema, dict):
    out = {}
    for k, v in schema.items():
      if (
          k == "$ref"
          and isinstance(v, str)
          and v.startswith("common_types.json")
      ):
        v = COMMON_TYPES_ID + v[len("common_types.json") :]
      out[k] = _ref_common(v)
    return out
  if isinstance(schema, list):
    return [_ref_common(x) for x in schema]
  return schema


def to_a2ui_catalog_json(
    catalog: Catalog,
    catalog_id: str,
    title: str = "",
    description: str = "",
    theme: dict[str, Any] | None = None,
) -> dict[str, Any]:
  """Spec-shaped A2UI v0.9 catalog (enabled Basic comps + custom comps)."""
  raw = _load_basic()
  enabled = catalog.enabled_components()
  components: dict[str, Any] = {}
  for name, spec in enabled.items():
    if not spec.custom and name in raw["components"]:
      components[name] = raw["components"][name]
      continue
    defn = catalog.custom_defs.get(name)
    props_schema = (defn or {}).get("props") or {}
    props = dict(_ref_common(props_schema.get("properties") or {}))
    props = {"component": {"const": name}, **props}
    required = ["component"] + [
        r for r in props_schema.get("required") or [] if r in props
    ]
    components[name] = {
        "type": "object",
        "description": spec.description,
        "allOf": [
            {"$ref": COMMON_TYPES_ID + "#/$defs/ComponentCommon"},
            {"$ref": "#/$defs/CatalogComponentCommon"},
            {
                "type": "object",
                "properties": props,
                "required": required,
            },
        ],
        "unevaluatedProperties": False,
    }
    if spec.usage:
      components[name]["x-a2ui-usage"] = spec.usage
  defs = dict(raw.get("$defs") or {})
  defs["anyComponent"] = dict(
      defs.get("anyComponent") or {},
      oneOf=[{"$ref": f"#/components/{n}"} for n in components],
  )
  if theme:
    th = dict(defs.get("theme") or {"type": "object", "properties": {}})
    th["x-dcb-tokens"] = theme
    defs["theme"] = th
  return {
      "$schema": raw.get("$schema"),
      "$id": catalog_id,
      "title": title or "Custom A2UI catalog",
      "description": (
          description or "Basic catalog subset plus design-system components."
      ),
      "catalogId": catalog_id,
      "components": components,
      "functions": raw.get("functions") or {},
      "$defs": defs,
  }
