// Client-side lowering of composite custom components into Basic Catalog
// components. JS port of core/a2ui/lowering.py `expand_composite_components`
// (keep the two in sync): a Definition with `composition: {root, components}`
// (strategy `basic_composition`, or no web template) is rewritten, per
// instance, into Basic nodes before the messages reach the official
// MessageProcessor. Instance ids: the composition root takes the instance id,
// every other node `<instance>__<node id>`.

const SINGLE_BIND_RE = /^\{\{\s*([A-Za-z_]\w*(?:\.\w+)*)\s*\}\}$/;
const INLINE_BIND_RE = /\{\{\s*([A-Za-z_]\w*(?:\.\w+)*)\s*\}\}/g;
const CHILD_ID_FIELDS = new Set(['child', 'content', 'trigger']);

const clone = (v) => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
const isObj = (v) => !!v && typeof v === 'object' && !Array.isArray(v);

/** True when `defn` carries a non-empty Basic composition tree. */
export function hasComposition(defn) {
  if (!isObj(defn) || !isObj(defn.composition)) return false;
  const c = defn.composition;
  return !!String(c.root || '').trim() && Array.isArray(c.components) &&
      c.components.length > 0;
}

/** True if `defn` renders by lowering into Basic components (not a template). */
export function shouldLower(defn) {
  if (!hasComposition(defn)) return false;
  const strategy = String(defn.strategy || '').trim().toLowerCase();
  const hasWeb = !!String((defn.web && defn.web.template) || '').trim();
  return strategy === 'basic_composition' || !hasWeb;
}

function lookupExpr(props, expr) {
  let cur = props;
  for (const part of expr.split('.')) {
    if (isObj(cur) && Object.prototype.hasOwnProperty.call(cur, part)) {
      cur = cur[part];
    } else {
      return null;
    }
  }
  return cur === undefined ? null : cur;
}

function defaultProps(defn) {
  const out = {};
  const props = (defn.props && defn.props.properties) || {};
  if (!isObj(props)) return out;
  for (const [k, s] of Object.entries(props)) {
    if (isObj(s) && 'default' in s) out[k] = clone(s.default);
  }
  return out;
}

const isBinding = (v) => isObj(v) && ('@path' in v || 'path' in v);

function interpolateValue(val, props) {
  if (typeof val === 'string') {
    const m = SINGLE_BIND_RE.exec(val.trim());
    if (m) {
      const r = lookupExpr(props, m[1]);
      return r != null ? clone(r) : '';
    }
    if (INLINE_BIND_RE.test(val)) {
      INLINE_BIND_RE.lastIndex = 0;
      return val.replace(INLINE_BIND_RE, (_, expr) => {
        const v = lookupExpr(props, expr);
        if (v == null || isBinding(v)) return '';
        return typeof v === 'object' ? JSON.stringify(v) : String(v);
      });
    }
    return val;
  }
  if (Array.isArray(val)) return val.map((x) => interpolateValue(x, props));
  if (isObj(val)) {
    const o = {};
    for (const [k, v] of Object.entries(val)) o[k] = interpolateValue(v, props);
    return o;
  }
  return clone(val);
}

function resolveChildId(raw, idMap, props) {
  if (typeof raw !== 'string') return null;
  const s = raw.trim();
  if (!s) return null;
  const m = SINGLE_BIND_RE.exec(s);
  if (m) {
    const v = lookupExpr(props, m[1]);
    return typeof v === 'string' && v.trim() ? v.trim() : null;
  }
  return idMap[s] ?? s;
}

function resolveChildrenList(raw, idMap, props) {
  const fromValue = (v) => {
    if (Array.isArray(v)) {
      return v.filter((x) => typeof x === 'string' && x.trim()).map((x) => x.trim());
    }
    if (typeof v === 'string' && v.trim()) return [v.trim()];
    return [];
  };
  if (typeof raw === 'string') {
    const m = SINGLE_BIND_RE.exec(raw.trim());
    if (m) return fromValue(lookupExpr(props, m[1]));
    return [idMap[raw.trim()] ?? raw.trim()];
  }
  if (Array.isArray(raw)) {
    const out = [];
    for (const item of raw) {
      if (typeof item !== 'string' || !item.trim()) continue;
      const s = item.trim();
      const m = SINGLE_BIND_RE.exec(s);
      if (m) out.push(...fromValue(lookupExpr(props, m[1])));
      else out.push(idMap[s] ?? s);
    }
    return out;
  }
  if (isObj(raw)) {
    const o = clone(raw);
    if (typeof o.componentId === 'string') {
      const r = resolveChildId(o.componentId, idMap, props);
      if (r) o.componentId = r;
    }
    return o;
  }
  return [];
}

function expandInstance(inst, defn) {
  const spec = defn.composition || {};
  let root = String(spec.root || '').trim();
  const nodes = (spec.components || []).filter(isObj);
  if (!root || !nodes.length) return [inst];
  const instId = String(inst.id || 'root');
  const props = defaultProps(defn);
  for (const [k, v] of Object.entries(inst)) {
    if (!['id', 'component', 'weight', 'accessibility', 'catalogId'].includes(k)) {
      props[k] = clone(v);
    }
  }
  const internal = new Set(nodes.map((n) => (typeof n.id === 'string' ? n.id.trim() : ''))
                               .filter(Boolean));
  if (!internal.has(root)) root = String(nodes[0].id || 'root');
  const idMap = {};
  for (const cid of internal) idMap[cid] = cid === root ? instId : `${instId}__${cid}`;

  const expanded = [];
  const extra = [];
  for (const node of nodes) {
    const rawId = String(node.id || '').trim();
    if (!rawId) continue;
    const isRoot = rawId === root;
    const newId = idMap[rawId];
    const cname = String(node.component || 'Column').trim();
    const out = {id: newId, component: cname};
    if (isRoot) {
      if ('weight' in inst) out.weight = clone(inst.weight);
      if ('accessibility' in inst) out.accessibility = clone(inst.accessibility);
    }
    for (const [k, v] of Object.entries(node)) {
      if (k === 'id' || k === 'component') continue;
      if (CHILD_ID_FIELDS.has(k)) {
        const rc = resolveChildId(v, idMap, props);
        if (rc) {
          out[k] = rc;
        } else if (cname === 'Card' && k === 'child') {
          const ph = `${newId}__empty`;
          out.child = ph;
          extra.push({id: ph, component: 'Column', children: []});
        }
      } else if (k === 'children') {
        out.children = resolveChildrenList(v, idMap, props);
      } else if (k === 'tabs' && cname === 'Tabs' && Array.isArray(v)) {
        out.tabs = v.filter(isObj).map((tab) => {
          const t = interpolateValue(tab, props);
          if (typeof tab.child === 'string') {
            const rc = resolveChildId(tab.child, idMap, props);
            if (rc) t.child = rc;
          }
          return t;
        });
      } else {
        const iv = interpolateValue(v, props);
        if (iv === '' && ['icon', 'url', 'variant', 'fit', 'justify', 'align'].includes(k)) continue;
        if (k === 'action' && !isObj(iv)) continue;
        out[k] = iv;
      }
    }
    if (cname === 'Button' && !('action' in out)) {
      out.action = {event: {name: `${instId}_tap`}};
    }
    if (cname === 'Button' && !('child' in out)) {
      const lbl = `${newId}__label`;
      out.child = lbl;
      const label = 'label' in out ? out.label : 'Action';
      delete out.label;
      extra.push({id: lbl, component: 'Text', text: String(label)});
    }
    expanded.push(out);
  }
  return expanded.concat(extra);
}

/**
 * Expands composite custom component instances in `updateComponents`
 * messages (and inline `createSurface.components`). Returns new messages;
 * the input is not mutated.
 */
export function expandCompositeComponents(messages, customDefs, {maxDepth = 3} = {}) {
  if (!Array.isArray(messages) || !isObj(customDefs)) return messages;
  const lowerable = {};
  for (const [name, defn] of Object.entries(customDefs)) {
    if (shouldLower(defn)) lowerable[name] = defn;
  }
  if (!Object.keys(lowerable).length) return messages;
  const expandList = (comps) => {
    let cur = comps;
    for (let i = 0; i < maxDepth; i++) {
      let changed = false;
      const next = [];
      for (const c of cur) {
        if (!isObj(c)) continue;
        const name = String(c.component || '');
        if (Object.prototype.hasOwnProperty.call(lowerable, name)) {
          next.push(...expandInstance(c, lowerable[name]));
          changed = true;
        } else {
          next.push(c);
        }
      }
      cur = next;
      if (!changed) break;
    }
    return cur;
  };
  return messages.map((msg) => {
    if (!isObj(msg)) return msg;
    const out = clone(msg);
    if (isObj(out.updateComponents) && Array.isArray(out.updateComponents.components)) {
      out.updateComponents.components = expandList(out.updateComponents.components);
    }
    if (isObj(out.createSurface) && Array.isArray(out.createSurface.components)) {
      out.createSurface.components = expandList(out.createSurface.components);
    }
    return out;
  });
}
