// A2UI v0.9 renderer core: JSON pointers, scopes, data model, message
// processing and template expansion. DOM-free so node can import it.

export const BASIC_COMPONENTS = new Set([
  'Text',
  'Image',
  'Icon',
  'Video',
  'AudioPlayer',
  'Row',
  'Column',
  'List',
  'Card',
  'Tabs',
  'Modal',
  'Divider',
  'Button',
  'TextField',
  'CheckBox',
  'ChoicePicker',
  'Slider',
  'DateTimeInput',
]);

// ---------------------------------------------------------------------------
// JSON Pointer (RFC 6901) + A2UI relative paths.

/** Splits an absolute pointer into unescaped segments. "" or "/" -> []. */
export function parsePointer(pointer) {
  if (pointer == null || pointer === '' || pointer === '/') return [];
  const s = pointer.startsWith('/') ? pointer.slice(1) : pointer;
  return s.split('/').map((p) => p.replace(/~1/g, '/').replace(/~0/g, '~'));
}

/** Joins segments back into an absolute pointer. [] -> "". */
export function joinPointer(segments) {
  if (!segments.length) return '';
  return '/' +
      segments.map((s) => String(s).replace(/~/g, '~0').replace(/\//g, '~1'))
          .join('/');
}

/**
 * Resolves `path` against a scope base pointer (e.g. "/items/0").
 * Absolute paths (leading "/") ignore the scope. Relative paths are appended
 * to the scope; "." / "./x" / "../x" are tolerated.
 */
export function resolvePath(scope, path) {
  if (path == null) return scope || '';
  path = String(path).trim();
  if (path.startsWith('/')) return path === '/' ? '' : path;
  const segs = parsePointer(scope || '');
  for (const part of path.split('/')) {
    if (part === '' || part === '.') continue;
    if (part === '..') {
      segs.pop();
      continue;
    }
    segs.push(part.replace(/~1/g, '/').replace(/~0/g, '~'));
  }
  return joinPointer(segs);
}

export function getAt(root, pointer) {
  let cur = root;
  for (const seg of parsePointer(pointer)) {
    if (cur == null || typeof cur !== 'object') return undefined;
    if (Array.isArray(cur)) {
      if (!/^\d+$/.test(seg)) return undefined;
      cur = cur[Number(seg)];
    } else {
      cur =
          Object.prototype.hasOwnProperty.call(cur, seg) ? cur[seg] : undefined;
    }
  }
  return cur;
}

/**
 * Returns a new root with `value` set at `pointer` (creating containers as
 * needed; numeric segments create arrays). value === undefined deletes the key
 * (array slots are set to undefined, preserving length, per spec).
 * Mutates in place where possible; the returned root must be used.
 */
export function setAt(root, pointer, value) {
  const segs = parsePointer(pointer);
  if (!segs.length) return value === undefined ? {} : value;
  if (root == null || typeof root !== 'object')
    root = /^\d+$/.test(segs[0]) ? [] : {};
  let cur = root;
  for (let i = 0; i < segs.length - 1; i++) {
    const seg = segs[i];
    const key = Array.isArray(cur) ? Number(seg) : seg;
    let next = cur[key];
    if (next == null || typeof next !== 'object') {
      if (value === undefined) return root;  // nothing to delete
      next = /^\d+$/.test(segs[i + 1]) ? [] : {};
      cur[key] = next;
    }
    cur = next;
  }
  const last = segs[segs.length - 1];
  if (Array.isArray(cur)) {
    const idx = last === '-' ? cur.length : Number(last);
    if (Number.isNaN(idx)) return root;
    cur[idx] = value;
  } else if (value === undefined) {
    delete cur[last];
  } else {
    cur[last] = value;
  }
  return root;
}

export function deleteAt(root, pointer) {
  return setAt(root, pointer, undefined);
}

// ---------------------------------------------------------------------------
// Surface store: processes server_to_client messages.

export class SurfaceState {
  constructor(surfaceId) {
    this.surfaceId = surfaceId;
    this.catalogId = null;
    this.theme = {};
    this.sendDataModel = false;
    this.created = false;
    this.components = new Map();
    this.data = {};
  }
  getData(pointer) {
    return getAt(this.data, pointer);
  }
  setData(pointer, value) {
    this.data = setAt(this.data, pointer, value);
  }
  get hasRoot() {
    return this.components.has('root');
  }
}

/**
 * Holds every surface. `process(msgs)` returns a change summary
 * {structure:Set<surfaceId>, data:Set<surfaceId>, deleted:Set<surfaceId>}.
 * Messages for unknown surfaces implicitly create (buffer) the surface so
 * out-of-order streams still render once `root` exists.
 */
export class SurfaceStore {
  constructor() {
    this.surfaces = new Map();
    this.errors = [];
    this.warnings = [];
  }

  _surface(id, implicitFrom) {
    let s = this.surfaces.get(id);
    if (!s) {
      s = new SurfaceState(id);
      this.surfaces.set(id, s);
      if (implicitFrom)
        this.warnings.push(`${implicitFrom} before createSurface for "${id}"`);
    }
    return s;
  }

  process(messages) {
    const changes = {structure: new Set(), data: new Set(), deleted: new Set()};
    if (!Array.isArray(messages)) messages = messages ? [messages] : [];
    for (const msg of messages) {
      try {
        this._one(msg, changes);
      } catch (e) {
        this.errors.push(`message error: ${e && e.message || e}`);
      }
    }
    return changes;
  }

  _one(msg, changes) {
    if (!msg || typeof msg !== 'object') {
      this.errors.push('ignored non-object message');
      return;
    }
    if (msg.createSurface) {
      const m = msg.createSurface;
      const id = m.surfaceId || 'default';
      const s = this._surface(id);
      s.created = true;
      s.catalogId = m.catalogId || null;
      s.theme = m.theme && typeof m.theme === 'object' ? m.theme : {};
      s.sendDataModel = !!m.sendDataModel;
      changes.structure.add(id);
    } else if (msg.updateComponents) {
      const m = msg.updateComponents;
      const id = m.surfaceId || 'default';
      const s = this._surface(id, 'updateComponents');
      for (const c of m.components || []) {
        if (!c || typeof c !== 'object' || typeof c.id !== 'string') {
          this.errors.push(`component without id in "${id}"`);
          continue;
        }
        s.components.set(c.id, c);
      }
      changes.structure.add(id);
    } else if (msg.updateDataModel) {
      const m = msg.updateDataModel;
      const id = m.surfaceId || 'default';
      const s = this._surface(id, 'updateDataModel');
      const path = m.path == null ? '' : m.path;
      if (!('value' in m)) {
        if (path === '' || path === '/')
          s.data = {};
        else
          s.data = deleteAt(s.data, path);
      } else {
        s.data = setAt(s.data, path, structuredCloneSafe(m.value));
      }
      changes.data.add(id);
    } else if (msg.deleteSurface) {
      const id = msg.deleteSurface.surfaceId;
      if (this.surfaces.delete(id)) changes.deleted.add(id);
    } else {
      const keys = Object.keys(msg).filter((k) => k !== 'version');
      this.errors.push(`unknown message type: ${keys.join(',') || '(empty)'}`);
    }
  }
}

function structuredCloneSafe(v) {
  if (v === undefined) return v;
  try {
    return JSON.parse(JSON.stringify(v));
  } catch {
    return v;
  }
}

// ---------------------------------------------------------------------------
// Tree structure: child references & template expansion.

/**
 * Lists child slots of a component: [{role, ref}] where ref is either a
 * component id string or a template {componentId, path}.
 */
export function childRefs(comp) {
  if (!comp) return [];
  const out = [];
  switch (comp.component) {
    case 'Row':
    case 'Column':
    case 'List':
      if (comp.children != null)
        out.push({role: 'children', ref: comp.children});
      break;
    case 'Card':
    case 'Button':
      if (comp.child) out.push({role: 'child', ref: comp.child});
      break;
    case 'Tabs':
      for (const t of Array.isArray(comp.tabs) ? comp.tabs : []) {
        if (t && t.child) out.push({role: 'tab', ref: t.child});
      }
      break;
    case 'Modal':
      if (comp.trigger) out.push({role: 'trigger', ref: comp.trigger});
      if (comp.content) out.push({role: 'content', ref: comp.content});
      break;
    default:
      if (!BASIC_COMPONENTS.has(comp.component)) {
        if (comp.children != null)
          out.push({role: 'children', ref: comp.children});
        if (typeof comp.child === 'string')
          out.push({role: 'child', ref: comp.child});
      }
  }
  return out;
}

/**
 * Expands a ChildList (array of ids or a template) into [{id, scope}].
 * Templates iterate arrays (scope "<path>/<i>") or objects (scope
 * "<path>/<key>").
 */
export function expandChildList(ref, scope, getData) {
  if (ref == null) return [];
  if (typeof ref === 'string') return [{id: ref, scope}];
  if (Array.isArray(ref)) {
    return ref.filter((x) => typeof x === 'string').map((id) => ({id, scope}));
  }
  if (typeof ref === 'object' && typeof ref.componentId === 'string') {
    const base = resolvePath(scope, ref.path || '');
    const list = getData(base);
    const out = [];
    if (Array.isArray(list)) {
      list.forEach(
          (_, i) =>
              out.push({id: ref.componentId, scope: `${base}/${i}`, index: i}));
    } else if (list && typeof list === 'object') {
      Object.keys(list).forEach((k, i) => out.push({
        id: ref.componentId,
        scope: `${base}/${k.replace(/~/g, '~0').replace(/\//g, '~1')}`,
        index: i,
      }));
    }
    return out;
  }
  return [];
}

/** Signature of a template's data shape (to detect structural changes). */
export function templateSignature(ref, scope, getData) {
  if (!ref || typeof ref !== 'object' || Array.isArray(ref)) return '';
  const list = getData(resolvePath(scope, ref.path || ''));
  if (Array.isArray(list)) return `a${list.length}`;
  if (list && typeof list === 'object')
    return `o${Object.keys(list).join('\u0001')}`;
  return 'n';
}

/**
 * Walks a surface from "root" and returns the flattened instance list
 * [{id, scope, component, depth, parent}], plus missing references. DOM-free;
 * used by tests and for meta.
 */
export function walkSurface(surface, {maxDepth = 64} = {}) {
  const nodes = [];
  const missing = [];
  const getData = (p) => surface.getData(p);
  const visit = (id, scope, depth, parent, stack) => {
    const comp = surface.components.get(id);
    if (!comp) {
      missing.push({id, parent});
      return;
    }
    const key = `${id}|${scope}`;
    if (stack.has(key) || depth > maxDepth) return;
    nodes.push({id, scope, component: comp.component, depth, parent});
    stack.add(key);
    for (const {ref} of childRefs(comp)) {
      for (const ch of expandChildList(ref, scope, getData)) {
        visit(ch.id, ch.scope, depth + 1, id, stack);
      }
    }
    stack.delete(key);
  };
  if (surface.components.has('root')) visit('root', '', 0, null, new Set());
  return {nodes, missing};
}
