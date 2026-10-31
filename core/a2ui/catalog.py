"""A2UI v1.0 catalogs: upstream Basic + opt-in extension + project components.

Three kinds of catalogs are mixed per the A2UI 1.0 rules:

1. the upstream Basic catalog, loaded VERBATIM from
   `seeds/a2ui/catalogs/basic/v1/catalog.json` (surface default catalogId);
2. the A2UI Designer extension catalog (`seeds/a2ui_designer_catalog.json`,
   HeroCard + SvgIcon), opted into per project via
   `settings.catalog.extensions: ["a2ui-designer"]`;
3. the project catalog: the design system's custom components, published
   under `settings.catalog.catalog_id`.

Every component instance from catalog 2 or 3 carries `"catalogId"` on the
wire (`core.a2ui.normalize` injects it by name).
"""

from __future__ import annotations

import copy
import dataclasses
import json
import os
from typing import Any

from core.a2ui import custom_web

ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
SEEDS = os.path.join(ROOT, "seeds")
A2UI_SEEDS = os.path.join(SEEDS, "a2ui")
BASIC_CATALOG_PATH = os.path.join(
    A2UI_SEEDS, "catalogs", "basic", "v1", "catalog.json"
)
BASIC_EXAMPLES_DIR = os.path.join(
    A2UI_SEEDS, "catalogs", "basic", "v1", "examples"
)
SPEC_JSON_DIR = os.path.join(A2UI_SEEDS, "specification", "v1_0", "json")
EXTENSION_CATALOG_PATH = os.path.join(SEEDS, "a2ui_designer_catalog.json")

PROTOCOL_VERSION = "v1.0"  # message envelope "version"
CATALOG_PROTOCOL_VERSION = "1.0"  # catalog "protocolVersion"
BASIC_CATALOG_ID = (
    "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json"
)
COMMON_TYPES_ID = "https://a2ui.org/specification/v1_0/common_types.json"
CATALOG_SCHEMA_ID = "https://json-schema.org/draft/2020-12/schema"
EXTENSION_KEY = "a2ui-designer"
EXTENSION_CATALOG_ID = (
    "https://posttool.github.io/a2ui-designer/catalogs/extensions/v1/"
    "catalog.json"
)
PROJECT_CATALOG_ID_TEMPLATE = (
    "https://posttool.github.io/a2ui-designer/catalogs/projects/{slug}/"
    "catalog.json"
)
DEFAULT_PROJECT_CATALOG_ID = PROJECT_CATALOG_ID_TEMPLATE.format(
    slug="default"
)
# Properties every component may carry (composed at the envelope level).
COMMON_PROPS = {
    "id",
    "catalogId",
    "accessibility",
    "weight",
    "component",
    "metadata",
}
WEIGHT_SCHEMA = {
    "type": "number",
    "description": (
        "The relative weight of this component within a Row or Column. This"
        " is similar to the CSS 'flex-grow' property. Note: this may ONLY be"
        " set when the component is a direct descendant of a Row or Column."
    ),
}
# common_types.json $defs a catalog may reference (spec "Restricted $ref").
ALLOWED_COMMON_REFS = frozenset({
    "ComponentId",
    "Child",
    "ChildList",
    "DynamicString",
    "DynamicNumber",
    "DynamicBoolean",
    "DynamicStringList",
    "DynamicValue",
    "AccessibilityAttributes",
    "CheckRule",
    "Checkable",
    "Action",
    "DataBinding",
    "FunctionCall",
})
CATALOG_TOP_KEYS = frozenset({
    "$schema",
    "$id",
    "protocolVersion",
    "title",
    "description",
    "catalogId",
    "instructions",
    "components",
    "functions",
    "$defs",
})


def project_catalog_id(slug: str) -> str:
  return PROJECT_CATALOG_ID_TEMPLATE.format(slug=slug or "default")


@dataclasses.dataclass
class PropSpec:
  name: str
  type: str
  required: bool = False
  enum: list[str] | None = None
  default: Any = None
  description: str = ""
  minimum: float | None = None
  maximum: float | None = None

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
  catalog_id: str = BASIC_CATALOG_ID
  extension: bool = False
  # Groups of props of which exactly one must be present (schema `oneOf`
  # of `required` lists, e.g. SvgIcon url|svg).
  one_of: list[list[str]] = dataclasses.field(default_factory=list)

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
        "extension": self.extension,
        "catalog_id": self.catalog_id,
        "usage": self.usage,
        "has_web": self.has_web,
        "has_composition": self.has_composition,
        "strategy": self.strategy,
        "targets": list(self.targets),
    }


@dataclasses.dataclass
class Catalog:
  """A project's view of the available components across catalogs."""

  catalog_id: str  # the project catalog id (custom components)
  components: dict[str, ComponentSpec]
  functions: dict[str, dict[str, Any]]
  disabled: list[str]
  # name → custom component Definition (web implementation + sample).
  custom_defs: dict[str, dict[str, Any]] = dataclasses.field(
      default_factory=dict
  )
  extensions: list[str] = dataclasses.field(default_factory=list)
  basic_catalog_id: str = BASIC_CATALOG_ID

  def enabled_components(self) -> dict[str, ComponentSpec]:
    return {k: v for k, v in self.components.items() if k not in self.disabled}

  @property
  def extension_enabled(self) -> bool:
    return EXTENSION_KEY in self.extensions

  def catalog_id_for(self, name: str) -> str | None:
    spec = self.components.get(name)
    return spec.catalog_id if spec else None

  def catalog_members(self) -> dict[str, set[str]]:
    """catalogId → component names resolvable through it.

    The project catalog also lists the Basic components because the exported
    project catalog (`to_a2ui_catalog_json`) is self-contained.
    """
    basic = {n for n, s in self.components.items() if not s.custom}
    basic -= {n for n, s in self.components.items() if s.extension}
    out: dict[str, set[str]] = {self.basic_catalog_id: set(basic)}
    ext = {n for n, s in self.components.items() if s.extension}
    if ext:
      out[EXTENSION_CATALOG_ID] = ext
    custom = {n for n, s in self.components.items() if s.custom}
    out.setdefault(self.catalog_id, set()).update(custom | basic)
    return out

  def function_catalogs(self) -> dict[str, dict[str, dict[str, Any]]]:
    """catalogId → functions resolvable through it."""
    out = {self.basic_catalog_id: self.functions}
    out.setdefault(self.catalog_id, self.functions)
    if self.extension_enabled:
      out[EXTENSION_CATALOG_ID] = {}
    return out

  def instructions(self) -> dict[str, str]:
    """catalogId → upstream/extension `instructions` text in prompt order."""
    out = {self.basic_catalog_id: basic_instructions()}
    if self.extension_enabled:
      out[EXTENSION_CATALOG_ID] = extension_instructions()
    return out


_CACHE: dict[str, Any] = {}


def _load_json(path: str) -> dict[str, Any]:
  if path not in _CACHE:
    with open(path, encoding="utf-8") as fh:
      _CACHE[path] = json.load(fh)
  return _CACHE[path]


def _load_basic() -> dict[str, Any]:
  return _load_json(BASIC_CATALOG_PATH)


def load_basic_catalog() -> dict[str, Any]:
  """The upstream Basic catalog JSON (verbatim; deep copy)."""
  return copy.deepcopy(_load_basic())


def load_extension_catalog() -> dict[str, Any]:
  """The A2UI Designer extension catalog JSON (deep copy)."""
  return copy.deepcopy(_load_json(EXTENSION_CATALOG_PATH))


def extension_catalog_json() -> dict[str, Any]:
  """The extension catalog as published (identical to the seed file)."""
  return load_extension_catalog()


def basic_instructions() -> str:
  return str(_load_basic().get("instructions") or "")


def extension_instructions() -> str:
  return str(_load_json(EXTENSION_CATALOG_PATH).get("instructions") or "")


def load_basic_examples() -> list[dict[str, Any]]:
  """Upstream v1 Basic catalog examples: [{file, name, description, messages}]."""
  if "examples" not in _CACHE:
    out = []
    for fn in sorted(os.listdir(BASIC_EXAMPLES_DIR)):
      if not fn.endswith(".json"):
        continue
      with open(os.path.join(BASIC_EXAMPLES_DIR, fn), encoding="utf-8") as fh:
        data = json.load(fh)
      out.append(dict(data, file=fn))
    _CACHE["examples"] = out
  return copy.deepcopy(_CACHE["examples"])


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
    name: str,
    schema: dict[str, Any],
    custom: bool = False,
    catalog_id: str = BASIC_CATALOG_ID,
    extension: bool = False,
) -> ComponentSpec:
  props: dict[str, PropSpec] = {}
  required: set[str] = set()
  description = schema.get("description", "")
  checkable = False
  one_of: list[list[str]] = []
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
      if pname in COMMON_PROPS or not isinstance(pschema, dict):
        continue
      if pname == "checks":
        checkable = True
        continue
      enum = pschema.get("enum")
      if not enum and isinstance(pschema.get("oneOf"), list):
        for o in pschema["oneOf"]:
          if isinstance(o, dict) and o.get("enum"):
            enum = o["enum"]
      props[pname] = PropSpec(
          name=pname,
          type=_type_label(pschema),
          enum=enum,
          default=pschema.get("default"),
          description=(pschema.get("description") or "").strip(),
          minimum=pschema.get("minimum"),
          maximum=pschema.get("maximum"),
      )
    required.update(part.get("required") or [])
    alts = part.get("oneOf")
    if isinstance(alts, list) and alts and all(
        isinstance(a, dict) and set(a) == {"required"} for a in alts
    ):
      one_of.append([r for a in alts for r in a["required"]])
  for r in required:
    if r in props:
      props[r].required = True
  if checkable:
    props["checks"] = PropSpec(
        name="checks",
        type="array<CheckRule>",
        description=(
            "Validation rules: [{condition: DataBinding | FunctionCall,"
            " message?}]"
        ),
    )
  return ComponentSpec(
      name=name,
      description=description,
      props=props,
      checkable=checkable,
      custom=custom,
      catalog_id=catalog_id,
      extension=extension,
      one_of=one_of,
  )


def basic_components() -> dict[str, ComponentSpec]:
  raw = _load_basic()
  return {
      name: _parse_component(name, schema)
      for name, schema in raw["components"].items()
  }


def extension_components() -> dict[str, ComponentSpec]:
  raw = _load_json(EXTENSION_CATALOG_PATH)
  return {
      name: _parse_component(
          name, schema, catalog_id=EXTENSION_CATALOG_ID, extension=True
      )
      for name, schema in raw["components"].items()
  }


def basic_functions() -> dict[str, dict[str, Any]]:
  return dict(_load_basic().get("functions") or {})


def basic_names() -> frozenset[str]:
  return frozenset(_load_basic()["components"])


def extension_names() -> frozenset[str]:
  return frozenset(_load_json(EXTENSION_CATALOG_PATH)["components"])


def reserved_names() -> frozenset[str]:
  """Names custom components may not use (Basic + extension + Surface)."""
  return basic_names() | extension_names() | {"Surface"}


def build_catalog(
    settings_catalog: dict[str, Any] | None,
    custom_defs: dict[str, dict[str, Any]] | None = None,
) -> Catalog:
  """Basic catalog + project toggles + optional extension + custom components.

  `settings_catalog.custom_components` may hold legacy raw props schemas or
  (in run snapshots) full Definitions; `custom_defs` (active rows of the
  custom_components table) wins on name clashes.
  """
  cfg = settings_catalog or {}
  comps = basic_components()
  extensions = [
      e for e in cfg.get("extensions") or [] if e == EXTENSION_KEY
  ]
  if EXTENSION_KEY in extensions:
    comps.update(extension_components())
  reserved = reserved_names()
  project_id = str(cfg.get("catalog_id") or "").strip()
  if not project_id or project_id in (BASIC_CATALOG_ID, EXTENSION_CATALOG_ID):
    project_id = DEFAULT_PROJECT_CATALOG_ID
  defs: dict[str, dict[str, Any]] = {}
  merged_custom = dict(cfg.get("custom_components") or {})
  merged_custom.update(custom_defs or {})
  for name, schema in merged_custom.items():
    if not isinstance(schema, dict) or not name or name in reserved:
      continue
    if custom_web.is_definition(schema):
      spec = _parse_component(
          name, schema.get("props") or {}, custom=True, catalog_id=project_id
      )
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
      defs[name] = schema
    else:
      spec = _parse_component(
          name, schema, custom=True, catalog_id=project_id
      )
    comps[name] = spec
  disabled = [d for d in cfg.get("disabled_components") or [] if d in comps]
  return Catalog(
      catalog_id=project_id,
      components=comps,
      functions=basic_functions(),
      disabled=disabled,
      custom_defs=defs,
      extensions=extensions,
  )


def catalog_from_json(cat_json: dict[str, Any]) -> Catalog:
  """A Catalog for an arbitrary v1.0 catalog document (e.g. a test catalog).

  The document's catalogId plays the role of the "basic" (surface default)
  catalog; there are no project/custom components.
  """
  cid = str(cat_json.get("catalogId") or cat_json.get("$id") or "")
  comps = {
      name: _parse_component(name, schema, catalog_id=cid)
      for name, schema in (cat_json.get("components") or {}).items()
  }
  return Catalog(
      catalog_id=cid,
      components=comps,
      functions=dict(cat_json.get("functions") or {}),
      disabled=[],
      basic_catalog_id=cid,
  )


def _relative_refs(schema: Any) -> Any:
  """Rewrites common_types refs to the relative v1.0 form.

  Accepts legacy absolute (`https://a2ui.org/specification/v0_9/...`) and
  `#/$defs/X` shorthand refs from older Definitions.
  """
  if isinstance(schema, dict):
    out = {}
    for k, v in schema.items():
      if k == "$ref" and isinstance(v, str):
        if "common_types.json#" in v:
          v = "common_types.json#" + v.split("common_types.json#", 1)[1]
        elif v.startswith("#/$defs/"):
          v = "common_types.json" + v
      out[k] = _relative_refs(v)
    return out
  if isinstance(schema, list):
    return [_relative_refs(x) for x in schema]
  return schema


def custom_component_schema(
    name: str, spec: ComponentSpec, defn: dict[str, Any] | None
) -> dict[str, Any]:
  """A v1.0 catalog component schema for a custom component."""
  props_schema = (defn or {}).get("props") or {}
  raw_props = props_schema.get("properties") or {}
  props = dict(_relative_refs(raw_props))
  for k in list(props):
    if k in COMMON_PROPS:
      props.pop(k)
  props = {"component": {"const": name}, **props, "weight": dict(WEIGHT_SCHEMA)}
  required = ["component"] + [
      r for r in props_schema.get("required") or [] if r in props
  ]
  out: dict[str, Any] = {"type": "object"}
  if spec.description:
    out["description"] = spec.description
  out["properties"] = props
  out["required"] = required
  return out


def _custom_instructions(catalog: Catalog, names: list[str]) -> str:
  lines = []
  for n in names:
    spec = catalog.components[n]
    if spec.usage:
      lines.append(f"- {n}: {' '.join(spec.usage.split())}")
  if not lines:
    return ""
  return (
      "## Design-system components\n\nInstances of these components carry"
      f' "catalogId": "{catalog.catalog_id}". Prefer them over assembling'
      " the same thing from Basic components when their usage fits.\n\n"
      + "\n".join(lines)
  )


def _project_rules(catalog: Catalog) -> str:
  if not catalog.disabled:
    return ""
  return (
      "## Project rules\n\nDo not use these components in this project: "
      + ", ".join(sorted(catalog.disabled))
      + "."
  )


def _defs_for(components: dict[str, Any], functions: dict[str, Any]):
  defs: dict[str, Any] = {
      "anyComponent": {
          "oneOf": [{"$ref": f"#/components/{n}"} for n in components],
          "discriminator": {"propertyName": "component"},
      }
  }
  if functions:
    defs["anyFunction"] = {
        "oneOf": [{"$ref": f"#/functions/{n}"} for n in functions]
    }
  else:
    defs["anyFunction"] = {
        "description": "This catalog defines no functions.",
        "not": {},
    }
  return defs


def to_a2ui_catalog_json(
    catalog: Catalog,
    catalog_id: str,
    title: str = "",
    description: str = "",
    theme: dict[str, Any] | None = None,  # pylint: disable=unused-argument
    include_basic: bool = True,
) -> dict[str, Any]:
  """Spec-shaped A2UI v1.0 catalog for the project.

  With `include_basic` (default) the catalog is self-contained: enabled Basic
  components and functions (verbatim from upstream) plus the custom
  components, so it can serve as a surface default catalog. With
  `include_basic=False` it holds only the custom components (what the wire
  references through per-component `catalogId`). Extension components live
  in their own catalog (`extension_catalog_json`). `theme` is accepted for
  backwards compatibility and ignored: v1.0 catalogs carry no theme.
  """
  raw = _load_basic()
  enabled = catalog.enabled_components()
  components: dict[str, Any] = {}
  custom_names = []
  for name, spec in enabled.items():
    if spec.extension:
      continue
    if not spec.custom:
      if include_basic and name in raw["components"]:
        components[name] = copy.deepcopy(raw["components"][name])
      continue
    custom_names.append(name)
    components[name] = custom_component_schema(
        name, spec, catalog.custom_defs.get(name)
    )
  functions = (
      copy.deepcopy(raw.get("functions") or {}) if include_basic else {}
  )
  parts = []
  if include_basic and raw.get("instructions"):
    parts.append(str(raw["instructions"]).strip())
  for p in (_project_rules(catalog), _custom_instructions(catalog, custom_names)):
    if p:
      parts.append(p)
  out: dict[str, Any] = {
      "$schema": raw.get("$schema") or CATALOG_SCHEMA_ID,
      "$id": catalog_id,
      "protocolVersion": CATALOG_PROTOCOL_VERSION,
      "title": title or "Custom A2UI catalog",
      "description": description
      or (
          "Basic catalog subset plus design-system components."
          if include_basic
          else "Design-system components."
      ),
      "catalogId": catalog_id,
  }
  if parts:
    out["instructions"] = "\n\n".join(parts)
  out["components"] = components
  out["functions"] = functions
  out["$defs"] = _defs_for(components, functions)
  return out


def project_catalog_json(
    catalog: Catalog, title: str = "", description: str = ""
) -> dict[str, Any]:
  """The project catalog as referenced on the wire (custom components only)."""
  return to_a2ui_catalog_json(
      catalog,
      catalog.catalog_id,
      title=title,
      description=description,
      include_basic=False,
  )


def catalog_shape_issues(cat: dict[str, Any]) -> list[str]:
  """Checks a catalog against the v1.0 catalog_definition structural rules."""
  issues: list[str] = []
  extra = set(cat) - CATALOG_TOP_KEYS
  if extra:
    issues.append(f"unexpected top-level keys {sorted(extra)}")
  if not isinstance(cat.get("catalogId"), str) or not cat.get("catalogId"):
    issues.append("catalogId is required")
  if cat.get("$id") and cat.get("$id") != cat.get("catalogId"):
    issues.append("$id and catalogId should be equal")
  if cat.get("protocolVersion") != CATALOG_PROTOCOL_VERSION:
    issues.append('protocolVersion must be "1.0"')
  defs = cat.get("$defs") or {}
  if set(defs) != {"anyComponent", "anyFunction"}:
    issues.append(
        f"$defs must hold exactly anyComponent and anyFunction (got"
        f" {sorted(defs)})"
    )
  comps = cat.get("components") or {}
  funcs = cat.get("functions") or {}
  from core.a2ui import validator as _v  # pylint: disable=g-import-not-at-top

  for name, schema in comps.items():
    if name == "Surface":
      issues.append("component name 'Surface' is reserved")
    if not _v.is_identifier(name):
      issues.append(f"component name '{name}' is not a UAX #31 identifier")
    props = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
    parts = schema.get("allOf") or [] if isinstance(schema, dict) else []
    for p in parts:
      if isinstance(p, dict) and p.get("properties"):
        props = dict(props, **p["properties"])
      if isinstance(p, dict) and str(p.get("$ref", "")).endswith(
          "ComponentCommon"
      ):
        issues.append(f"{name}: must not wrap ComponentCommon via allOf")
    if (props.get("component") or {}).get("const") != name:
      issues.append(f"{name}: needs properties.component.const == '{name}'")
    if isinstance(schema, dict) and "unevaluatedProperties" in schema:
      issues.append(f"{name}: unevaluatedProperties is not allowed")
    for pname in props:
      if not _v.is_identifier(pname):
        issues.append(f"{name}.{pname}: not a UAX #31 identifier")
  for name, schema in funcs.items():
    if name.startswith("@") or not _v.is_identifier(name):
      issues.append(f"function name '{name}' is not a UAX #31 identifier")
    if not isinstance(schema, dict) or "returnType" not in schema:
      issues.append(f"function {name}: returnType is required")

  def walk(v: Any, path: str) -> None:
    if isinstance(v, dict):
      for k, x in v.items():
        if k == "$ref" and isinstance(x, str):
          if x.startswith("#/"):
            if not x.startswith(("#/components/", "#/functions/")):
              issues.append(f"{path}: local $ref '{x}' not allowed")
          elif x.startswith("common_types.json#/$defs/"):
            target = x.rsplit("/", 1)[-1]
            if target not in ALLOWED_COMMON_REFS:
              issues.append(f"{path}: $ref to '{target}' not allowed")
          elif x != CATALOG_SCHEMA_ID:
            issues.append(f"{path}: external $ref '{x}' must be relative")
        walk(x, f"{path}/{k}")
    elif isinstance(v, list):
      for i, x in enumerate(v):
        walk(x, f"{path}/{i}")

  walk(comps, "/components")
  walk(funcs, "/functions")
  return issues
