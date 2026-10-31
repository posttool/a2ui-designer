// Renderer logic checks (DOM-free). Run from the package root:
//   node tests/renderer_check.mjs
// Exits non-zero on failure.

import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const R = (p) => new URL(`file://${path.join(ROOT, 'web/renderer', p)}`).href;

const model = await import(R('model.js'));
const fns = await import(R('functions.js'));
const md = await import(R('markdown.js'));
const icons = await import(R('icons.js'));

let passed = 0;
const failures = [];
function check(name, cond, detail = '') {
  if (cond) passed++;
  else failures.push(`${name}${detail ? ` — ${detail}` : ''}`);
}
function eq(name, got, want) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  check(name, g === w, `got ${g}, want ${w}`);
}

// ---- JSON pointer ----------------------------------------------------------
{
  const {parsePointer, getAt, setAt, deleteAt, resolvePath, joinPointer} = model;
  eq('parse root', parsePointer(''), []);
  eq('parse slash', parsePointer('/'), []);
  eq('parse escapes', parsePointer('/a~1b/c~0d'), ['a/b', 'c~d']);
  eq('join escapes', joinPointer(['a/b', 'c~d']), '/a~1b/c~0d');
  const data = {user: {name: 'Ada', tags: ['x', 'y']}};
  eq('get nested', getAt(data, '/user/name'), 'Ada');
  eq('get array', getAt(data, '/user/tags/1'), 'y');
  eq('get missing', getAt(data, '/user/nope/deep'), undefined);
  eq('get non-numeric array idx', getAt(data, '/user/tags/foo'), undefined);
  let d = setAt({}, '/a/b/c', 1);
  eq('set creates objects', d, {a: {b: {c: 1}}});
  d = setAt({}, '/list/0/name', 'n');
  eq('set creates arrays', d, {list: [{name: 'n'}]});
  d = setAt({a: 1}, '', {b: 2});
  eq('set root replaces', d, {b: 2});
  d = deleteAt({a: {b: 1, c: 2}}, '/a/b');
  eq('delete key', d, {a: {c: 2}});
  d = deleteAt({a: [1, 2, 3]}, '/a/1');
  check('delete array slot preserves length', d.a.length === 3 && d.a[1] === undefined);
  d = setAt({a: [1]}, '/a/-', 2);
  eq('append with -', d, {a: [1, 2]});
  eq('resolve absolute', resolvePath('/items/0', '/title'), '/title');
  eq('resolve relative', resolvePath('/items/0', 'name'), '/items/0/name');
  eq('resolve relative nested', resolvePath('/items/0', 'a/b'), '/items/0/a/b');
  eq('resolve relative root scope', resolvePath('', 'name'), '/name');
  eq('resolve dot', resolvePath('/items/2', '.'), '/items/2');
  eq('resolve dotdot', resolvePath('/items/2', '../0/x'), '/items/0/x');
}

// ---- functions -------------------------------------------------------------
const mkctx = (data, scope = '') => ({
  getData: (p) => model.getAt(data, p), scope, locale: 'en-US', errors: [],
});
{
  const data = {
    user: {first: 'Ada', last: 'Lovelace'}, count: 1, n: 1234567.891, price: 1299.5,
    items: [{name: 'Apple', qty: 3}, {name: 'Pear', qty: 0}], now: '2026-01-16T14:30:00',
    reviewCount: 2847, email: 'a@b.co', zip: '1234', agree: true, empty: '', list: [],
  };
  const ctx = mkctx(data);
  const fs_ = (s, c = ctx) => fns.formatString(s, c);
  eq('formatString abs', fs_('Hello, ${/user/first} ${/user/last}!'), 'Hello, Ada Lovelace!');
  eq('formatString relative', fs_('${name} x${qty}', mkctx(data, '/items/0')), 'Apple x3');
  eq('formatString escape', fs_('cost \\${/count}'), 'cost ${/count}');
  eq('formatString missing -> empty', fs_('[${/nope}]'), '[]');
  eq('formatString object -> JSON', fs_('${/user}'), '{"first":"Ada","last":"Lovelace"}');
  eq('formatString nested fn', fs_('(${formatNumber(value: ${/reviewCount})} ${pluralize(value: ${/reviewCount}, one: \'review\', other: \'reviews\')})'),
      '(2,847 reviews)');
  eq('formatString date fn', fs_('Today is ${formatDate(value: ${/now}, format: \'EEEE, MMMM d\')}.'),
      'Today is Friday, January 16.');
  eq('formatString currency fn', fs_('${formatCurrency(value: ${/price}, currency: \'USD\')}/year'), '$1,299.50/year');
  eq('formatString literal args', fs_('${formatNumber(value: 3.14159, decimals: 2)}'), '3.14');
  eq('formatString bare path in fn', fs_('${formatNumber(value: /n, decimals: 1)}'), '1,234,567.9');
  eq('formatString via call', fns.resolveDynamic({call: 'formatString', args: {value: 'v=${/count}'}, returnType: 'string'}, ctx), 'v=1');

  eq('formatNumber default', fns.callFunction('formatNumber', {value: {path: '/n'}}, ctx), '1,234,567.891');
  eq('formatNumber decimals', fns.callFunction('formatNumber', {value: 1234.5, decimals: 2}, ctx), '1,234.50');
  eq('formatNumber no grouping', fns.callFunction('formatNumber', {value: 1234.5, decimals: 0, grouping: false}, ctx), '1235');
  eq('formatCurrency USD', fns.callFunction('formatCurrency', {value: {path: '/price'}, currency: 'USD'}, ctx), '$1,299.50');
  eq('formatCurrency EUR 0dp', fns.callFunction('formatCurrency', {value: 12, currency: 'EUR', decimals: 0}, ctx), '€12');
  eq('formatDate MMM dd, yyyy', fns.callFunction('formatDate', {value: '2026-01-16T14:30:00', format: 'MMM dd, yyyy'}, ctx), 'Jan 16, 2026');
  eq('formatDate HH:mm', fns.callFunction('formatDate', {value: '2026-01-16T14:30:00', format: 'HH:mm'}, ctx), '14:30');
  eq('formatDate h:mm a', fns.callFunction('formatDate', {value: '2026-01-16T14:30:00', format: 'h:mm a'}, ctx), '2:30 PM');
  eq('formatDate EEEE, d MMMM', fns.callFunction('formatDate', {value: '2026-01-16', format: 'EEEE, d MMMM'}, ctx), 'Friday, 16 January');
  eq('formatDate quoted literal', fns.callFunction('formatDate', {value: '2026-01-16T19:05:00', format: "EEE 'at' h:mm a"}, ctx), 'Fri at 7:05 PM');
  eq('formatDate E', fns.callFunction('formatDate', {value: '2026-01-16', format: 'E'}, ctx), 'Fri');
  eq('formatDate yy', fns.callFunction('formatDate', {value: '2026-01-16', format: 'yy/M/d'}, ctx), '26/1/16');
  eq('pluralize one', fns.callFunction('pluralize', {value: 1, one: 'item', other: 'items'}, ctx), 'item');
  eq('pluralize other', fns.callFunction('pluralize', {value: 5, one: 'item', other: 'items'}, ctx), 'items');
  eq('pluralize zero explicit', fns.callFunction('pluralize', {value: 0, zero: 'none', one: 'item', other: 'items'}, ctx), 'none');
  eq('pluralize zero fallback', fns.callFunction('pluralize', {value: 0, one: 'item', other: 'items'}, ctx), 'items');

  // checks
  const t = (call, args) => fns.callFunction(call, args, ctx);
  eq('required ok', t('required', {value: {path: '/user/first'}}), true);
  eq('required empty string', t('required', {value: {path: '/empty'}}), false);
  eq('required empty list', t('required', {value: {path: '/list'}}), false);
  eq('required missing', t('required', {value: {path: '/missing'}}), false);
  eq('required zero ok', t('required', {value: 0}), true);
  eq('regex ok', t('regex', {value: '12345', pattern: '^[0-9]{5}$'}), true);
  eq('regex fail', t('regex', {value: {path: '/zip'}, pattern: '^[0-9]{5}$'}), false);
  eq('regex invalid pattern', t('regex', {value: 'x', pattern: '('}), false);
  eq('length min ok', t('length', {value: 'password1', min: 8}), true);
  eq('length min fail', t('length', {value: 'short', min: 8}), false);
  eq('length max fail', t('length', {value: 'toolong', max: 3}), false);
  eq('numeric range ok', t('numeric', {value: '5', min: 1, max: 10}), true);
  eq('numeric range fail', t('numeric', {value: 11, max: 10}), false);
  eq('numeric NaN', t('numeric', {value: 'abc', min: 0}), false);
  eq('email ok', t('email', {value: {path: '/email'}}), true);
  eq('email fail', t('email', {value: 'nope@'}), false);
  eq('and true', t('and', {values: [true, {path: '/agree'}]}), true);
  eq('and false', t('and', {values: [true, {call: 'required', args: {value: {path: '/empty'}}}]}), false);
  eq('or true', t('or', {values: [false, {call: 'email', args: {value: {path: '/email'}}}]}), true);
  eq('or false', t('or', {values: [false, false]}), false);
  eq('not', t('not', {value: {path: '/agree'}}), false);
  eq('evaluateChecks CheckRule', fns.evaluateChecks([
    {condition: {call: 'required', args: {value: {path: '/empty'}}}, message: 'Required'},
    {condition: {call: 'email', args: {value: {path: '/email'}}}, message: 'Bad email'},
  ], ctx), ['Required']);
  eq('evaluateChecks legacy inline', fns.evaluateChecks([
    {call: 'regex', args: {value: {path: '/zip'}, pattern: '^\\d{5}$'}, message: 'Zip'},
  ], ctx), ['Zip']);
  eq('action context', fns.resolveActionContext({a: {path: 'name'}, b: 'lit', c: {path: '/nope'}}, mkctx(data, '/items/1')),
      {a: 'Pear', b: 'lit', c: null});
  // openUrl security
  const opened = [];
  const octx = {...ctx, openUrl: (u) => opened.push(u), baseUrl: 'http://example.test/x'};
  fns.callFunction('openUrl', {url: 'javascript:alert(1)'}, octx);
  fns.callFunction('openUrl', {url: 'https://a2ui.org/'}, octx);
  fns.callFunction('openUrl', {url: '/rel'}, octx);
  eq('openUrl allowlist', opened, ['https://a2ui.org/', 'http://example.test/rel']);
  check('unknown fn noted', (() => { const c = mkctx({}); fns.callFunction('nope', {}, c); return c.errors.length === 1; })());
}

// ---- markdown --------------------------------------------------------------
{
  const blocks = md.parseMarkdown('# Heading 1\n\nThis is **bold** and *italic* and `code`.\n\n- one\n- two\n\n[Link](https://google.com) [bad](javascript:alert(1))');
  eq('md block types', blocks.map((b) => b.type), ['h', 'p', 'ul', 'p']);
  eq('md heading level', blocks[0].level, 1);
  eq('md inline kinds', blocks[1].inlines.map((n) => n.t), ['text', 'strong', 'text', 'em', 'text', 'code', 'text']);
  eq('md list items', blocks[2].items.map((it) => md.inlineText(it)), ['one', 'two']);
  eq('md safe link', blocks[3].inlines[0], {t: 'link', href: 'https://google.com', c: [{t: 'text', v: 'Link'}]});
  eq('md unsafe link -> span', blocks[3].inlines[2].t, 'span');
  eq('md html stays text', md.inlineText(md.parseMarkdown('<b>x</b>')[0].inlines), '<b>x</b>');
  eq('md line break', md.parseMarkdown('a\nb')[0].inlines.map((n) => n.t), ['text', 'br', 'text']);
  eq('md snake_case not italic', md.inlineText(md.parseMarkdown('my_var_name')[0].inlines), 'my_var_name');
  eq('md escaped star', md.inlineText(md.parseMarkdown('\\*not\\*')[0].inlines), '*not*');
}

// ---- icons -----------------------------------------------------------------
{
  eq('icon accountCircle', icons.iconGlyph('accountCircle'), {glyph: 'account_circle', fill: 0, known: true});
  eq('icon starOff outline', icons.iconGlyph('starOff').fill, 0);
  eq('icon star filled', icons.iconGlyph('star').fill, 1);
  eq('icon count', icons.BASIC_ICON_NAMES.length, 59);
  eq('icon unknown snake', icons.iconGlyph('someThing').glyph, 'some_thing');
  // Every catalog icon is mapped.
  const cat = JSON.parse(fs.readFileSync(path.join(ROOT, 'seeds/a2ui_basic_catalog_v0_9.json'), 'utf8'));
  const names = cat.components.Icon.allOf[2].properties.name.oneOf[0].enum;
  check('all catalog icons mapped', names.every((n) => icons.iconGlyph(n).known),
      names.filter((n) => !icons.iconGlyph(n).known).join(','));
}

// ---- message processing ----------------------------------------------------
{
  const store = new model.SurfaceStore();
  const ch = store.process([
    {version: 'v0.9', updateComponents: {surfaceId: 's', components: [
      {id: 'root', component: 'Column', children: ['t']}, {id: 't', component: 'Text', text: {path: '/x'}},
    ]}},
    {version: 'v0.9', updateDataModel: {surfaceId: 's', path: '/x', value: 'hi'}},
    {version: 'v0.9', createSurface: {surfaceId: 's', catalogId: 'basic', theme: {primaryColor: '#FF0000'}}},
  ]);
  const s = store.surfaces.get('s');
  check('components before createSurface buffered', s && s.components.size === 2 && s.hasRoot);
  eq('data applied before create', s.getData('/x'), 'hi');
  eq('createSurface kept theme', s.theme.primaryColor, '#FF0000');
  check('structure change reported', ch.structure.has('s') && ch.data.has('s'));
  store.process([{updateComponents: {surfaceId: 's', components: [{id: 't', component: 'Text', text: 'upserted'}]}}]);
  eq('upsert by id', s.components.get('t').text, 'upserted');
  store.process([{updateDataModel: {surfaceId: 's', path: '/x'}}]);
  eq('omit value deletes', s.getData('/x'), undefined);
  store.process([{updateDataModel: {surfaceId: 's', value: {a: 1}}}]);
  eq('no path replaces root', s.data, {a: 1});
  store.process([{bogus: {}}, null]);
  check('bad messages collected not thrown', store.errors.length === 2);
  store.process([{deleteSurface: {surfaceId: 's'}}]);
  check('deleteSurface', !store.surfaces.has('s'));
}

// ---- spec examples: template expansion ------------------------------------
const exDir = path.join(ROOT, 'seeds/spec_examples');
const examples = Object.fromEntries(fs.readdirSync(exDir).filter((f) => f.endsWith('.json'))
    .map((f) => [f.replace(/\.json$/, ''), JSON.parse(fs.readFileSync(path.join(exDir, f), 'utf8'))]));
function load(name) {
  const store = new model.SurfaceStore();
  store.process(examples[name].messages);
  const s = [...store.surfaces.values()][0];
  return {store, s, tree: model.walkSurface(s)};
}
function countInstances(tree, id) { return tree.nodes.filter((n) => n.id === id).length; }
{
  let r = load('34_child-list-template');
  eq('34 item-row x3', countInstances(r.tree, 'item-row'), 3);
  eq('34 scopes', r.tree.nodes.filter((n) => n.id === 'item-name').map((n) => n.scope), ['/items/0', '/items/1', '/items/2']);
  const nameCtx = mkctx(r.s.data, '/items/1');
  eq('34 relative text', fns.resolveDynamic(r.s.components.get('item-name').text, nameCtx), 'Banana');
  r = load('31_incremental-dashboard');
  eq('31 log-template x3', countInstances(r.tree, 'log-template'), 3);
  check('31 loading placeholders replaced', countInstances(r.tree, 'panel-a-loading') === 0 && countInstances(r.tree, 'analytics-card') === 1);
  r = load('00_incremental');
  check('00_incremental restaurants expanded', countInstances(r.tree, 'restaurant_card') >= 2, String(countInstances(r.tree, 'restaurant_card')));
  for (const [name, tmplId] of [['18_track-list', 'track-item-template'], ['13_coffee-order', 'order-item-template'],
    ['33_financial-data-grid', 'row-template'], ['12_chat-message', 'message-template'], ['03_calendar-day', 'event-template'],
    ['21_shipping-status', 'step-item-template'], ['04_weather-current', 'forecast-day-template']]) {
    r = load(name);
    const tmplComp = [...r.s.components.values()].find((c) => c.children && c.children.componentId === tmplId);
    const listLen = (model.getAt(r.s.data, model.resolvePath('', tmplComp.children.path)) || []).length;
    check(`${name} template count`, listLen > 0 && countInstances(r.tree, tmplId) === listLen,
        `instances=${countInstances(r.tree, tmplId)} data=${listLen}`);
  }
  r = load('24_recipe-card');
  check('24 shared template used by 2 lists', countInstances(r.tree, 'item-template') >= 2);
  // 33: relative formatCurrency inside template.
  r = load('33_financial-data-grid');
  const priceNode = r.tree.nodes.find((n) => n.scope === '/assets/0' && r.s.components.get(n.id).text?.call === 'formatCurrency');
  check('33 template currency resolves', priceNode && /^\$[\d,]+\.\d\d$/.test(
      fns.resolveDynamic(r.s.components.get(priceNode.id).text, mkctx(r.s.data, priceNode.scope))));

  // Every example: walks without missing refs, all ids present, evaluates every dynamic text.
  for (const [name, ex] of Object.entries(examples)) {
    const store = new model.SurfaceStore();
    store.process(ex.messages);
    check(`${name}: no message errors`, store.errors.length === 0, store.errors.join('; '));
    for (const s of store.surfaces.values()) {
      const tree = model.walkSurface(s);
      check(`${name}: has root`, s.hasRoot);
      check(`${name}: no missing refs`, tree.missing.length === 0, JSON.stringify(tree.missing));
      const ctxErrors = [];
      for (const n of tree.nodes) {
        const c = s.components.get(n.id);
        for (const k of ['text', 'label', 'url', 'value']) {
          if (c[k] == null) continue;
          const cx = {...mkctx(s.data, n.scope), errors: ctxErrors};
          fns.resolveDynamic(c[k], cx);
        }
      }
      check(`${name}: dynamic values resolve without errors`, ctxErrors.length === 0, ctxErrors.join('; '));
      const unsupported = new Set(tree.nodes.map((n) => n.component).filter((t) => !model.BASIC_COMPONENTS.has(t)));
      const expected = new Set(ex.customComponents || []);
      check(`${name}: unsupported == customComponents`, [...unsupported].every((u) => expected.has(u)), [...unsupported].join(','));
    }
  }
}

// ---- theme (pure parts of a2ui.js need a DOM-free import) ------------------
{
  // a2ui.js only touches DOM inside functions, so importing is safe in node.
  const a2ui = await import(R('a2ui.js'));
  const v = a2ui.themeToCssVars({primary: '#123456', radius_card: 16, space: 4, type_scale: 1.25});
  eq('theme primary', v['--a2ui-primary'], '#123456');
  eq('theme radius px', v['--a2ui-radius-card'], '16px');
  eq('theme space', v['--a2ui-space'], '4px');
  eq('theme scale', v['--a2ui-type-scale'], '1.25');
  check('theme font stack', v['--a2ui-font'].startsWith('"Google Sans Flex"'));
  const dark = a2ui.resolveTheme({...a2ui.DEFAULT_THEME, scheme: 'dark'});
  check('dark swaps default light surface', dark.surface !== '#FFFFFF' && dark.on_surface !== '#1F1F1F');
  const darkCustom = a2ui.resolveTheme({scheme: 'dark', surface: '#000010'});
  eq('dark keeps custom surface', darkCustom.surface, '#000010');
  eq('onColor yellow -> dark text', a2ui.onColorFor('#FFEB3B'), '#1F1F1F');
  eq('onColor navy -> white', a2ui.onColorFor('#0B57D0'), '#FFFFFF');

  // Expanded tokens + custom tokens + two-way CSS parsing
  const expanded = a2ui.themeToCssVars({
    primary: '#0F766E',
    radius_chip: 99,
    radius_media: 18,
    border_width: 2,
    card_elevation: 3,
    heading_scale: 1.15,
    heading_weight: 700,
    font_family_heading: 'Fraunces, Georgia, serif',
    card_padding: 20,
    surface_padding: 24,
    control_height: 44,
    custom_tokens: {
      'brand-gold': {value: '#D97706', maps_to: 'tertiary'},
      '--a2ui-badge-bg': {value: '#FEF3C7', maps_to: ''},
    },
  });
  eq('expanded radius chip', expanded['--a2ui-radius-chip'], '99px');
  eq('expanded radius media', expanded['--a2ui-radius-media'], '18px');
  eq('expanded border width', expanded['--a2ui-border-width'], '2px');
  eq('expanded heading scale', expanded['--a2ui-heading-scale'], '1.15');
  eq('expanded heading weight', expanded['--a2ui-heading-weight'], '700');
  check('expanded heading font', expanded['--a2ui-font-heading'].includes('Fraunces'));
  eq('expanded card padding', expanded['--a2ui-card-padding'], '20px');
  eq('expanded surface padding', expanded['--a2ui-surface-padding'], '24px');
  eq('expanded control height', expanded['--a2ui-control-height'], '44px');
  eq('custom token var emitted', expanded['--a2ui-brand-gold'], '#D97706');
  eq('custom token maps_to overrides tertiary', expanded['--a2ui-tertiary'], '#D97706');
  eq('custom token badge bg', expanded['--a2ui-badge-bg'], '#FEF3C7');

  const cssText = a2ui.themeToCssText({
    ...a2ui.DEFAULT_THEME,
    primary: '#1E293B',
    radius_card: 8,
    border_width: 2,
    custom_tokens: {'accent-warm': {value: '#F59E0B', maps_to: 'secondary'}},
    custom_css: '.a2ui-Card {\n  border-style: dashed;\n}',
  });
  check('themeToCssText includes :root and custom css', cssText.includes('--a2ui-primary: #1E293B;') && cssText.includes('border-style: dashed;'));
  const parsedTheme = a2ui.parseCssToTheme(cssText + '\n:root { --a2ui-radius-card: 14px; --a2ui-new-token: #10B981; }', a2ui.DEFAULT_THEME);
  eq('parseCssToTheme primary', parsedTheme.primary, '#1E293B');
  eq('parseCssToTheme overridden radius_card', parsedTheme.radius_card, 14);
  eq('parseCssToTheme border_width', parsedTheme.border_width, 2);
  eq('parseCssToTheme custom token', parsedTheme.custom_tokens['new-token'].value, '#10B981');
  check('parseCssToTheme preserves custom_css', parsedTheme.custom_css.includes('border-style: dashed;'));

  // Dynamic Google Fonts extraction & category-aware font stacks
  const gfFams = a2ui.extractGoogleFontFamilies({
    font_family: 'Nunito Sans',
    font_family_heading: 'Quicksand',
    font_family_mono: 'JetBrains Mono',
  });
  eq('extractGoogleFontFamilies imported typefaces', gfFams, ['Nunito Sans', 'Quicksand', 'JetBrains Mono']);
  const gfUrl = a2ui.buildGoogleFontsUrl(gfFams);
  check('buildGoogleFontsUrl includes families',
      gfUrl.includes('Nunito+Sans:') && gfUrl.includes('Quicksand:') && gfUrl.includes('JetBrains+Mono:'));
  const defaultFams = a2ui.extractGoogleFontFamilies(a2ui.DEFAULT_THEME);
  eq('extractGoogleFontFamilies skips default/preloaded fonts', defaultFams, []);

  const monoSerifTheme = a2ui.themeToCssVars({
    font_family: 'JetBrains Mono',
    font_family_heading: 'Noto Serif',
  });
  check('mono body font stack uses monospace fallback',
      monoSerifTheme['--a2ui-font'].startsWith('"JetBrains Mono"') &&
      monoSerifTheme['--a2ui-font'].endsWith('monospace'));
  check('serif heading font stack uses serif fallback',
      monoSerifTheme['--a2ui-font-heading'].startsWith('"Noto Serif"') &&
      monoSerifTheme['--a2ui-font-heading'].endsWith('serif'));

  // 4-value box spacing tokens (top right bottom left) and canonical margin tokens
  eq('parseBoxPx single scalar', a2ui.parseBoxPx(16), [16, 16, 16, 16]);
  eq('parseBoxPx 2-value shorthand', a2ui.parseBoxPx('12px 20px'), [12, 20, 12, 20]);
  eq('parseBoxPx 3-value shorthand', a2ui.parseBoxPx('12px 20px 8px'), [12, 20, 8, 20]);
  eq('parseBoxPx 4-value trbl', a2ui.parseBoxPx('12px 20px 8px 16px'), [12, 20, 8, 16]);
  eq('coerceBoxPx collapses uniform sides to int', a2ui.coerceBoxPx('16px 16px 16px 16px'), 16);
  eq('coerceBoxPx preserves 4 distinct sides', a2ui.coerceBoxPx('12px 20px 8px 16px'), '12px 20px 8px 16px');
  const boxVars = a2ui.themeToCssVars({
    card_padding: '12px 20px 8px 16px',
    surface_padding: '24px 16px',
    card_margin: '8px 4px 12px 4px',
    item_margin: '6px 8px 6px 8px',
  });
  eq('boxVars card_padding 4-value', boxVars['--a2ui-card-padding'], '12px 20px 8px 16px');
  eq('boxVars surface_padding expanded to 4-value', boxVars['--a2ui-surface-padding'], '24px 16px 24px 16px');
  eq('boxVars card_margin 4-value', boxVars['--a2ui-card-margin'], '8px 4px 12px 4px');
  eq('boxVars item_margin 4-value', boxVars['--a2ui-item-margin'], '6px 8px 6px 8px');
  const boxCss = a2ui.themeToCssText({
    ...a2ui.DEFAULT_THEME,
    card_padding: '10px 14px 12px 16px',
    card_margin: '4px 8px 4px 8px',
  });
  const roundtrippedBox = a2ui.parseCssToTheme(boxCss, a2ui.DEFAULT_THEME);
  eq('parseCssToTheme 4-value card_padding', roundtrippedBox.card_padding, '10px 14px 12px 16px');
  eq('parseCssToTheme 4-value card_margin', roundtrippedBox.card_margin, '4px 8px 4px 8px');
}

// ---- custom components (custom.js + mountSurface on a tiny fake DOM) -------
{
  const custom = await import(R('custom.js'));
  const a2ui = await import(R('a2ui.js'));
  const {compileCustomComponent, sanitizeCss, parseHtml, evalExpr, eachItems, testCondition} = custom;

  // Pure helpers.
  eq('custom cond truthy', [testCondition({expr: 'x', value: null}, 'a'), testCondition({expr: 'x', value: null}, ''),
    testCondition({expr: 'x', value: null}, 0), testCondition({expr: 'x', value: null}, []), testCondition({expr: 'x', value: null}, 'false')],
  [true, false, false, false, false]);
  eq('custom cond prop:value', [testCondition({expr: 'v', value: 'full'}, 'full'), testCondition({expr: 'v', value: 'full'}, 'compact')], [true, false]);
  const hooks = {prop: (n) => ({user: {name: 'Ada', tags: ['x']}, items: {path: '/items'}}[n]), resolveIn: (v) => v && v.path === '/n' ? 'resolved' : v};
  eq('custom evalExpr dotted', evalExpr('user.name', null, hooks), 'Ada');
  eq('custom evalExpr local', evalExpr('it.title', {it: {value: {title: 'T'}, scope: '/a/0'}}, hooks), 'T');
  eq('custom evalExpr nested dynamic', evalExpr('it.n', {it: {value: {n: {path: '/n'}}, scope: null}}, hooks), 'resolved');
  eq('custom eachItems scopes', eachItems('xs', null, {prop: () => ['a', 'b'], propPath: () => '/xs'}), [{value: 'a', scope: '/xs/0'}, {value: 'b', scope: '/xs/1'}]);
  const pt = parseHtml('<ul><li>a<li>b</ul><img src="/x"><br/><p>t &amp; &lt;x&gt;</p>');
  eq('custom parseHtml implicit li close', pt.children[0].children.length, 2);
  eq('custom parseHtml entities', pt.children[3].children[0], 't & <x>');

  // Sanitizer.
  const bad = compileCustomComponent({web: {
    template: '<div onclick="x()" class="a" href="/h"><script>alert(1)</script><a href="javascript:alert(1)">link</a>' +
        '<img src="javascript:alert(1)"><img src="http://evil/x.png"><span title="javascript:alert(2)">s</span>' +
        '<iframe src="https://x"></iframe><form><input value="x">kept</form><p style="color:red; --c: {{tint}}">p</p></div>',
    css: '@import url("https://x/y.css"); .a{background:url(https://evil/x.png);color:var(--a2ui-primary)}' +
        ' .b{background:url(data:image/png;base64,AAAA)} .c{width:expression(alert(1));behavior:url(x.htc)}',
  }});
  const flat = JSON.stringify(bad.tree);
  check('custom sanitize ok (renderable)', bad.ok);
  check('custom sanitize strips script', !flat.includes('script') && !flat.includes('alert(1)'), flat);
  check('custom sanitize strips onclick', !flat.includes('onclick') && bad.errors.some((e) => e.includes('onclick')));
  check('custom sanitize strips href', !flat.includes('"href"') && bad.errors.some((e) => e.includes("'href'")));
  check('custom sanitize strips javascript:', !flat.includes('javascript:') && bad.errors.some((e) => e.includes('script URL')));
  check('custom sanitize strips iframe', !flat.includes('iframe'));
  check('custom sanitize unwraps a/form keeps text', flat.includes('"link"') && flat.includes('"kept"') && !flat.includes('"input"'));
  check('custom sanitize http img src dropped', !flat.includes('http://evil'));
  check('custom sanitize style keeps only custom props', flat.includes('--c') && !flat.includes('color:red'));
  check('custom css strips @import', !bad.css.includes('@import') && bad.errors.some((e) => e.includes('@import')));
  check('custom css strips external url()', !bad.css.includes('https://evil') && bad.css.includes('data:image/png'));
  check('custom css strips expression/behavior', !/expression\(|behavior:/i.test(bad.css));
  eq('custom css token refs', sanitizeCss('.x{color:var(--a2ui-primary);border-radius:var(--a2ui-radius-card)}').tokenRefs, ['--a2ui-primary', '--a2ui-radius-card']);
  check('custom empty template fails', !compileCustomComponent({web: {template: '  '}}).ok);
  check('custom only-script template fails', !compileCustomComponent({web: {template: '<script>x</script>'}}).ok);
  const undeclared = compileCustomComponent({props: {properties: {title: {}}}, web: {template: '<p>{{title}} {{nope}}</p>'}});
  check('custom undeclared prop error', undeclared.ok && undeclared.errors.some((e) => e.includes("'nope'")));
  const badBind = compileCustomComponent({web: {template: '<p>{{price | currency}}</p>'}});
  check('custom invalid binding reported', badBind.errors.some((e) => e.includes('unsupported binding')));

  // ---- tiny fake DOM (enough for mountSurface + shadow roots + events).
  class FNode {
    constructor(doc) { this.ownerDocument = doc; this.childNodes = []; this.parentNode = null; }
    appendChild(n) {
      if (n instanceof FFrag) { for (const c of [...n.childNodes]) this.appendChild(c); return n; }
      n.remove?.(); n.parentNode = this; this.childNodes.push(n); return n;
    }
    append(...ns) { for (const n of ns) this.appendChild(typeof n === 'string' ? this.ownerDocument.createTextNode(n) : n); }
    prepend(...ns) { const first = this.childNodes[0] || null; for (const n of ns) this.insertBefore(n, first); }
    insertBefore(n, ref) {
      if (!ref) return this.appendChild(n);
      if (n instanceof FFrag) { for (const c of [...n.childNodes]) this.insertBefore(c, ref); return n; }
      n.remove?.(); const i = this.childNodes.indexOf(ref); n.parentNode = this; this.childNodes.splice(i, 0, n); return n;
    }
    remove() { if (this.parentNode) { const a = this.parentNode.childNodes; a.splice(a.indexOf(this), 1); this.parentNode = null; } }
    replaceChildren(...ns) { for (const c of [...this.childNodes]) c.remove(); this.append(...ns); }
    get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
    set textContent(v) { this.replaceChildren(); if (v !== '' && v != null) this.append(String(v)); }
    contains(n) { for (let x = n; x; x = x.parentNode) if (x === this) return true; return false; }
    get isConnected() { return true; }
    *walk() { for (const c of this.childNodes) { if (c instanceof FEl) { yield c; yield* c.walk(); } } }
    querySelectorAll(sel) {
      const m = /^\[([\w-]+)(?:="([^"]*)")?\]$/.exec(sel) || null;
      const tag = /^[a-z0-9]+$/.test(sel) ? sel : null;
      const cls = /^\.([\w-]+)$/.exec(sel);
      return [...this.walk()].filter((e) => m ? (e.hasAttribute(m[1]) && (m[2] == null || e.getAttribute(m[1]) === m[2])) :
        tag ? e.localName === tag : cls ? e.classList.contains(cls[1]) : false);
    }
    querySelector(sel) { return sel.includes(':scope') ? null : (this.querySelectorAll(sel)[0] || null); }
  }
  class FFrag extends FNode {}
  class FText extends FNode {
    constructor(doc, d) { super(doc); this.data = String(d); }
    get textContent() { return this.data; }
    set textContent(v) { this.data = String(v); }
  }
  class FComment extends FText { get textContent() { return ''; } }
  class FShadow extends FNode { constructor(doc, host) { super(doc); this.host = host; } }
  const camelToKebab = (k) => k.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`);
  class FEl extends FNode {
    constructor(doc, tag) {
      super(doc); this.localName = tag; this.tagName = tag.toUpperCase(); this.attrs = new Map(); this.listeners = {};
      const props = new Map();
      this.style = {setProperty: (k, v) => props.set(k, String(v)), removeProperty: (k) => props.delete(k), getPropertyValue: (k) => props.get(k) || ''};
      this.dataset = new Proxy({}, {
        get: (_, k) => this.getAttribute(`data-${camelToKebab(String(k))}`) ?? undefined,
        set: (_, k, v) => { this.setAttribute(`data-${camelToKebab(String(k))}`, v); return true; },
      });
      const el = this;
      this.classList = {
        add: (...c) => { const s = new Set(el.className.split(/\s+/).filter(Boolean)); c.forEach((x) => s.add(x)); el.className = [...s].join(' '); },
        remove: (...c) => { el.className = el.className.split(/\s+/).filter((x) => x && !c.includes(x)).join(' '); },
        contains: (c) => el.className.split(/\s+/).includes(c),
        toggle: (c, on) => { const has = el.classList.contains(c); const want = on === undefined ? !has : !!on; if (want) el.classList.add(c); else el.classList.remove(c); return want; },
      };
    }
    get className() { return this.attrs.get('class') || ''; }
    set className(v) { this.attrs.set('class', String(v)); }
    setAttribute(k, v) { this.attrs.set(k, String(v)); }
    getAttribute(k) { return this.attrs.has(k) ? this.attrs.get(k) : null; }
    hasAttribute(k) { return this.attrs.has(k); }
    removeAttribute(k) { this.attrs.delete(k); }
    addEventListener(t, fn) { (this.listeners[t] ||= []).push(fn); }
    attachShadow() { this.shadowRoot = new FShadow(this.ownerDocument, this); return this.shadowRoot; }
    focus() {}
  }
  // Event path: slotted light-DOM children route through their <slot>.
  const eventParent = (n) => {
    if (n instanceof FShadow) return n.host;
    const p = n.parentNode;
    const slotName = n.getAttribute && n.getAttribute('slot');
    if (p && p.shadowRoot && slotName) {
      const slot = p.shadowRoot.querySelectorAll('slot').find((s) => s.getAttribute('name') === slotName);
      if (slot) return slot;
    }
    return p;
  };
  const fire = (target, type, extra = {}) => {
    const pathArr = [];
    for (let n = target; n; n = eventParent(n)) pathArr.push(n);
    const ev = {type, target, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, stopPropagation() {}, composedPath: () => pathArr, ...extra};
    for (const n of pathArr) for (const fn of (n.listeners?.[type] || [])) fn(ev);
    return ev;
  };
  const fakeDoc = {
    createElement: (t) => new FEl(fakeDoc, t),
    createTextNode: (d) => new FText(fakeDoc, d),
    createComment: (d) => new FComment(fakeDoc, d),
    createDocumentFragment: () => new FFrag(fakeDoc),
    activeElement: null,
  };
  const container = new FEl(fakeDoc, 'div');

  const tileDef = {
    name: 'ProductTile',
    props: {type: 'object', properties: {
      title: {$ref: 'common_types.json#/$defs/DynamicString'},
      price: {$ref: 'common_types.json#/$defs/DynamicString'},
      image: {$ref: 'common_types.json#/$defs/DynamicString'},
      badge: {$ref: 'common_types.json#/$defs/DynamicString'},
      variant: {type: 'string', enum: ['compact', 'full'], default: 'full'},
      features: {$ref: 'common_types.json#/$defs/DynamicStringList'},
      specs: {type: 'array'},
      icon: {$ref: 'common_types.json#/$defs/DynamicString'},
      footer: {$ref: 'common_types.json#/$defs/ComponentId'},
      extras: {$ref: 'common_types.json#/$defs/ChildList'},
      action: {$ref: 'common_types.json#/$defs/Action'},
    }, required: ['title']},
    web: {
      template: '<article class="tile {{variant}}" data-action="action" style="--tile-accent: {{badge}}">' +
          '<img src="{{image}}" alt="{{title}}"><span class="badge" data-if="badge">{{badge}}</span>' +
          '<span class="full-only" data-if="variant:full">F</span><span class="nobadge" data-unless="badge">none</span>' +
          '<h3 title="{{title}}">{{title}}</h3><p class="price">From {{price}}</p><span class="ic" data-icon="{{icon}}"></span>' +
          '<ul class="feat"><li data-each="features as it">{{it}}</li></ul>' +
          '<ul class="specs"><li data-each="specs as s" data-unless="s.hidden" data-action="s.action"><strong>{{s.label}}</strong>: <em>{{s.value}}</em></li></ul>' +
          '<footer><slot name="footer"></slot></footer><div class="x"><slot name="extras"></slot></div></article>',
      css: '.tile{border-radius:var(--a2ui-radius-card)}',
    },
  };
  const compiledTile = compileCustomComponent(tileDef);
  check('custom tile compiles clean', compiledTile.ok && compiledTile.errors.length === 0, compiledTile.errors.join('; '));
  eq('custom tile slots/actions', [compiledTile.slots, compiledTile.actions], [['footer', 'extras'], ['action']]);

  const actions = [];
  const msgs = [
    {version: 'v0.9', createSurface: {surfaceId: 's1', catalogId: 'https://dcb.local/catalogs/x'}},
    {version: 'v0.9', updateComponents: {surfaceId: 's1', components: [
      {id: 'root', component: 'Column', children: ['tile', 'bad']},
      {id: 'tile', component: 'ProductTile', title: {path: '/p/title'}, price: {call: 'formatCurrency', args: {value: {path: '/p/price'}, currency: 'USD'}},
        image: 'https://example.com/shoe.png', badge: {path: '/p/badge'}, icon: 'star',
        features: {path: '/p/features'}, specs: [{label: 'Weight', value: '220g', action: {event: {name: 'spec_click', context: {k: 'Weight'}}}}, {label: 'Drop', value: {path: '/p/drop'}}, {label: 'Hidden', value: 'x', hidden: true}],
        footer: 'buy', extras: {componentId: 'chip', path: '/p/chips'},
        accessibility: {label: 'Trail Runner tile'},
        action: {event: {name: 'open_product', context: {sku: {path: '/p/sku'}}}}},
      {id: 'buy', component: 'Button', child: 'buy-label', variant: 'primary', action: {event: {name: 'buy'}}},
      {id: 'buy-label', component: 'Text', text: 'Buy'},
      {id: 'chip', component: 'Text', text: {path: 'name'}},
      {id: 'bad', component: 'BrokenThing'},
    ]}},
    {version: 'v0.9', updateDataModel: {surfaceId: 's1', path: '/', value: {p: {title: 'Trail Runner', price: 129, badge: 'New', sku: 'TR-1', drop: '6mm',
      features: ['Grippy', 'Light'], chips: [{name: 'Red'}, {name: 'Blue'}]}}}},
  ];
  const surf = a2ui.mountSurface(container, msgs, {
    injectAssets: false, onAction: (p) => actions.push(p),
    customComponents: {ProductTile: tileDef, BrokenThing: {name: 'BrokenThing', web: {template: '<script>x</script>'}}, Text: {web: {template: '<p>no</p>'}}},
  });
  const host = surf.host.querySelectorAll('[data-a2ui-custom="ProductTile"]')[0];
  check('custom host rendered', !!host && host.getAttribute('data-a2ui-id') === 'tile' && host.classList.contains('a2ui-custom'));
  const sr = host.shadowRoot;
  const q1 = (sel) => sr.querySelectorAll(sel)[0];
  const textOf = (sel) => (q1(sel) ? q1(sel).textContent : null);
  check('custom shadow style', sr.childNodes[0].localName === 'style' && sr.childNodes[0].textContent.includes(':host{display:flex') &&
      sr.childNodes[0].textContent.includes('.tile{border-radius'));
  eq('custom text binding', textOf('h3'), 'Trail Runner');
  eq('custom mixed text + function call', textOf('.price'), 'From $129.00');
  eq('custom attr binding', [q1('img').getAttribute('src'), q1('img').getAttribute('alt'), q1('h3').getAttribute('title')],
      ['https://example.com/shoe.png', 'Trail Runner', 'Trail Runner']);
  eq('custom class binding + default prop', q1('article').getAttribute('class'), 'tile full');
  eq('custom style var binding', q1('article').style.getPropertyValue('--tile-accent'), 'New');
  eq('custom data-if truthy shown', q1('.badge').hasAttribute('hidden'), false);
  eq('custom data-if prop:value', q1('.full-only').hasAttribute('hidden'), false);
  eq('custom data-unless hidden when set', q1('.nobadge').hasAttribute('hidden'), true);
  eq('custom data-icon', [q1('.ic').textContent, q1('.ic').getAttribute('data-icon')], ['star', 'star']);
  eq('custom data-each bound primitives', sr.querySelectorAll('li').filter((l) => l.parentNode.getAttribute('class') === 'feat').map((l) => l.textContent), ['Grippy', 'Light']);
  const specLis = q1('.specs').childNodes.filter((n) => n.localName === 'li');
  eq('custom data-each literal objects + nested binding', specLis.map((l) => l.textContent), ['Weight: 220g', 'Drop: 6mm', 'Hidden: x']);
  eq('custom data-each + data-unless per item', specLis.map((l) => l.hasAttribute('hidden')), [false, false, true]);
  const slotted = host.childNodes.filter((n) => n instanceof FEl);
  eq('custom slot children in light DOM', slotted.map((n) => [n.getAttribute('data-a2ui-id'), n.getAttribute('slot'), n.getAttribute('data-a2ui-scope')]),
      [['buy', 'footer', null], ['chip', 'extras', '/p/chips/0'], ['chip', 'extras', '/p/chips/1']]);
  eq('custom ChildList template texts', slotted.slice(1).map((n) => n.textContent), ['Red', 'Blue']);
  eq('custom accessibility on host', host.getAttribute('aria-label'), 'Trail Runner tile');
  const art = q1('article');
  eq('custom action element is a button', [art.getAttribute('role'), art.tabIndex], ['button', 0]);
  fire(art, 'click');
  eq('custom action payload', actions.map((a) => [a.action.name, a.action.sourceComponentId, a.action.surfaceId, a.action.context]),
      [['open_product', 'tile', 's1', {sku: 'TR-1'}]]);
  const kev = fire(art, 'keydown', {key: 'Enter'});
  check('custom action Enter', actions.length === 2 && kev.defaultPrevented);
  fire(art, 'keydown', {key: ' '});
  check('custom action Space', actions.length === 3);
  fire(art, 'keydown', {key: 'a'});
  check('custom action ignores other keys', actions.length === 3);
  actions.length = 0;
  fire(specLis[0], 'click');  // loop item with data-action="s.action"
  eq('custom loop item data-action fires item action', actions.map((a) => [a.action.name, a.action.context]), [['spec_click', {k: 'Weight'}]]);
  actions.length = 0;
  fire(slotted[0], 'click');  // the slotted Button
  eq('custom slotted Button fires only its own action', actions.map((a) => a.action.name), ['buy']);

  // Reactive data model updates.
  actions.length = 0;
  surf.update([{version: 'v0.9', updateDataModel: {surfaceId: 's1', path: '/p/title', value: 'Trail Runner 2'}},
    {version: 'v0.9', updateDataModel: {surfaceId: 's1', path: '/p/badge', value: ''}},
    {version: 'v0.9', updateDataModel: {surfaceId: 's1', path: '/p/features', value: ['Grippy', 'Light', 'Vegan']}},
    {version: 'v0.9', updateDataModel: {surfaceId: 's1', path: '/p/price', value: 99}}]);
  const host2 = surf.host.querySelectorAll('[data-a2ui-custom="ProductTile"]')[0];
  const sr2 = host2.shadowRoot;
  check('custom reactive: same host (no rebuild)', host2 === host);
  eq('custom reactive text', sr2.querySelectorAll('h3')[0].textContent, 'Trail Runner 2');
  eq('custom reactive function call', sr2.querySelectorAll('.price')[0].textContent, 'From $99.00');
  eq('custom reactive data-if hides', [sr2.querySelectorAll('.badge')[0].hasAttribute('hidden'), sr2.querySelectorAll('.nobadge')[0].hasAttribute('hidden')], [true, false]);
  eq('custom reactive data-each', sr2.querySelectorAll('li').filter((l) => l.parentNode.getAttribute('class') === 'feat').map((l) => l.textContent), ['Grippy', 'Light', 'Vegan']);

  const meta = surf.meta;
  eq('custom meta.custom', meta.custom, ['ProductTile']);
  check('custom not in meta.unsupported', !meta.unsupported.includes('ProductTile'));
  check('custom broken def -> unsupported placeholder + error', meta.unsupported.includes('BrokenThing') &&
      meta.errors.some((e) => e.includes('BrokenThing')) && surf.host.querySelectorAll('.a2ui-unsupported').length === 1, JSON.stringify(meta));
  check('custom def with Basic name ignored', surf.host.querySelectorAll('[data-a2ui-custom="Text"]').length === 0);

  // interactive:false → no dispatch, not focusable.
  const inertActions = [];
  const inert = a2ui.mountSurface(new FEl(fakeDoc, 'div'), msgs, {injectAssets: false, interactive: false, onAction: (p) => inertActions.push(p), customComponents: {ProductTile: tileDef}});
  const inertArt = inert.host.querySelectorAll('[data-a2ui-custom="ProductTile"]')[0].shadowRoot.querySelectorAll('article')[0];
  fire(inertArt, 'click');
  check('custom inert: no action, tabIndex -1', inertActions.length === 0 && inertArt.tabIndex === -1);

  // Weight inside a Row lands on the host.
  const rowSurf = a2ui.mountSurface(new FEl(fakeDoc, 'div'), [
    {version: 'v0.9', createSurface: {surfaceId: 'r', catalogId: 'x'}},
    {version: 'v0.9', updateComponents: {surfaceId: 'r', components: [
      {id: 'root', component: 'Row', children: ['t']}, {id: 't', component: 'ProductTile', title: 'A', weight: 2}]}}],
  {injectAssets: false, customComponents: {ProductTile: tileDef}});
  const rowHost = rowSurf.host.querySelectorAll('[data-a2ui-id="t"]')[0];
  eq('custom weight on host', [rowHost.style.flex, rowHost.classList.contains('a2ui-weighted')], ['2 1 0', true]);
  eq('custom missing optional prop -> empty', rowHost.shadowRoot.querySelectorAll('.price')[0].textContent, 'From ');

  // Card.backgroundImage, Icon.name.url, Icon.name.svg, Icon.size, and custom SVG/data-bg.
  const weatherCustomDef = {
    name: 'WeatherBanner',
    props: {type: 'object', properties: {bgUrl: {type: 'string'}, label: {type: 'string'}}},
    web: {
      template: '<div class="wb" data-bg="{{bgUrl}}"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8" fill="#FFD54F"/></svg><span>{{label}}</span></div>',
      css: '.wb{padding:12px}',
    },
  };
  const compiledWb = compileCustomComponent(weatherCustomDef);
  check('custom SVG + data-bg compiles clean', compiledWb.ok && compiledWb.errors.length === 0, compiledWb.errors.join('; '));
  const imgSurf = a2ui.mountSurface(new FEl(fakeDoc, 'div'), [
    {version: 'v0.9', createSurface: {surfaceId: 'w', catalogId: 'x'}},
    {version: 'v0.9', updateComponents: {surfaceId: 'w', components: [
      {id: 'root', component: 'Card', child: 'col', backgroundImage: {url: '/api/gen/weather.png', scrim: 'dark', fit: 'cover'}},
      {id: 'col', component: 'Column', children: ['ic-url', 'ic-svg', 'wb']},
      {id: 'ic-url', component: 'Icon', name: {url: '/api/gen/icon.svg'}, size: 28},
      {id: 'ic-svg', component: 'Icon', name: {svg: '<svg viewBox="0 0 24 24"><path d="M4 12h16"/></svg>'}},
      {id: 'wb', component: 'WeatherBanner', bgUrl: '/api/gen/sky.png', label: 'Sunny'},
    ]}},
  ], {injectAssets: false, customComponents: {WeatherBanner: weatherCustomDef}});
  const cardEl = imgSurf.host.querySelectorAll('[data-a2ui-id="root"]')[0];
  check('Card.backgroundImage class + scrim', cardEl.classList.contains('a2ui-card-has-bg') && cardEl.classList.contains('a2ui-card-scrim-dark'));
  check('Card.backgroundImage renders img + scrim + content', cardEl.querySelectorAll('.a2ui-card-bg-img')[0]?.getAttribute('src') === '/api/gen/weather.png' &&
      cardEl.querySelectorAll('.a2ui-card-bg-scrim').length === 1 && cardEl.querySelectorAll('.a2ui-card-content').length === 1);
  const icUrlEl = imgSurf.host.querySelectorAll('[data-a2ui-id="ic-url"]')[0];
  check('Icon.name.url renders img + size', icUrlEl.classList.contains('a2ui-Icon-custom') &&
      icUrlEl.querySelectorAll('.a2ui-icon-img')[0]?.getAttribute('src') === '/api/gen/icon.svg' &&
      icUrlEl.style.getPropertyValue('--a2ui-icon-size') === '28px');
  const icSvgEl = imgSurf.host.querySelectorAll('[data-a2ui-id="ic-svg"]')[0];
  check('Icon.name.svg renders data URI img', icSvgEl.querySelectorAll('.a2ui-icon-img')[0]?.getAttribute('src')?.startsWith('data:image/svg+xml;utf8,'));
  const wbHost = imgSurf.host.querySelectorAll('[data-a2ui-id="wb"]')[0];
  const wbDiv = wbHost.shadowRoot.querySelectorAll('.wb')[0];
  check('Custom data-bg sets backgroundImage', wbDiv.style.backgroundImage.includes('/api/gen/sky.png'));
}

const total = passed + failures.length;
if (failures.length) {
  console.error(`renderer_check: ${failures.length}/${total} FAILED`);
  for (const f of failures) console.error(`  ✗ ${f}`);
  process.exit(1);
}
console.log(`renderer_check: all ${total} checks passed`);
