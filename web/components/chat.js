// Revision chat panel (right side of the project shell).
import {get, post, put} from './api.js';
import {diffView} from './diff.js';
import {h, icon, mount, relTime, spinner} from './dom.js';
import {jsonTree} from './jsontree.js';
import {renderMarkdown} from './markdown.js';
import {confirmRunWarnings} from './newrun.js';
import {emit, navigate, on, store} from './store.js';
import {toast} from './ui.js';

const HOME_LABEL = {
  theme: 'Settings › Theme',
  catalog: 'Settings › Catalog',
  component: 'a component',
};

// Views where the revision chat is relevant. Elsewhere the panel is hidden and
// opening chat routes to the constitution.
export const CHAT_VIEWS = new Set(['constitution', 'review', 'design']);
const prefKey = (view) => `dcb.chatOpen.${view}`;

/**
 * Review ⇄ Chat switch shown at the top of the review panel and the chat panel
 * in the review view (the two share the right column).
 */
export function reviewChatTabs(active, {onReview, onChat}) {
  const tab = (id, label, ic, on, fn) =>
      h('button.rc-tab', {
        type: 'button',
        id,
        role: 'tab',
        class: on ? 'on' : '',
        'aria-selected': String(on),
        onclick: () => !on && fn(),
      },
        icon(ic), h('span', label));
  return h(
      'div.rc-tabs', {role: 'tablist', 'aria-label': 'Review panel or chat'},
      tab(`rc-tab-review-${active}`, 'Review', 'rate_review',
          active === 'review', onReview),
      tab(`rc-tab-chat-${active}`, 'Chat', 'forum', active === 'chat', onChat));
}

function isHexColor(v) {
  return /^#([0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(
      String(v || '').trim());
}

function renderTokenValue(val) {
  if (val == null || val === '') return h('span.muted', '—');
  const s = String(val);
  if (isHexColor(s)) {
    return h(
        'span.token-val-chip',
        h('span.token-swatch-dot', {style: {backgroundColor: s}}),
        h('code', s),
    );
  }
  return h('code', s);
}

export class ChatPanel {
  constructor() {
    this.projectId = null;
    this.messages = [];
    this.chips =
        [];  // [{type:'output'|'guideline'|'component', id, label, thumb, ...}]
    this.dismissedSelectionKey = null;
    this.busy = false;
    this.lastAccepted = null;  // {revision, themeChangesCount}
    // Accepted/discarded proposals render collapsed; ids of the ones the user
    // expanded.
    this.expandedProposals = new Set();
    this.view = null;  // current route view (chat only lives in CHAT_VIEWS)
    this.el = h('aside.chat#chat-panel', {'aria-label': 'Revision chat'});
    this.build();
    on('chat-open', (d) => this.open(d || {}));
    on('chat-focus', (d) => this.focusMessage(d && d.message_id));
    on('review-selection-changed', (sel) => this.onReviewSelectionChanged(sel));
    on('project-updated', () => {
      if (this.loaded && this.isOpen()) this.renderList();
    });
  }

  hasRuns() {
    const p = store.project;
    if (p && p.id === this.projectId && p.stats &&
        typeof p.stats.runs === 'number') {
      return p.stats.runs > 0;
    }
    return Boolean(
        this.projectId && store.lastRunId && store.lastRunId[this.projectId]);
  }

  onReviewSelectionChanged(sel) {
    const comp = sel && sel.component;
    const out = sel && sel.output;
    if (!comp || !out) {
      this.dismissedSelectionKey = null;
      const before = this.chips.length;
      this.chips = this.chips.filter((c) => !c.fromSelection);
      if (this.chips.length !== before) this.renderChips();
      return;
    }
    const selKey = `${out.id}:${comp.id}`;
    if (this.dismissedSelectionKey && this.dismissedSelectionKey !== selKey) {
      this.dismissedSelectionKey = null;
    }
    if (this.dismissedSelectionKey === selKey) return;

    this.chips =
        this.chips.filter((c) => !c.fromSelection && c.type !== 'component');
    if (!this.chips.some((c) => c.type === 'output' && c.id === out.id)) {
      this.chips.push({
        type: 'output',
        id: out.id,
        label: `${out.sample_title || 'Output'} · ${out.viewport}`,
        thumb: out.thumb_url || null,
        fromSelection: true,
      });
    }
    this.chips.push({
      type: 'component',
      id: comp.id,
      output_id: out.id,
      component: comp.component,
      props: comp,
      label: `${comp.component} · #${comp.id}`,
      fromSelection: true,
    });
    this.renderChips();
  }

  /**
   * Opens chat (navigating to the constitution if needed), reloads the
   * conversation and scrolls to message `mid`.
   */
  focusMessage(mid) {
    if (!mid || !this.projectId) return;
    this.focusId = mid;
    this.loaded = false;
    if (this.isAllowed() && this.isOpen()) {
      this.load();
      return;
    }
    // setOpen navigates to the constitution when chat isn't allowed here; the
    // router then reopens the panel and load() runs because loaded=false.
    this.setOpen(true);
  }

  applyFocus() {
    if (!this.focusId) return;
    const el = this.listEl.querySelector(
        `[data-mid="${CSS.escape(String(this.focusId))}"]`);
    if (!el) return;
    this.focusId = null;
    requestAnimationFrame(() => {
      el.scrollIntoView({block: 'start', behavior: 'smooth'});
      el.classList.add('flash');
      setTimeout(() => el.classList.remove('flash'), 2400);
    });
  }

  build() {
    this.listEl = h('div.chat-list#chat-list');
    this.chipsEl = h('div.chat-chips#chat-chips');
    this.input = h('textarea.chat-input#chat-input', {
      rows: 3,
      placeholder:
          'Ask for constitution, design token, or generated imagery/SVG rules, e.g. \u201cUse a full-bleed background image of the weather in weather widgets\u201d\u2026',
      onkeydown: (e) => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
          e.preventDefault();
          this.send();
        }
      },
      oninput: () => this.autosize(),
    });
    this.fileInput = h('input', {
      type: 'file',
      accept: 'image/*',
      multiple: true,
      hidden: true,
      onchange: async (e) => {
        const files = Array.from(e.target.files || []);
        for (const f of files.slice(0, 4)) {
          const dataUrl = await new Promise((resolve) => {
            const r = new FileReader();
            r.onload = () => resolve(String(r.result || ''));
            r.onerror = () => resolve('');
            r.readAsDataURL(f);
          });
          if (dataUrl) {
            this.chips.push({
              type: 'image',
              id: `img-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
              name: f.name,
              label: `Style ref: ${f.name}`,
              thumb: dataUrl,
              data_url: dataUrl,
            });
          }
        }
        e.target.value = '';
        this.renderChips();
      },
    });
    this.attachBtn =
        h('button.icon-btn.chat-attach#chat-attach-image', {
          type: 'button',
          title:
              'Attach style reference image(s) for imagery / icon generation rules',
          onclick: () => this.fileInput.click(),
        },
          icon('add_photo_alternate'));
    this.sendBtn =
        h('button.btn.btn-primary.chat-send#chat-send',
          {type: 'button', onclick: () => this.send(), title: 'Send (Enter)'},
          icon('arrow_upward'));
    this.statusEl = h('div.chat-status');
    this.el.append(
        h('div.chat-head',
          h('div.chat-title', icon('forum'), h('span', 'Revise with chat')),
          reviewChatTabs('chat', {
            onReview: () => this.setOpen(false),
            onChat: () => {},
          }),
          h('button.icon-btn#chat-close',
            {'aria-label': 'Close chat', onclick: () => this.setOpen(false)},
            icon('right_panel_close'))),
        this.listEl,
        h('div.chat-compose', this.chipsEl,
          h('div.chat-row', this.attachBtn, this.fileInput, this.input,
            this.sendBtn),
          this.statusEl));
  }

  autosize() {
    this.input.style.height = 'auto';
    this.input.style.height =
        `${Math.min(220, Math.max(64, this.input.scrollHeight))}px`;
  }

  isOpen() {
    return document.body.classList.contains('chat-open');
  }
  /** Whether chat may be shown in the current view. */
  isAllowed() {
    return !!this.projectId && CHAT_VIEWS.has(this.view);
  }
  /**
   * Called by the router on every route change: hides chat outside
   * CHAT_VIEWS and restores the per-view open state inside them.
   */
  setView(view) {
    this.view = view;
    const want =
        this.isAllowed() && localStorage.getItem(prefKey(view)) === '1';
    if (want !== this.isOpen()) this.setOpen(want, {persist: false});
  }
  setOpen(open, {persist = true} = {}) {
    if (open && !this.isAllowed()) {
      if (this.projectId) {
        localStorage.setItem(prefKey('constitution'), '1');
        navigate(`/p/${this.projectId}/constitution`);
      }
      return;
    }
    document.body.classList.toggle('chat-open', open);
    if (persist && this.view)
      localStorage.setItem(prefKey(this.view), open ? '1' : '0');
    emit('chat-toggled', open);
    if (open && this.projectId && !this.loaded) this.load();
  }
  toggle() {
    this.setOpen(!this.isOpen());
  }

  async setProject(pid) {
    if (pid === this.projectId) return;
    this.projectId = pid;
    this.messages = [];
    this.chips = [];
    this.loaded = false;
    this.lastAccepted = null;
    this.renderChips();
    mount(this.listEl);
    if (pid && this.isOpen()) await this.load();
  }

  async load() {
    if (!this.projectId) return;
    const pid = this.projectId;
    mount(
        this.listEl,
        h('div.chat-loading', spinner('Loading conversation\u2026')));
    try {
      const {messages} = await get(`/api/projects/${pid}/chat`);
      if (pid !== this.projectId) return;
      this.messages = messages || [];
      this.loaded = true;
      this.renderList();
    } catch (e) {
      mount(this.listEl, h('div.chat-empty', 'Could not load chat.'));
    }
  }

  /**
   * open({text, output_ids, guideline_ids, chips}) — prefill and attach
   * context.
   */
  open({text, chips} = {}) {
    // Outside constitution/review this navigates to the constitution, where
    // the router reopens the panel; chips and text are kept on the instance.
    this.setOpen(true);
    (chips || []).forEach((c) => {
      if (c.type === 'component') {
        this.dismissedSelectionKey = null;
        this.chips = this.chips.filter((x) => x.type !== 'component');
        this.chips.push(c);
      } else if (!this.chips.some((x) => x.type === c.type && x.id === c.id)) {
        this.chips.push(c);
      }
    });
    this.renderChips();
    if (text) {
      this.input.value = this.input.value.trim() ?
          `${this.input.value.trim()}\n\n${text}` :
          text;
      this.autosize();
    }
    setTimeout(() => {
      this.input.focus();
      this.input.setSelectionRange(
          this.input.value.length, this.input.value.length);
    }, 50);
  }

  renderChips() {
    const compChip = this.chips.find((c) => c.type === 'component');
    if (this.input) {
      this.input.placeholder = compChip ?
          `Ask about ${compChip.component} (#${
              compChip
                  .id}) \u2014 e.g. rules, layout, tokens, or component behavior\u2026` :
          'Ask for constitution or design token changes, e.g. \u201cSummarize long lists after 5 items and set card radius to 16px\u201d\u2026';
    }
    mount(
        this.chipsEl,
        this.chips.map(
            (c, i) =>
                h('span.ctx-chip', {
                  class: c.type === 'component' ? 'ctx-chip-comp' : '',
                  title: c.label
                },
                  c.thumb ? h('img', {src: c.thumb, alt: ''}) :
                            icon(
                                c.type === 'output' ?
                                    'image' :
                                    c.type === 'component' ?
                                    'ads_click' :
                                    (c.kind === 'do' ? 'check' : 'block')),
                  h('span.ctx-label', c.label),
                  h('button.ctx-x', {
                    type: 'button',
                    'aria-label': `Remove ${c.label}`,
                    onclick: () => {
                      if (c.type === 'component' && c.output_id) {
                        this.dismissedSelectionKey = `${c.output_id}:${c.id}`;
                      }
                      this.chips.splice(i, 1);
                      this.renderChips();
                    }
                  },
                    icon('close')))));
    this.chipsEl.hidden = !this.chips.length;
  }

  renderList() {
    const items = [];
    const hasRuns = this.hasRuns();
    if (!this.messages.length) {
      const starters = [
        ...(hasRuns ?
                [
                  'Draft a first constitution from the worst outputs of the last run'
                ] :
                []),
        'Tighten the rules about density on phone and reduce card padding to 10px',
        'Update design tokens to a warm editorial serif palette with 14px card radius',
        'What decisions should this constitution cover that it doesn\u2019t yet?',
      ];
      items.push(h(
          'div.chat-empty',
          h('div.chat-empty-title',
            'Draft & revise constitution and design tokens'),
          h('p',
            'Describe what went wrong in an output, how the generator should decide in context, or what design tokens (colors, radii, typography, spacing, custom tokens) to update. Proposals arrive with diffs you can accept in one click.'),
          h('p.muted',
            'Tip: you can ask for constitution changes, design token changes, or both at once.'),
          h('div.chat-starters',
            starters.map(
                (t) =>
                    h('button.link-btn', {
                      type: 'button',
                      onclick: () => {
                        this.input.value = t;
                        this.autosize();
                        this.input.focus();
                      }
                    },
                      t)))));
    }
    this.messages.forEach((m) => items.push(this.renderMessage(m)));
    if (this.busy)
      items.push(
          h('div.msg.msg-assistant.thinking#chat-thinking',
            h('div.msg-body', h('span.dots', h('i'), h('i'), h('i')),
              h('span.muted',
                'Thinking through constitution & design tokens\u2026'))));
    if (this.lastAccepted && hasRuns) items.push(this.renderRerunOffer());
    mount(this.listEl, items);
    this.listEl.scrollTop = this.listEl.scrollHeight;
    this.applyFocus();
  }

  referenceLink(m) {
    const ctx = m.context || {};
    if (!this.projectId) return null;
    // Import summaries (and legacy reference proposals) link to the import.
    if (ctx.import_id) {
      return h(
          'a.msg-ref-link.msg-import-link', {
            href: `/p/${this.projectId}/imports/${ctx.import_id}`,
            title: 'Open the import this came from'
          },
          icon('move_to_inbox'),
          ctx.import_title ? `Import \u00b7 ${ctx.import_title}` :
                             'From import');
    }
    const ids = ctx.reference_ids || [];
    if (!ids.length) return null;
    return h(
        'a.msg-ref-link', {
          href: `/p/${this.projectId}/imports/${ids[0]}`,
          title: 'Open the import (design reference) this came from'
        },
        icon('move_to_inbox'),
        ids.length > 1 ? `From ${ids.length} imports` : 'From import');
  }

  renderMessage(m) {
    const ctx = m.context || {};
    const selComp = ctx.selected_component && ctx.selected_component.id ?
        ctx.selected_component :
        null;
    const attachedImgs = ctx.attached_images || [];
    const ctxCount = (ctx.output_ids || []).length +
        (ctx.guideline_ids || []).length + attachedImgs.length +
        (selComp ? 1 : 0);
    if (m.role === 'user') {
      return h(
          'div.msg.msg-user', {dataset: {mid: m.id}},
          h('div.msg-body', renderMarkdown(m.text_md)), this.referenceLink(m),
          attachedImgs.length ?
              h('div.imagery-ref-thumbs',
                attachedImgs.map((r) => h('img.imagery-ref-thumb', {
                                   src: r.url || r.data_url,
                                   alt: r.name || 'Reference image',
                                   title: r.name || 'Reference image',
                                 }))) :
              null,
          ctxCount ?
              h('div.msg-ctx', icon('attach_file'),
                [
                  selComp ?
                      `${selComp.component || 'Component'} #${selComp.id}` :
                      null,
                  attachedImgs.length ?
                      `${attachedImgs.length} style image${
                          attachedImgs.length > 1 ? 's' : ''}` :
                      null,
                  (ctx.output_ids || []).length ?
                      `${ctx.output_ids.length} output${
                          ctx.output_ids.length > 1 ? 's' : ''}` :
                      null,
                  (ctx.guideline_ids || []).length ?
                      `${ctx.guideline_ids.length} do/don\u2019t` :
                      null
                ].filter(Boolean)
                    .join(' · ')) :
              null);
    }
    const parts =
        [this.referenceLink(m), h('div.msg-body', renderMarkdown(m.text_md))];
    if (m.out_of_scope && m.out_of_scope.length) {
      parts.push(h(
          'div.callout.callout-scope',
          h('div.callout-title', icon('tune'),
            'Better outside the constitution'),
          h('ul', m.out_of_scope.map((o) => {
            const stc = o.suggested_theme_change;
            const hasToken =
                stc && stc.token && stc.value != null && stc.value !== '';
            return h(
                'li',
                h('strong', o.rule),
                ' \u2014 ',
                o.why,
                ' ',
                h('span.chip.home',
                  `\u2192 ${HOME_LABEL[o.suggested_home] || o.suggested_home}`),
                hasToken ?
                    h('button.btn.btn-sm.btn-ghost', {
                      type: 'button',
                      style: {marginLeft: '6px'},
                      onclick: async (e) => {
                        const b = e.currentTarget;
                        b.disabled = true;
                        await this.applySingleToken(stc.token, stc.value);
                        b.replaceChildren(icon('check'), 'Applied');
                      },
                    },
                      icon('palette'), `Set ${stc.token} = ${stc.value}`) :
                    null,
            );
          }))));
    }
    if (m.proposal) parts.push(this.renderProposal(m));
    return h(
        'div.msg.msg-assistant', {dataset: {mid: m.id}}, parts,
        h('div.msg-meta', relTime(m.created_at)));
  }

  renderProposal(m) {
    const p = m.proposal;
    const st = m.proposal_status;
    const curNum = store.project && store.project.current_revision ?
        store.project.current_revision.number :
        null;
    const nextNum = curNum != null ? curNum + 1 : 'N+1';

    const hasCons = p.has_constitution_change !== undefined ?
        Boolean(p.has_constitution_change) :
        Boolean(p.constitution_md && p.diff);
    const themeChanges = Array.isArray(p.theme_changes) ? p.theme_changes : [];
    const hasTheme = themeChanges.length > 0 || Boolean(p.custom_css);
    const imageryChanges =
        Array.isArray(p.imagery_changes) ? p.imagery_changes : [];
    const hasImagery = imageryChanges.length > 0;
    const cc = p.catalog_changes || {};
    const ccEnable = Array.isArray(cc.enable) ? cc.enable : [];
    const ccDisable = Array.isArray(cc.disable) ? cc.disable : [];
    const ccProposed =
        Array.isArray(cc.proposed_components) ? cc.proposed_components : [];
    const hasCatalog =
        ccEnable.length + ccDisable.length + ccProposed.length > 0;

    const diffBox = (hasCons && p.diff) ?
        h('div.proposal-diff', diffView(p.diff, {
            aLabel: 'Current',
            bLabel: 'Proposed',
            compact: true,
            context: 2,
          })) :
        null;

    const themeBox = hasTheme ?
        h('div.proposal-tokens',
          h('div.proposal-tokens-head', icon('palette'),
            h('span', `Design token changes (${themeChanges.length})`)),
          h('div.proposal-tokens-list',
            themeChanges.map(
                (tc) => h(
                    'div.proposal-token-row',
                    h('div.proposal-token-key',
                      h('code.token-key-name', tc.token),
                      tc.is_custom ? h('span.chip.chip-xs', 'custom') : null,
                      tc.maps_to ?
                          h('span.chip.chip-xs', `\u2192 ${tc.maps_to}`) :
                          null,
                      tc.confidence ? h('span.chip.chip-xs.conf', {
                        class: `conf-${tc.confidence}`,
                        title: tc.confidence === 'exact' ?
                            'Read exactly from the design source' :
                            'Approximated from the design (e.g. sampled from a screenshot)'
                      },
                                        tc.confidence) :
                                      null),
                    h('div.proposal-token-vals', renderTokenValue(tc.before),
                      h('span.muted', '\u2192'), renderTokenValue(tc.after)),
                    tc.reason ?
                        h('div.proposal-token-reason.muted', tc.reason) :
                        null)))) :
        null;

    const catalogBox = hasCatalog ?
        h('div.proposal-catalog',
          h('div.proposal-tokens-head', icon('widgets'),
            h('span', 'Catalog changes')),
          ccEnable.length || ccDisable.length ?
              h('div.cc-chips',
                ccEnable.map(
                    (n) =>
                        h('span.chip.cc-enable', {title: 'Enable'}, icon('add'),
                          n)),
                ccDisable.map(
                    (n) =>
                        h('span.chip.cc-disable', {title: 'Disable'},
                          icon('remove'), n))) :
              null,
          ccProposed.length ?
              h('div.cc-proposed',
                ccProposed.map(
                    (c) => h(
                        'div.cc-comp',
                        h('div.cc-comp-head', h('code.token-key-name', c.name),
                          h('span.chip.chip-xs', 'proposed')),
                        c.purpose ? h('div.cc-comp-purpose', c.purpose) : null,
                        (c.maps_to_basic || []).length ?
                            h('div.cc-maps.muted.small', 'Maps to ',
                              c.maps_to_basic.map(
                                  (b) =>
                                      h('span.chip.chip-xs.chip-quiet', b))) :
                            null,
                        c.props_schema && Object.keys(c.props_schema).length ?
                            h('details.cc-schema',
                              h('summary.small', 'Props schema'),
                              jsonTree(c.props_schema, {collapseDepth: 2})) :
                            null)),
                h('p.help.small.cc-design-link',
                  st === 'accepted' ?
                      'Accepted as draft components. ' :
                      'Accepting creates draft components; the generator doesn\u2019t use them until you activate them. ',
                  h('a', {href: `/p/${this.projectId}/design?tab=components`},
                    'Build in Design system \u2192'))) :
              null) :
        null;
    const imageryBox = hasImagery ?
        h('div.proposal-tokens.proposal-imagery',
          h('div.proposal-tokens-head', icon('add_photo_alternate'),
            h('span',
              `Imagery & SVG style presets (${imageryChanges.length})`)),
          h('div.proposal-tokens-list',
            imageryChanges.map(
                (ic) => h(
                    'div.proposal-token-row',
                    h('div.proposal-token-key', h('strong', ic.name || ic.slug),
                      h('code.token-key-name', `style=${ic.slug}`),
                      h('span.chip.chip-xs', ic.medium || 'raster_image'),
                      ic.target_slot ?
                          h('span.chip.chip-xs.chip-quiet', ic.target_slot) :
                          null,
                      ic.use_attached_references ?
                          h('span.chip.chip-xs', '+ reference image(s)') :
                          null),
                    ic.incorporation ?
                        h('div.proposal-token-reason',
                          h('strong', 'Incorporate: '), ic.incorporation) :
                        null,
                    ic.style_text ? h('div.proposal-token-reason.muted',
                                      h('strong', 'Style: '), ic.style_text) :
                                    null)))) :
        null;

    const applyCatalog = hasCatalog ?
        h('input',
          {type: 'checkbox', id: `proposal-catalog-${m.id}`, checked: true}) :
        null;
    let acceptLabel = `Accept as rev ${nextNum}`;
    if (hasCons && hasImagery) {
      acceptLabel =
          `Accept rev ${nextNum} + ${imageryChanges.length} imagery style${
              imageryChanges.length === 1 ? '' : 's'}`;
    } else if (hasCons && hasTheme) {
      acceptLabel = `Accept rev ${nextNum} + ${themeChanges.length} token${
          themeChanges.length === 1 ? '' : 's'}`;
    } else if (!hasCons && hasImagery) {
      acceptLabel = `Save ${imageryChanges.length} imagery style${
          imageryChanges.length === 1 ? '' : 's'}`;
    } else if (!hasCons && hasTheme) {
      acceptLabel = `Apply ${themeChanges.length} token change${
          themeChanges.length === 1 ? '' : 's'}`;
    } else if (!hasCons && hasCatalog) {
      acceptLabel = 'Apply catalog changes';
    }

    let headTitle = 'Proposed revision';
    if (hasCons && hasImagery)
      headTitle = 'Proposed revision & imagery style';
    else if (hasCons && hasTheme)
      headTitle = 'Proposed revision & design tokens';
    else if (!hasCons && hasImagery)
      headTitle = 'Proposed imagery & icon style';
    else if (!hasCons && hasTheme)
      headTitle = 'Proposed design token changes';
    else if (!hasCons && hasCatalog)
      headTitle = 'Proposed catalog changes';
    if (hasCatalog && (hasCons || hasTheme || hasImagery))
      headTitle += ' + catalog';

    const actions = [];
    if (st === 'pending' || st === 'none' || !st) {
      actions.push(
          h('button.btn.btn-primary.btn-sm', {
            type: 'button',
            id: `proposal-accept-${m.id}`,
            class: 'proposal-accept',
            onclick: (e) => this.accept(m, null, e.currentTarget, {
              applyCatalog: applyCatalog ? applyCatalog.checked : undefined
            }),
          },
            acceptLabel));
      if (hasCons) {
        actions.push(
            h('button.btn.btn-ghost.btn-sm', {
              type: 'button',
              id: `proposal-edit-${m.id}`,
              class: 'proposal-edit',
              onclick: () => this.editThenAccept(m),
            },
              'Edit then accept'));
      }
      actions.push(
          h('button.btn.btn-quiet.btn-sm', {
            type: 'button',
            id: `proposal-discard-${m.id}`,
            class: 'proposal-discard',
            onclick: (e) => this.discard(m, e.currentTarget),
          },
            'Discard'));
      if (applyCatalog && (hasCons || hasTheme))
        actions.push(
            h('label.check.small.proposal-catalog-toggle', applyCatalog,
              'Apply catalog changes'));
    } else if (st === 'accepted') {
      actions.push(
          h('span.status.s-done', h('span.dot'),
            hasCons && hasTheme ? 'Accepted revision & tokens' :
                hasTheme        ? 'Tokens applied' :
                                  'Accepted'));
    } else if (st === 'discarded') {
      actions.push(h('span.status.s-muted', h('span.dot'), 'Discarded'));
    }
    const headIcon = icon(
        hasTheme && !hasCons       ? 'palette' :
            !hasCons && hasCatalog ? 'widgets' :
                                     'difference');
    if (st === 'accepted' || st === 'discarded') {
      // Collapse resolved proposals to a one-line summary; details on demand.
      const expanded = this.expandedProposals.has(m.id);
      const toggle = () => {
        if (expanded)
          this.expandedProposals.delete(m.id);
        else
          this.expandedProposals.add(m.id);
        // renderList() pins to the bottom; keep the reader where they were.
        const top = this.listEl.scrollTop;
        this.renderList();
        this.listEl.scrollTop = top;
      };
      return h(
          'div.proposal.p-collapsible',
          {class: `p-${st} ${expanded ? 'is-expanded' : 'is-collapsed'}`},
          h('button.proposal-head.proposal-toggle', {
            type: 'button',
            id: `proposal-toggle-${m.id}`,
            'aria-expanded': String(expanded),
            title: expanded ? 'Hide details' : 'Show details',
            onclick: toggle,
          },
            headIcon, h('span.proposal-head-title', headTitle), actions[0],
            icon(expanded ? 'expand_less' : 'expand_more')),
          p.change_summary ? h('p.proposal-summary', p.change_summary) : null,
          expanded && (p.changed_sections || []).length ?
              h('div.proposal-sections',
                p.changed_sections.map((s) => h('span.chip', s))) :
              null,
          expanded ? [themeBox, imageryBox, catalogBox, diffBox] : null);
    }
    return h(
        'div.proposal', {class: `p-${st || 'pending'}`},
        h('div.proposal-head', headIcon, h('span', headTitle)),
        p.change_summary ? h('p.proposal-summary', p.change_summary) : null,
        (p.changed_sections || []).length ?
            h('div.proposal-sections',
              p.changed_sections.map((s) => h('span.chip', s))) :
            null,
        themeBox, imageryBox, catalogBox, diffBox,
        h('div.proposal-actions', actions));
  }

  renderRerunOffer() {
    const rev = this.lastAccepted.revision;
    const tcCount = this.lastAccepted.themeChangesCount || 0;
    const hasCons = this.lastAccepted.hasConstitutionChange;
    const titleText = hasCons && tcCount ?
        `Rev ${rev.number} & ${tcCount} token change${
            tcCount === 1 ? '' : 's'} applied.` :
        (!hasCons && tcCount) ?
        `${tcCount} design token${tcCount === 1 ? '' : 's'} updated.` :
        `Rev ${rev.number} is now current.`;
    return h(
        'div.rerun-offer#rerun-offer', icon('replay'),
        h('div', h('strong', titleText),
          h('div.muted',
            'Re-run the last run\u2019s samples to see the updated output across renderers.')),
        h('button.btn.btn-primary.btn-sm#rerun-last', {
          type: 'button',
          onclick: (e) => this.rerunLast(rev, e.currentTarget)
        },
          hasCons ? 'Re-run on new revision' : 'Re-run with new tokens'),
        h('button.icon-btn', {
          'aria-label': 'Dismiss',
          onclick: () => {
            this.lastAccepted = null;
            this.renderList();
          }
        },
          icon('close')));
  }

  async rerunLast(rev, btn) {
    btn.disabled = true;
    try {
      const {runs} = await get(`/api/projects/${this.projectId}/runs`);
      const last =
          (runs || []).find((r) => r.revision_id !== rev.id) || (runs || [])[0];
      if (!last) {
        toast('No previous run to repeat \u2014 start one from Runs.', 'warn');
        navigate(`/p/${this.projectId}/runs`);
        return;
      }
      const body = {
        revision_id: rev.id,
        viewports: last.viewports,
        judge: last.config ? last.config.judge !== false : true
      };
      if (last.sample_set_id)
        body.sample_set_id = last.sample_set_id;
      else if (last.config && last.config.sample_ids)
        body.sample_ids = last.config.sample_ids;
      if (!(await confirmRunWarnings(this.projectId, {revisionId: rev.id})))
        return;
      const {run} = await post(`/api/projects/${this.projectId}/runs`, body);
      this.lastAccepted = null;
      this.renderList();
      toast(`Run started on rev ${rev.number}`, 'good');
      navigate(`/p/${this.projectId}/runs/${run.id}?compare_with=${last.id}`);
    } finally {
      btn.disabled = false;
    }
  }

  async applySingleToken(token, value) {
    const {settings} = await get(`/api/projects/${this.projectId}/settings`);
    settings.theme = {...(settings.theme || {}), [token]: value};
    const res =
        await put(`/api/projects/${this.projectId}/settings`, {settings});
    if (store.project && res.settings) store.project.settings = res.settings;
    emit(
        'settings-changed',
        {settings: res.settings || settings, source: 'chat'});
    toast(`Theme ${token} set to ${value}`, 'good');
  }

  async accept(m, constitutionMd, btn, {applyCatalog} = {}) {
    if (btn) btn.disabled = true;
    try {
      const body =
          constitutionMd != null ? {constitution_md: constitutionMd} : {};
      if (applyCatalog !== undefined) body.apply_catalog = !!applyCatalog;
      const res = await post(
          `/api/projects/${this.projectId}/chat/${m.id}/accept`, body);
      const revision = res.revision;
      const appliedTokens = res.theme_changes_applied || [];
      const appliedImagery = res.imagery_changes_applied || [];
      const appliedCatalog = res.catalog_changes_applied;
      const catalogCount = !appliedCatalog ?
          0 :
          Array.isArray(appliedCatalog) ?
          appliedCatalog.length :
          typeof appliedCatalog === 'object' ?
          Object.values(appliedCatalog)
              .map((v) => (Array.isArray(v) ? v.length : 0))
              .reduce((a, b) => a + b, 0) :
          Number(appliedCatalog) || 0;
      const hasCons = res.has_constitution_change !== undefined ?
          Boolean(res.has_constitution_change) :
          Boolean(m.proposal?.constitution_md);

      m.proposal_status = 'accepted';
      m.accepted_revision_id = revision ? revision.id : null;
      this.lastAccepted = {
        revision,
        themeChangesCount: appliedTokens.length,
        hasConstitutionChange: hasCons,
      };
      if (store.project) {
        if (revision) store.project.current_revision = revision;
        if (res.settings) store.project.settings = res.settings;
      }
      if (hasCons && revision) {
        emit('revision-changed', {revision, source: 'chat'});
      }
      if (appliedTokens.length || res.settings) {
        emit('settings-changed', {
          settings: res.settings,
          theme_changes_applied: appliedTokens,
          source: 'chat',
        });
      }
      if (hasCons && appliedTokens.length) {
        toast(
            `Accepted as rev ${revision.number} + ${
                appliedTokens.length} token change(s)`,
            'good');
      } else if (!hasCons && appliedTokens.length) {
        toast(`Applied ${appliedTokens.length} design token change(s)`, 'good');
      } else if (revision) {
        toast(`Accepted as rev ${revision.number}`, 'good');
      }
      if (appliedImagery.length) {
        emit('imagery-changed', {styles: appliedImagery, source: 'chat'});
        toast(
            `Saved ${appliedImagery.length} imagery/icon style preset${
                appliedImagery.length === 1 ? '' : 's'}`,
            'good');
      }
      if (catalogCount) {
        toast(
            `Applied ${catalogCount} catalog change${
                catalogCount === 1 ? '' : 's'}`,
            'good');
      }
      this.renderList();
      return revision;
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  editThenAccept(m) {
    store.pendingEdit = {
      message_id: m.id,
      constitution_md: m.proposal.constitution_md,
      change_summary: m.proposal.change_summary
    };
    emit('pending-edit', store.pendingEdit);
    if (!location.pathname.match(/\/p\/[^/]+\/(constitution)?$/))
      navigate(`/p/${this.projectId}/constitution`);
  }

  async discard(m, btn) {
    btn.disabled = true;
    try {
      await post(`/api/projects/${this.projectId}/chat/${m.id}/discard`);
      m.proposal_status = 'discarded';
      this.renderList();
    } finally {
      btn.disabled = false;
    }
  }

  setBusy(b) {
    this.busy = b;
    this.sendBtn.disabled = b;
    this.input.disabled = b;
    this.sendBtn.replaceChildren(b ? h('span.spinner') : icon('arrow_upward'));
  }

  async send() {
    const text = this.input.value.trim();
    if (!text || this.busy || !this.projectId) return;
    const pid = this.projectId;
    let compChip = this.chips.find((c) => c.type === 'component');
    if (!compChip && this.view === 'review' && store.reviewSelection &&
        store.reviewSelection.component && store.reviewSelection.output) {
      const out = store.reviewSelection.output;
      const comp = store.reviewSelection.component;
      const selKey = `${out.id}:${comp.id}`;
      if (this.dismissedSelectionKey !== selKey) {
        compChip = {
          type: 'component',
          id: comp.id,
          output_id: out.id,
          component: comp.component,
          props: comp,
        };
      }
    }
    const outputIds =
        this.chips.filter((c) => c.type === 'output').map((c) => c.id);
    if (compChip && compChip.output_id &&
        !outputIds.includes(compChip.output_id)) {
      outputIds.unshift(compChip.output_id);
    }
    const selectedComponent = compChip ? {
      id: compChip.id,
      component: compChip.component,
      output_id: compChip.output_id || '',
      props: compChip.props || null,
    } :
                                         null;
    const attachedImages =
        this.chips.filter((c) => c.type === 'image' && c.data_url)
            .map((c) => ({
                   data_url: c.data_url,
                   name: c.name || 'reference.png',
                   caption: c.caption || '',
                 }));
    const body = {
      text,
      output_ids: outputIds,
      guideline_ids:
          this.chips.filter((c) => c.type === 'guideline').map((c) => c.id),
    };
    if (selectedComponent) body.selected_component = selectedComponent;
    if (attachedImages.length) body.attached_images = attachedImages;
    const draft = store.draftProvider ? store.draftProvider() : null;
    if (draft != null) body.draft_md = draft;
    // Optimistic user message.
    const temp = {
      id: `tmp-${Date.now()}`,
      role: 'user',
      text_md: text,
      context: {
        output_ids: body.output_ids,
        guideline_ids: body.guideline_ids,
        selected_component: selectedComponent,
        attached_images: attachedImages,
      }
    };
    this.messages.push(temp);
    const savedInput = this.input.value;
    const savedChips = this.chips;
    this.input.value = '';
    this.chips = this.chips.filter((c) => c.fromSelection);
    this.renderChips();
    this.autosize();
    this.setBusy(true);
    this.renderList();
    try {
      const res = await post(`/api/projects/${pid}/chat`, body);
      if (pid !== this.projectId) return;
      const i = this.messages.indexOf(temp);
      this.messages.splice(i, 1, res.user_message, res.assistant_message);
    } catch (e) {
      const i = this.messages.indexOf(temp);
      if (i >= 0) this.messages.splice(i, 1);
      this.input.value = savedInput;
      this.chips = savedChips;
      this.renderChips();
    } finally {
      this.setBusy(false);
      this.renderList();
    }
  }
}

/**
 * Convenience: open chat with an output (and optional selected component)
 * attached.
 */
export function sendOutputToChat(output, text, component = null) {
  const chips = [{
    type: 'output',
    id: output.id,
    label: `${output.sample_title || 'Output'} · ${output.viewport}`,
    thumb: output.thumb_url || null
  }];
  if (component && component.id) {
    chips.push({
      type: 'component',
      id: component.id,
      output_id: output.id,
      component: component.component,
      props: component,
      label: `${component.component} · #${component.id}`,
    });
  }
  emit('chat-open', {
    text: text || '',
    chips,
  });
}
export function sendGuidelineToChat(g, text) {
  emit('chat-open', {
    text: text || '',
    chips: [{
      type: 'guideline',
      id: g.id,
      kind: g.kind,
      label: `${g.kind === 'do' ? 'Do' : 'Don\u2019t'}: ${g.title}`,
      thumb: (g.screenshots && g.screenshots[0] && g.screenshots[0].url) || null
    }],
  });
}
