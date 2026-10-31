# A2UI in A2UI Designer

A2UI Designer is built on the **A2UI v1.0** specification. It combines the unmodified upstream Basic catalog with its own extension catalog and per-project custom components.

## Upstream vs Ours

- **Verbatim upstream**: Files in `seeds/a2ui/` are exact copies from the upstream A2UI repository. The official renderer bundle in `web/vendor/a2ui.bundle.js` is built from unmodified `@a2ui/web_core@0.14.0` and `@a2ui/lit@0.14.0`.
- **Ours**: The host layer (`web/renderer/host.js`), which handles design tokens, inspection, and composition lowering. The extension catalog (`seeds/a2ui_designer_catalog.json`) and any project-specific catalogs.

## Catalog IDs

- **Basic catalog**: `https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json`
- **Extension catalog**: `https://posttool.github.io/a2ui-designer/catalogs/extensions/v1/catalog.json`
- **Project catalog**: Default is `https://posttool.github.io/a2ui-designer/catalogs/projects/<slug>/catalog.json` (configurable in Package settings).

## Mixed-Catalog Wire Format

A2UI Designer uses A2UI v1.0 mixed catalogs. The `createSurface` message specifies the Basic catalog, while extension or project components carry their own `catalogId`.

Example:
```json
{
  "version": "v1.0",
  "messages": [
    {
      "@call": "createSurface",
      "args": {
        "surfaceId": "main",
        "catalogId": "https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json",
        "components": [
          {
            "componentId": "hero",
            "catalogId": "https://posttool.github.io/a2ui-designer/catalogs/extensions/v1/catalog.json",
            "componentName": "HeroCard",
            "props": {
              "image": "https://example.com/image.jpg"
            }
          }
        ]
      }
    }
  ]
}
```

## How to Re-pin Upstream

To update to a newer upstream release (e.g., the final 1.0 release):

```bash
python3 tools/sync_a2ui.py --ref <tag-or-sha>
cd tools/renderer && npm install @a2ui/web_core@X @a2ui/lit@X && node build.mjs
cd ../..
python3 tools/build_starter_cache.py
```

## Extension Catalog Spec

The extension catalog provides components that are not in the Basic catalog but are useful for design tooling. It is off by default and enabled per project.

### `HeroCard`
- `child` (Child, required)
- `image` (DynamicString, required)
- `scrim` (enum: `auto|dark|light|none`, default `auto`)
- `fit` (enum: `cover|contain`, default `cover`)
- `weight`
- `accessibility`

*Replaces the old non-spec `Card.backgroundImage`.*

### `SvgIcon`
- `url` (DynamicString) or `svg` (string, inline SVG markup, sanitized). Exactly one is required.
- `size` (number 12–96, default 20)
- `accessibility`

*Replaces the old `Icon.name {url}/{svg}` and `Icon.size`.*
