// Drag-to-mark regions on a card screenshot. Boxes are reported in the card's
// CSS px (the 412-px-wide web render), regardless of how large the
// screenshot is displayed. Drawn boxes are outline-only; they tint on hover.
import {h} from './dom.js';

/**
 * regionSelect(host, {cardWidth = 412, onRegion([x, y, w, h]), enabled})
 * `host` must be position:relative and contain the displayed screenshot.
 * Returns {el, setBoxes([{box: [x,y,w,h], kind, label}]), setEnabled(bool),
 * destroy()}.
 */
export function regionSelect(host, opts = {}) {
  const cardWidth = opts.cardWidth || 412;
  let enabled = opts.enabled !== false;
  const layer = h('div.rs-layer', {class: enabled ? 'on' : ''});
  const boxesEl = h('div.rs-boxes');
  const draft = h('div.rs-draft', {hidden: true});
  layer.append(boxesEl, draft);
  host.appendChild(layer);

  const scale = () => (host.clientWidth || cardWidth) / cardWidth;
  let start = null;

  function point(e) {
    const r = layer.getBoundingClientRect();
    return {
      x: Math.max(0, Math.min(r.width, e.clientX - r.left)),
      y: Math.max(0, Math.min(r.height, e.clientY - r.top))
    };
  }
  function onDown(e) {
    if (!enabled || e.button !== 0) return;
    e.preventDefault();
    start = point(e);
    Object.assign(draft.style, {
      left: `${start.x}px`,
      top: `${start.y}px`,
      width: '0px',
      height: '0px'
    });
    draft.hidden = false;
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp, {once: true});
  }
  function onMove(e) {
    if (!start) return;
    const p = point(e);
    Object.assign(draft.style, {
      left: `${Math.min(start.x, p.x)}px`,
      top: `${Math.min(start.y, p.y)}px`,
      width: `${Math.abs(p.x - start.x)}px`,
      height: `${Math.abs(p.y - start.y)}px`
    });
  }
  function onUp(e) {
    window.removeEventListener('mousemove', onMove);
    if (!start) return;
    const p = point(e);
    const s = scale();
    const x = Math.min(start.x, p.x) / s, y = Math.min(start.y, p.y) / s;
    const w = Math.abs(p.x - start.x) / s, hh = Math.abs(p.y - start.y) / s;
    start = null;
    draft.hidden = true;
    if (w < 6 || hh < 6) return;
    opts.onRegion && opts.onRegion([x, y, w, hh].map((v) => Math.round(v)));
  }
  layer.addEventListener('mousedown', onDown);

  function setBoxes(list) {
    const s = scale();
    boxesEl.replaceChildren(...(list || []).map((b) => {
      const [x, y, w, hh] = b.box;
      return h(
          'div.rs-box', {
            class: b.kind || '',
            title: b.label || '',
            style: {
              left: `${x * s}px`,
              top: `${y * s}px`,
              width: `${w * s}px`,
              height: `${hh * s}px`
            }
          },
          b.label ? h('span.rs-label', b.label) : null);
    }));
  }
  function setEnabled(v) {
    enabled = !!v;
    layer.classList.toggle('on', enabled);
  }
  function destroy() {
    window.removeEventListener('mousemove', onMove);
    layer.remove();
  }
  return {el: layer, setBoxes, setEnabled, destroy};
}

/** Union of [x, y, w, h] boxes (null when empty). */
export function unionBox(boxes) {
  const bs = (boxes || []).filter((b) => b && b[2] > 0 && b[3] > 0);
  if (!bs.length) return null;
  const x0 = Math.min(...bs.map((b) => b[0]));
  const y0 = Math.min(...bs.map((b) => b[1]));
  const x1 = Math.max(...bs.map((b) => b[0] + b[2]));
  const y1 = Math.max(...bs.map((b) => b[1] + b[3]));
  return [x0, y0, x1 - x0, y1 - y0];
}

const imgCache = new Map();
function loadImage(url) {
  if (!imgCache.has(url)) {
    imgCache.set(url, new Promise((resolve, reject) => {
                   const img = new Image();
                   img.onload = () => resolve(img);
                   img.onerror = () => {
                     imgCache.delete(url);
                     reject(new Error('screenshot did not load'));
                   };
                   img.src = url;
                 }));
  }
  return imgCache.get(url);
}

/**
 * Client-side crop of the same-origin card screenshot (rendered at device
 * scale 2 of the 412-css-px card) to a CSS-px box, padded. Resolves to a PNG
 * data URL, or null when there is no screenshot / box.
 */
export async function cropScreenshot(
    url, box, {cardWidth = 412, pad = 8, maxSide = 640} = {}) {
  if (!url || !box) return null;
  try {
    const img = await loadImage(url);
    const s = img.naturalWidth / cardWidth;
    let [x, y, w, hh] = box;
    x = Math.max(0, x - pad);
    y = Math.max(0, y - pad);
    w = Math.min(cardWidth - x, w + pad * 2);
    hh = Math.min(img.naturalHeight / s - y, hh + pad * 2);
    if (w <= 0 || hh <= 0) return null;
    const sw = w * s, sh = hh * s;
    const k = Math.min(1, maxSide / Math.max(sw, sh));
    const c = document.createElement('canvas');
    c.width = Math.max(1, Math.round(sw * k));
    c.height = Math.max(1, Math.round(sh * k));
    const g = c.getContext('2d');
    g.fillStyle = '#fff';
    g.fillRect(0, 0, c.width, c.height);
    g.drawImage(img, x * s, y * s, sw, sh, 0, 0, c.width, c.height);
    return c.toDataURL('image/png');
  } catch (e) {
    console.warn('crop failed', e);
    return null;
  }
}
