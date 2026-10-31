// Immersive teaching mode: the input-data and observation panes disappear;
// the same live card fills the screen (CSS-scaled iframe, so click-to-point
// keeps working), and the designer talks (or types) about what they point at.
// Every final utterance goes to POST /api/teach/{sid}/voice_turn; Gemini
// authors the intent, polarity and anchors, which are shown as authoritative
// ("Gemini heard", tap to change) — nothing is inferred from words here.
import {del, post} from '../components/api.js';
import {compLabel} from '../components/datatree.js';
import {h, icon, mount, spinner} from '../components/dom.js';
import {drawer, toast} from '../components/ui.js';
import {bindPushToTalk, Voice} from '../components/voice.js';

import {anchorChip} from './teach.js';

const CARD_W = 412;
const BUSY = new Set(
    ['queued', 'generating', 'rendering', 'judging', 'running', 'pending']);
const FOLD = 760;

function isTyping(t) {
  return t &&
      (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
       t.tagName === 'SELECT' || t.isContentEditable);
}

/**
 * mountImmersive(host, api) → {destroy, onState, onCard}
 * `api` is provided by the Teach workspace (state access, links, crops,
 * applyState/refresh, distill, renderLearnedInto, templateFrom, exit).
 */
export function mountImmersive(host, api) {
  let alive = true;
  let iframe = null;
  let links = null;
  let scale = 1.45;
  let frameH = 900;
  let selectedKey = null;
  let hoverKey = null;
  let pending = null;  // {polarity, text, anchors} authored by Gemini
  let openQuestion = null;
  let quickReplies = [];
  let say = '';
  let captured = [];
  let typing = false;
  let listening = false;
  let finalText = '';
  let interim = '';
  let turnBusy = false;
  let turnQueue = Promise.resolve();
  let speakOn = true;
  let learnedDrawer = null;
  let drawerDistillBtn = null;
  let stageSig = '';
  let micError = '';

  const voice = new Voice({
    onInterim: (t) => {
      interim = t;
      drawTranscript();
    },
    onFinal: (t) => {
      finalText = finalText ? `${finalText} ${t}` : t;
      interim = '';
      drawTranscript();
      sendTurn(t, {source: 'voice'});
    },
    onState: (on) => {
      listening = on;
      if (on) {
        finalText = '';
        interim = '';
      }
      drawBar();
    },
    onError: (msg) => {
      micError = msg;
      toast(msg, 'warn');
      drawBar();
    },
  });
  const unbindPtt = bindPushToTalk(
      voice,
      {enabled: () => alive && !typing && !document.querySelector('.overlay')});

  const root =
      h('div.imm#teach-immersive',
        {role: 'dialog', 'aria-label': 'Immersive teaching'});
  const topEl = h('div.imm-top');
  const stageEl = h('div.imm-stage');
  const barWrap = h('div.imm-bar-wrap');
  const phoneEl = h('div.imm-phone#imm-phone');
  const scrollEl = h('div.imm-scrollbox');
  const frameBox = h('div.imm-framebox');
  const overlayEl = h('div.imm-overlay');
  const tipEl = h('div.imm-tip', {hidden: true});
  const capEl = h('div.imm-captured#imm-captured');
  const assistEl = h('div.imm-assist#imm-assist');
  const typeIn = h('input.input.imm-type#imm-type', {
    placeholder: 'Type what you see \u2014 Enter sends it to Gemini',
    onkeydown: (e) => {
      if (e.key === 'Enter' && typeIn.value.trim()) {
        e.preventDefault();
        const t = typeIn.value.trim();
        typeIn.value = '';
        finalText = t;
        drawTranscript();
        sendTurn(t, {source: 'typed'});
      } else if (e.key === 'Escape') {
        e.preventDefault();
        typeIn.blur();
      }
    }
  });
  scrollEl.appendChild(frameBox);
  phoneEl.append(scrollEl, h('span.imm-scroll#imm-scrollpos'));
  stageEl.append(
      h('button.imm-arrow.l', {
        type: 'button',
        'aria-label': 'Previous card',
        onclick: () => api.setIndex(api.getIndex() - 1)
      },
        icon('chevron_left'), h('small', 'Previous \u00b7 \u2190')),
      h('button.imm-arrow.r', {
        type: 'button',
        'aria-label': 'Next card',
        onclick: () => api.setIndex(api.getIndex() + 1)
      },
        icon('chevron_right'), h('small', 'Next \u00b7 \u2192')),
      phoneEl, tipEl, capEl, assistEl);
  root.append(topEl, stageEl, barWrap);
  host.appendChild(root);
  scrollEl.addEventListener('scroll', () => {
    drawTip();
    drawScrollPos();
  });

  window.addEventListener('message', onMessage);
  document.addEventListener('keydown', onKey);
  window.addEventListener('resize', onResize);

  drawTop();
  buildStage();
  drawAssist();
  drawCaptured();
  drawBar();

  // ---------------------------------------------------------------- helpers
  function card() {
    return api.card();
  }
  function cardSig() {
    const c = card();
    const o = (c && c.output) || {};
    return c ? [c.output_id, o.status, o.render_url].join('|') : '';
  }
  function postFrame(msg) {
    if (iframe && iframe.contentWindow)
      iframe.contentWindow.postMessage(msg, '*');
  }
  function pointedAnchors() {
    const out = [];
    for (const k of [selectedKey, hoverKey]) {
      if (!k || out.some((a) => a.key === k)) continue;
      const c = api.compFor(k, links);
      const bar = k.indexOf('|');
      out.push({
        kind: 'component',
        key: k,
        id: bar < 0 ? k : k.slice(0, bar),
        scope: bar < 0 ? '' : k.slice(bar + 1),
        component: c ? c.type : undefined,
        label: c ? compLabel(c) : undefined,
        why: k === selectedKey ? 'pointed' : 'hovered',
      });
    }
    return out;
  }

  // ----------------------------------------------------------------- stage
  function computeScale() {
    const availW = window.innerWidth - 2 * 380;
    const availH =
        (stageEl.clientHeight || (window.innerHeight - 52 - 190)) - 24;
    scale = Math.max(0.9, Math.min(1.45, availW / CARD_W));
    phoneEl.style.width = `${Math.round(CARD_W * scale)}px`;
    phoneEl.style.height =
        `${Math.max(360, Math.min(Math.round(frameH * scale), availH))}px`;
    frameBox.style.width = `${CARD_W * scale}px`;
    frameBox.style.height = `${frameH * scale}px`;
    if (iframe) iframe.style.transform = `scale(${scale})`;
  }

  function buildStage() {
    stageSig = cardSig();
    const c = card();
    iframe = null;
    links = null;
    selectedKey = null;
    hoverKey = null;
    tipEl.hidden = true;
    overlayEl.replaceChildren();
    const o = (c && c.output) || {};
    frameH = (o.render_meta && o.render_meta.height) || 900;
    if (!c) {
      mount(frameBox, h('div.imm-wait', 'No cards.'));
    } else if (BUSY.has(o.status) || !o.render_url) {
      mount(
          frameBox,
          h('div.imm-wait',
            BUSY.has(o.status) ? spinner('Rendering\u2026') :
                                 (o.error || 'Not rendered')));
    } else {
      const src =
          `${o.render_url}${o.render_url.includes('?') ? '&' : '?'}inspect=1`;
      iframe = h('iframe.live-frame#imm-live', {
        src,
        title: 'Live card (immersive)',
        style: {
          width: `${CARD_W}px`,
          height: `${frameH}px`,
          transformOrigin: '0 0',
          position: 'absolute',
          left: 0,
          top: 0
        }
      });
      mount(frameBox, iframe, overlayEl);
      const oid = c.output_id;
      api.linksFor(oid)
          .then((L) => {
            if (!alive || !card() || card().output_id !== oid) return;
            links = L;
            if (L && L.height) {
              frameH = L.height;
              computeScale();
            }
            drawHeard();
          })
          .catch(() => {});
    }
    scrollEl.scrollTop = 0;
    computeScale();
    drawScrollPos();
  }

  function drawScrollPos() {
    const el = phoneEl.querySelector('#imm-scrollpos');
    if (!el) return;
    el.textContent = `scrolled to ${Math.round(scrollEl.scrollTop / scale)} / ${
        Math.round(frameH)} px`;
  }

  function onMessage(e) {
    const d = e.data;
    if (!d || typeof d !== 'object' || !iframe ||
        e.source !== iframe.contentWindow)
      return;
    if (d.type === 'a2ui-ready') {
      const hh = d.height || (d.ready && d.ready.height);
      if (hh) {
        frameH = Math.ceil(hh);
        iframe.style.height = `${frameH}px`;
        computeScale();
      }
      pushSelect();
      drawHeard();
    } else if (d.type === 'a2ui-hover') {
      hoverKey = d.id ? `${d.id}|${d.scope || ''}` : null;
      drawTip();
    } else if (d.type === 'a2ui-click') {
      selectedKey = d.id ? `${d.id}|${d.scope || ''}` : null;
      pushSelect();
      drawTip();
    }
  }

  function pushSelect() {
    postFrame({
      type: 'a2ui-highlight',
      ids: selectedKey ? [selectedKey] : [],
      select: true,
      scroll: false
    });
  }

  function drawHeard() {
    overlayEl.replaceChildren();
    if (!pending || !links) return;
    const keys = api.anchorKeys(pending.anchors || [], links)
                     .filter((k) => k !== selectedKey);
    const boxes = [];
    keys.forEach((k) => api.boxesFor(k, links).forEach((b) => boxes.push(b)));
    for (const [x, y, w, hh] of boxes.slice(0, 40)) {
      overlayEl.appendChild(h('div.tov.heard', {
        style: {
          left: `${x * scale}px`,
          top: `${y * scale}px`,
          width: `${w * scale}px`,
          height: `${hh * scale}px`
        }
      }));
    }
    const heard =
        (pending.anchors || []).find((a) => a.why && a.why !== 'pointed');
    if (boxes.length && heard) {
      const [x, y] = boxes[0];
      overlayEl.appendChild(
          h('div.tov-tag.hov', {
            style: {
              left: `${x * scale}px`,
              top: `${Math.max(0, y * scale - 20)}px`,
              background: 'var(--tint-7-ink)'
            }
          },
            `heard ${heard.why} \u2192 ${
                String(heard.path || heard.key).replace(/\|$/, '')} \u00b7 ${
                keys.length} component${keys.length === 1 ? '' : 's'}`));
    }
  }

  function drawTip() {
    const key = selectedKey || hoverKey;
    if (!key || !links) {
      tipEl.hidden = true;
      return;
    }
    const c = api.compFor(key, links);
    if (!c) {
      tipEl.hidden = true;
      return;
    }
    const reads = [...(c.reads || []), ...(c.literal || [])];
    const val = (p) => {
      const l = (links.leaves || []).find((x) => x.path === p);
      return l && l.value_preview != null && l.value_preview !== '' ?
          ` = ${l.value_preview}` :
          '';
    };
    tipEl.classList.toggle('light', !selectedKey);
    mount(
        tipEl, h('b', compLabel(c)),
        reads.length ? h('span', 'reads ',
                         reads.slice(0, 3).map(
                             (p, i) => [i ? ', ' : '', h('code', p), val(p)])) :
                       h('span', 'reads no input data'),
        c.box ?
            h('span',
              `rendered at y ${c.box.y} of ${
                  Math.round(links.height || frameH)}${
                  c.box.y > FOLD ? ' \u00b7 below the fold on phone' : ''}`) :
            null);
    const pr = phoneEl.getBoundingClientRect();
    const sr = stageEl.getBoundingClientRect();
    const y = c.box ? c.box.y * scale - scrollEl.scrollTop : 40;
    tipEl.style.left = `${pr.right - sr.left + 14}px`;
    tipEl.style.top = `${
        Math.max(8, Math.min(pr.bottom - sr.top - 70, pr.top - sr.top + y))}px`;
    tipEl.hidden = false;
  }

  // ------------------------------------------------------------ top + side
  function drawTop() {
    const st = api.getState();
    const cards = st.cards || [];
    const idx = api.getIndex();
    const c = card();
    const t = c && c.teaching_id ? api.teachById(c.teaching_id) : null;
    const open =
        (st.observations || []).filter((o) => o.status === 'open').length;
    const learned =
        (st.teachings || [])
            .filter((x) => x.status !== 'dismissed' && x.status !== 'merged')
            .length;
    const capturedHere = c ? (st.observations || [])
                                 .filter((o) => o.output_id === c.output_id)
                                 .length :
                             0;
    const distilling = api.isDistilling ? api.isDistilling() : false;
    const canDistill = api.canDistill ? api.canDistill() : open > 0;
    const distillN =
        (api.distillingCount ? api.distillingCount() : open) || open;
    mount(
        topEl,
        h('button.imm-pill#imm-exit', {
          type: 'button',
          title:
              'Expert mode: show the input data, all observations and the learned rules side by side',
          onclick: () => api.exit()
        },
          icon('tune'), 'Expert mode ', h('kbd', 'I')),
        h('button.imm-pill#imm-type-toggle',
          {type: 'button', onclick: () => setTyping(!typing)},
          icon(typing ? 'mic' : 'keyboard'),
          typing ? 'Talk instead ' : 'Type instead ', h('kbd', 'T')),
        h('span.spacer'),
        h('span.imm-title',
          h('span.imm-dots',
            cards.map(
                (cc, i) => h('i', {
                  class: [
                    i === idx ? 'on' : '', cc.origin === 'probe' ? 'probe' : '',
                    i !== idx && cc.observation_count ? 'done' : ''
                  ],
                  title: (cc.sample && cc.sample.title) || '',
                  onclick: () => api.setIndex(i)
                }))),
          c ? `${idx + 1} \u00b7 ${(c.sample && c.sample.title) || ''}` : '',
          c ? h('span.muted', {style: 'font-weight:400'},
                ` \u00b7 ${
                    c.origin === 'probe' ?
                        `Probe${t ? ` ${t.label}` : ''}` :
                        c.origin === 'added' ?
                        'Added' :
                        'Baseline'} \u00b7 ${capturedHere} captured`) :
              null),
        h('span.spacer'),
        h('button.imm-pill#imm-learned', {
          type: 'button',
          title: 'Open learned rules and candidates',
          onclick: () => openLearned(),
        },
          icon('school'), `Learned ${learned}`),
        h('button.imm-pill#imm-distill', {
          type: 'button',
          class: distilling ? 'is-working' : '',
          disabled: !canDistill,
          'aria-busy': String(distilling),
          title: distilling ?
              'Distillation in progress\u2026' :
              open ?
              `Distill ${open} open observation${
                  open === 1 ? '' : 's'} into candidate rules (D)` :
              'All observations distilled \u2014 capture a new observation to distill again',
          onclick: () => api.distill(),
        },
          distilling ?
              [
                spinner('Distilling\u2026'),
                distillN ? h('span.badge-count', {style: 'margin-left:0'},
                             String(distillN)) :
                           null,
              ] :
              [
                icon('auto_awesome'),
                'Distill ',
                open ? h('span.badge-count', {style: 'margin-left:0'},
                         String(open)) :
                       h('span.muted', '0'),
              ]),
        h('button.imm-pill#imm-voice-out', {
          type: 'button',
          class: speakOn ? 'dark' : '',
          title: 'Speak Gemini\u2019s replies',
          onclick: () => {
            speakOn = !speakOn;
            voice.speakEnabled = speakOn;
            if (!speakOn && window.speechSynthesis)
              window.speechSynthesis.cancel();
            drawTop();
          }
        },
          icon(speakOn ? 'graphic_eq' : 'volume_off'),
          speakOn ? 'Voice on' : 'Voice off'),
        h('button.imm-pill.dark#imm-finish', {
          type: 'button',
          title:
              'Finish (F): assemble the constitution from confirmed rules, save Dos & Don\u2019ts and compare Before vs. After',
          onclick: () => api.finish(),
        },
          'Finish ', icon('arrow_forward')));
  }

  function drawAssist() {
    const distilling = api.isDistilling ? api.isDistilling() : false;
    const distillN = api.distillingCount ? api.distillingCount() : 0;
    const distillBubble = distilling ?
        h('div.imm-bubble.working#imm-distill-status',
          {role: 'status', 'aria-live': 'polite'},
          h('div.who', icon('auto_awesome'), 'Gemini'),
          spinner(`Distilling ${
              distillN ? `${distillN} observation${distillN === 1 ? '' : 's'}` :
                         'observations'} into candidate rules\u2026`)) :
        null;
    if (!say && !openQuestion) {
      mount(
          assistEl, distillBubble,
          h('div.imm-bubble.idle', h('div.who', icon('auto_awesome'), 'Gemini'),
            'Point at something and say what\u2019s good or bad about it. I\u2019ll ask if anything is unclear.'));
      return;
    }
    mount(
        assistEl, distillBubble,
        h('div.imm-bubble',
          h('div.who', icon('auto_awesome'),
            speakOn ? 'Gemini \u00b7 spoken' : 'Gemini'),
          say,
          openQuestion && openQuestion !== say ?
              [say ? ' ' : '', h('b', openQuestion)] :
              null),
        quickReplies.length ?
            h('div.imm-replies',
              quickReplies.slice(0, 3).map(
                  (r, i) =>
                      h('button.mini-btn', {
                        type: 'button',
                        onclick: () => sendTurn(r, {source: 'typed'})
                      },
                        r, h('kbd', String(i + 1))))) :
            null);
  }

  function drawCaptured() {
    const items = captured.slice(0, 3).reverse();
    mount(
        capEl,
        ...items.map(
            (o, i) =>
                h('div.imm-cap', {class: i < items.length - 1 ? 'fade' : ''},
                  h('div',
                    h('div.top', icon('check_circle'),
                      `captured \u00b7 ${
                          (o.polarity || 'note')
                              .replace(/^./, (x) => x.toUpperCase())}`,
                      i === items.length - 1 ? h('a.undo', {
                        href: '#',
                        onclick: (e) => {
                          e.preventDefault();
                          undoLast();
                        }
                      },
                                                 'undo') :
                                               null),
                    h('div.txt', o.text),
                    (o.anchors || []).length ?
                        h('div.anchors',
                          o.anchors.map((a) => anchorChip(a, {links}))) :
                        null),
                  h('div.obs-crop', {
                    style: o.crop_url ?
                        {backgroundImage: `url("${o.crop_url}")`} :
                        null
                  }))));
  }

  // ------------------------------------------------------------- voice bar
  function drawTranscript() {
    const el = barWrap.querySelector('#imm-transcript');
    if (!el) return;
    if (typing) return;
    if (!finalText && !interim) {
      mount(
          el,
          h('span.muted',
            voice.available ?
                (listening ? 'Listening\u2026' :
                             'Hold Space and talk \u2014 or press T to type') :
                (micError || voice.support.reason)),
          listening ? h('span.caret') : null);
      return;
    }
    mount(
        el, finalText ? `${finalText} ` : '',
        interim ? h('span.interim', interim) : null,
        listening ? h('span.caret') : null);
  }

  function drawBar() {
    const can = voice.available;
    const mic = typing ?
        null :
        h('button.imm-mic#imm-mic', {
          type: 'button',
          class: [listening ? 'on' : '', can ? '' : 'off'],
          disabled: !can,
          title: can ?
              (listening ? 'Listening \u2014 click or release Space to send' :
                           'Click or hold Space to talk') :
              voice.support.reason,
          onclick: () => voice.toggle()
        },
          icon(can ? 'mic' : 'mic_off'));
    const meta = pending ? h('div.imm-meta#imm-heard',
                             h('span.lbl', 'Gemini heard'),
                             h('span.imm-pol',
                               ['bad', 'good', 'note'].map(
                                   (p) =>
                                       h(
                                           'b', {
                                             class:
                                                 [pending.polarity === p ?
                                                      `on ${p}` :
                                                      ''],
                                             role: 'button',
                                             title: 'Tap to change',
                                             onclick:
                                                 () => {
                                                   pending.polarity = p;
                                                   drawBar();
                                                 }
                                           },
                                           p.replace(
                                               /^./, (x) => x.toUpperCase())))),
                             (pending.anchors ||
                              []).map((a) => anchorChip(a, {
                                        links,
                                        onRemove: (x) => {
                                          pending.anchors =
                                              (pending.anchors ||
                                               []).filter((y) => y !== x);
                                          drawBar();
                                          drawHeard();
                                        }
                                      })),
                             h('span.gem', icon('auto_awesome'),
                               'polarity & anchors proposed by Gemini \u2014 tap to change')) :
                           (turnBusy ? h('div.imm-meta',
                                         spinner('Gemini is listening\u2026')) :
                                       null);
    const pendingText = pending && pending.text ?
        h('div.imm-pending', h('span.lbl', 'Will capture: '), pending.text) :
        null;
    mount(
        barWrap,
        h('div.imm-bar', mic || h('span.imm-mic.typed', icon('keyboard')),
          h('div.imm-mid',
            typing ? typeIn : h('div.imm-transcript#imm-transcript'),
            pendingText, meta,
            !can && !typing ? h('div.imm-novoice.small', icon('info'),
                                ` ${voice.support.reason}`) :
                              null),
          h('div.imm-right',
            h('button.btn.btn-primary#imm-capture', {
              type: 'button',
              disabled: !pending || turnBusy,
              onclick: () => capture()
            },
              icon('add'), 'Capture ', icon('keyboard_return')),
            h('div.keys', h('span', h('kbd', 'Space'), ' talk'),
              h('span', h('kbd', 'G'), '/', h('kbd', 'B'), ' polarity'),
              h('span', h('kbd', '\u2190'), h('kbd', '\u2192'), ' cards'),
              (api.isDistilling && api.isDistilling()) ?
                  h('span.muted', 'distilling\u2026') :
                  h('span', {
                    class: (!api.canDistill || api.canDistill()) ? '' : 'muted'
                  },
                    h('kbd', 'D'), ' distill')))));
    drawTranscript();
    requestAnimationFrame(() => alive && computeScale());
  }

  function setTyping(on) {
    typing = on;
    if (on) voice.stop();
    drawTop();
    drawBar();
    if (on) setTimeout(() => typeIn.focus(), 0);
  }

  // ------------------------------------------------------------ voice turn
  function sendTurn(transcript, {source = 'voice', capture = false} = {}) {
    turnQueue = turnQueue.then(() => doTurn(transcript, {source, capture}))
                    .catch(() => {});
    return turnQueue;
  }

  async function doTurn(transcript, {source, capture}) {
    const c = card();
    if (!c || !alive) return;
    turnBusy = true;
    drawBar();
    try {
      // Crop from the anchors Gemini settled on (client-side, like the
      // composer).
      const crop = capture && pending ?
          await api.cropFor(c, pending.anchors || []) :
          null;
      const res = await post(`/api/teach/${api.sid}/voice_turn`, {
        crop_data_url: crop || undefined,
        output_id: c.output_id,
        transcript: transcript || '',
        source,
        pointed: pointedAnchors(),
        pending: pending || null,
        question: openQuestion || null,
        capture: capture || undefined,
      });
      if (!alive) return;
      await handleTurn(res);
    } catch {
    } finally {
      turnBusy = false;
      if (alive) drawBar();
    }
  }

  async function handleTurn(res) {
    const ob = res.observation;
    if (ob && (ob.text || (ob.anchors || []).length)) {
      pending = {
        ...ob,
        polarity: ob.polarity || 'note',
        text: ob.text || '',
        anchors: ob.anchors || []
      };
    }
    if (res.captured) {
      captured.unshift(res.captured);
      pending = null;
      finalText = '';
      const st = api.getState();
      if (st) {
        st.observations = st.observations || [];
        if (!st.observations.some((o) => o.id === res.captured.id)) {
          st.observations.push(res.captured);
        }
      }
      drawCaptured();
      drawTop();
      drawBar();
      syncDrawerDistill();
      api.refresh().catch(() => {});
    }
    if (res.undone) {
      captured = captured.filter((o) => o.id !== res.undone);
      const st = api.getState();
      if (st && st.observations) {
        st.observations = st.observations.filter((o) => o.id !== res.undone);
      }
      drawCaptured();
      drawTop();
      drawBar();
      syncDrawerDistill();
      api.refresh().catch(() => {});
    }
    openQuestion = res.question || null;
    quickReplies = openQuestion ? (res.quick_replies || []) : [];
    say = res.say || '';
    if (say) voice.speak(say);
    drawAssist();
    drawHeard();
    const intent = res.intent || '';
    if (intent === 'navigate' && res.navigate) {
      const n = res.navigate;
      if (n.to === 'next')
        api.setIndex(api.getIndex() + 1);
      else if (n.to === 'prev')
        api.setIndex(api.getIndex() - 1);
      else if (n.to === 'index' && Number.isInteger(n.index))
        api.setIndex(n.index);
    } else if (intent === 'distill') {
      if (!api.canDistill || api.canDistill()) await api.distill();
    } else if (intent === 'applications') {
      openLearned();
    } else if (intent === 'template') {
      api.templateFrom(
          res.template_from ?
              {key: res.template_from} :
              (selectedKey ? {key: selectedKey} : {whole: true}));
    } else if (intent === 'undo' && !res.undone) {
      await undoLast();
    }
  }

  function capture() {
    if (!pending) return;
    sendTurn('', {source: typing ? 'typed' : 'voice', capture: true});
  }

  async function undoLast() {
    const last = captured[0];
    if (!last) return;
    await del(`/api/teach/observations/${last.id}`);
    captured.shift();
    const st = api.getState();
    if (st && st.observations) {
      st.observations = st.observations.filter((o) => o.id !== last.id);
    }
    drawCaptured();
    drawTop();
    drawBar();
    syncDrawerDistill();
    toast('Observation removed', 'good');
    api.refresh().catch(() => {});
  }

  // ---------------------------------------------------------------- learned
  function syncDrawerDistill() {
    if (!drawerDistillBtn) return;
    const st = api.getState() || {};
    const open =
        (st.observations || []).filter((o) => o.status === 'open').length;
    const distilling = api.isDistilling ? api.isDistilling() : false;
    const can = api.canDistill ? api.canDistill() : open > 0;
    const distillN =
        (api.distillingCount ? api.distillingCount() : open) || open;
    drawerDistillBtn.disabled = !can;
    drawerDistillBtn.classList.toggle('is-working', distilling);
    drawerDistillBtn.setAttribute('aria-busy', String(distilling));
    drawerDistillBtn.title = distilling ?
        'Distillation in progress\u2026' :
        open ?
        `Distill ${open} open observation${
            open === 1 ? '' : 's'} into candidate rules` :
        'All observations distilled \u2014 capture a new observation to distill again';
    if (distilling) {
      mount(
          drawerDistillBtn, spinner('Distilling\u2026'),
          distillN ? h('span.badge-count', String(distillN)) : null);
    } else {
      mount(
          drawerDistillBtn, icon('auto_awesome'), 'Distill',
          open ? h('span.badge-count', String(open)) : null);
    }
  }

  function openLearned() {
    if (learnedDrawer) return;
    const body = h('div.imm-learned');
    learnedDrawer = drawer({
      title: 'Learned so far',
      body,
      wide: true,
      onClose: () => {
        learnedDrawer = null;
        drawerDistillBtn = null;
      }
    });
    learnedDrawer.el.classList.add('teach-scope');
    learnedDrawer.el.style.zIndex = '120';
    const scrim = learnedDrawer.el.previousElementSibling;
    if (scrim && scrim.classList.contains('scrim')) scrim.style.zIndex = '110';
    drawerDistillBtn =
        h('button.btn.btn-ghost.btn-sm#imm-drawer-distill',
          {type: 'button', onclick: () => api.distill()}, icon('auto_awesome'),
          'Distill');
    syncDrawerDistill();
    learnedDrawer.el.querySelector('.drawer-head-actions')
        .prepend(drawerDistillBtn);
    api.renderLearnedInto(body);
  }

  // ------------------------------------------------------------------- keys
  function onKey(e) {
    if (!alive || e.metaKey || e.ctrlKey || e.altKey) return;
    if (document.querySelector('.overlay')) return;
    if (isTyping(e.target)) return;
    if (document.querySelector('.drawer.open')) return;
    const k = e.key;
    if (k === 'Escape') {
      // Immersive is the default mode: Esc only cancels the current turn.
      e.preventDefault();
    } else if (k === 'ArrowLeft' || k === 'ArrowRight') {
      e.preventDefault();
      api.setIndex(api.getIndex() + (k === 'ArrowRight' ? 1 : -1));
    } else if (k === 'f' || k === 'F') {
      e.preventDefault();
      api.finish();
    } else if (k === 'd' || k === 'D') {
      e.preventDefault();
      if (!api.canDistill || api.canDistill()) api.distill();
    } else if (k === 't' || k === 'T') {
      e.preventDefault();
      setTyping(!typing);
    } else if (k === 'Enter') {
      e.preventDefault();
      capture();
    } else if (/^[1-3]$/.test(k) && quickReplies[Number(k) - 1]) {
      e.preventDefault();
      sendTurn(quickReplies[Number(k) - 1], {source: 'typed'});
    } else if (pending && (k === 'g' || k === 'b' || k === 'n')) {
      e.preventDefault();
      pending.polarity = k === 'g' ? 'good' : k === 'b' ? 'bad' : 'note';
      drawBar();
    }
  }

  function onResize() {
    computeScale();
    drawTip();
    drawHeard();
  }

  // ------------------------------------------------------------- lifecycle
  return {
    onState() {
      if (!alive) return;
      drawTop();
      drawAssist();
      drawBar();
      syncDrawerDistill();
      if (cardSig() !== stageSig) buildStage();
      if (learnedDrawer) {
        mount(learnedDrawer.body, h('div.imm-learned'));
        api.renderLearnedInto(learnedDrawer.body.firstChild);
      }
    },
    onCard() {
      if (!alive) return;
      pending = null;
      openQuestion = null;
      quickReplies = [];
      say = '';
      finalText = '';
      drawTop();
      buildStage();
      drawAssist();
      drawBar();
    },
    destroy() {
      alive = false;
      voice.destroy();
      unbindPtt();
      window.removeEventListener('message', onMessage);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', onResize);
      if (learnedDrawer) learnedDrawer.close();
      root.remove();
    },
  };
}
