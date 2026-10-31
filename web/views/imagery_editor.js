// Imagery & Iconography tab in Design System (/p/:pid/design?tab=imagery).
// Lets designers author reusable generated imagery & SVG icon style presets
// (text style guidance + reference images + incorporation rules), test live
// Gemini previews, and add incorporation rules to the Constitution.
import {del, get, patch, post} from '../components/api.js';
import {emptyState, h, icon, mount, spinner} from '../components/dom.js';
import {emit, on} from '../components/store.js';
import {confirmDialog, modal, toast} from '../components/ui.js';

const MEDIUM_LABELS = {
  raster_image: 'Raster Image (Gemini Image)',
  svg_icon: 'SVG Icon (Vector)',
  svg_illustration: 'SVG Illustration (Vector)',
};

const SLOT_LABELS = {
  card_background: 'Full-bleed Card Background',
  list_item_icon: 'List Item SVG Icon',
  hero_banner: 'Hero / Empty-State Banner',
  inline_image: 'Inline Image',
  custom_svg: 'Custom Component SVG',
};

const QUICK_PRESETS = [
  {
    name: 'Weather Full-Bleed Background',
    slug: 'weather-bg',
    medium: 'raster_image',
    target_slot: 'card_background',
    aspect_ratio: '16:9',
    scrim: 'auto',
    incorporation:
        'Use as a full-bleed Card.backgroundImage in weather widgets and forecast summary cards (with scrim="auto"), depicting the current sky, precipitation, and time of day.',
    style_text:
        'Soft atmospheric editorial gouache and watercolor sky illustration, warm horizon gradients, subtle cloud textures, calm composition with breathing room so overlaid typography stays crisp and legible.',
    preview_prompt: 'partly cloudy golden hour over coastal hills, 68°F',
  },
  {
    name: 'List Item Duotone SVG Icon',
    slug: 'list-item-icon',
    medium: 'svg_icon',
    target_slot: 'list_item_icon',
    aspect_ratio: '1:1',
    scrim: 'auto',
    incorporation:
        'Place a 24px generated SVG Icon at the leading edge of each list item row to visually anchor the item category or action.',
    style_text:
        '2px rounded-stroke geometric vector icon with a subtle soft-tinted container fill using theme primary and primary_container colors, minimal 2-layer construction, crisp at 24x24px.',
    preview_prompt: 'calendar event reminder bell',
  },
  {
    name: 'Editorial Hero SVG Illustration',
    slug: 'hero-illustration',
    medium: 'svg_illustration',
    target_slot: 'hero_banner',
    aspect_ratio: '16:9',
    scrim: 'none',
    incorporation:
        'Include as a hero Image at the top of overview cards, onboarding prompts, or empty states.',
    style_text:
        'Modern editorial flat vector illustration with layered organic shapes, balanced negative space, and harmonious theme palette accents.',
    preview_prompt: 'calm morning itinerary planning desk with coffee and map',
  },
];

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('Could not read image file'));
    reader.readAsDataURL(file);
  });
}

export function createImageryTab(pid) {
  let styles = [];
  let paneEl = null;
  const disposers = [];

  async function loadStyles() {
    const res = await get(`/api/projects/${pid}/imagery`);
    styles = res.styles || [];
    return styles;
  }

  function actions() {
    return [
      h('button.btn.btn-ghost#imagery-preset-weather', {
        type: 'button',
        title: 'Quick-add a full-bleed weather background style preset',
        onclick: () => openStyleModal(QUICK_PRESETS[0], {isNew: true}),
      },
        icon('wb_twilight'), 'Weather background preset'),
      h('button.btn.btn-ghost#imagery-preset-list-icon', {
        type: 'button',
        title: 'Quick-add a generated SVG list-item icon preset',
        onclick: () => openStyleModal(QUICK_PRESETS[1], {isNew: true}),
      },
        icon('format_list_bulleted'), 'List SVG icon preset'),
      h('button.btn.btn-primary#new-imagery-style', {
        type: 'button',
        onclick: () => openStyleModal(null, {isNew: true}),
      },
        icon('add_photo_alternate'), 'New imagery / icon style'),
    ];
  }

  async function mountTab(pane) {
    paneEl = pane;
    mount(paneEl, spinner('Loading imagery & icon styles\u2026'));
    await loadStyles();
    draw();
    if (!disposers.length) {
      disposers.push(on('imagery-changed', async () => {
        await loadStyles().catch(() => null);
        if (paneEl) draw();
      }));
    }
  }

  function draw() {
    if (!paneEl) return;
    mount(
        paneEl,
        h('div.imagery-intro.callout', icon('auto_awesome'),
          h('div', h('strong', 'Generated Imagery & SVG Iconography'),
            h('p.small.muted',
              'Define how generated images and vector icons should look (text style guidance + reference images) and how they should be incorporated (e.g. full-bleed ',
              h('code', 'Card.backgroundImage'),
              ' in a weather widget, or a generated ', h('code', '<svg>'), ' ',
              h('code', 'Icon'),
              ' next to each list item). The UI generator emits ',
              h('code', '/gen/img?style=<slug>&prompt=...'), ' or ',
              h('code', '/gen/svg?style=<slug>&prompt=...'),
              ' and materializes them with Gemini during runs.'))),
        styles.length ?
            h('div.imagery-grid#imagery-style-grid', styles.map(styleCard)) :
            emptyState(
                'No imagery or icon style presets yet',
                'Create a style preset with text guidance and optional reference images, or start from a template below.',
                ...QUICK_PRESETS.map(
                    (preset) =>
                        h('button.btn.btn-ghost', {
                          type: 'button',
                          onclick: () => openStyleModal(preset, {isNew: true}),
                        },
                          icon(
                              preset.medium === 'raster_image' ? 'wallpaper' :
                                                                 'interests'),
                          `Add "${preset.name}"`)),
                h('button.btn.btn-primary', {
                  type: 'button',
                  onclick: () => openStyleModal(null, {isNew: true}),
                },
                  icon('add'), 'Create custom style')));
  }

  function styleCard(st) {
    const isSvg = st.medium === 'svg_icon' || st.medium === 'svg_illustration';
    const urlPattern = isSvg ?
        `/gen/svg?style=${st.slug}&prompt=<icon subject>` :
        `/gen/img?style=${st.slug}&prompt=<scene description>&aspect=${
            st.aspect_ratio || '16:9'}`;

    const previewPromptIn = h('input.input.input-sm.imagery-preview-input', {
      placeholder: isSvg ?
          'Test subject, e.g. "rain umbrella" or "flight departure"\u2026' :
          'Test scene, e.g. "thunderstorm over bay at dusk"\u2026',
      value: '',
    });

    const previewBtn =
        h('button.btn.btn-ghost.btn-sm.imagery-preview-btn', {
          type: 'button',
          onclick: async () => {
            previewBtn.disabled = true;
            previewBtn.replaceChildren(spinner(), 'Generating\u2026');
            try {
              const res =
                  await post(`/api/projects/${pid}/imagery/${st.id}/preview`, {
                    prompt: previewPromptIn.value.trim(),
                  });
              if (res.style) {
                const idx = styles.findIndex((x) => x.id === st.id);
                if (idx >= 0) styles[idx] = res.style;
                draw();
              }
              toast(`Generated preview for ${st.name}`, 'good');
            } finally {
              previewBtn.disabled = false;
              previewBtn.replaceChildren(
                  icon('auto_awesome'), 'Generate preview');
            }
          },
        },
          icon('auto_awesome'),
          st.preview_url ? 'Regenerate preview' : 'Generate preview');

    const refs = st.reference_images || [];
    return h(
        'article.comp-card.imagery-card',
        {dataset: {sid: st.id, slug: st.slug}},
        h('div.imagery-card-media', {
          class: isSvg ? 'imagery-media-svg' : 'imagery-media-raster',
        },
          st.preview_url ?
              h('img.imagery-preview-img', {
                src: st.preview_url,
                alt: `${st.name} preview`,
                loading: 'lazy',
              }) :
              h('div.draft-fig-empty', icon(isSvg ? 'interests' : 'landscape'),
                h('span', 'No preview generated yet'))),
        h('div.comp-card-body.imagery-card-body',
          h('div.comp-card-title', h('strong.comp-name', st.name),
            h('code.chip.chip-xs', `style=${st.slug}`), h('span.grow'),
            h('span.chip.chip-xs', MEDIUM_LABELS[st.medium] || st.medium)),
          h('div.chip-row',
            h('span.chip.chip-xs.chip-quiet',
              SLOT_LABELS[st.target_slot] || st.target_slot),
            h('span.chip.chip-xs.chip-quiet',
              `Aspect ${st.aspect_ratio || '16:9'}`),
            st.target_slot === 'card_background' ?
                h('span.chip.chip-xs.chip-quiet',
                  `Scrim: ${st.scrim || 'auto'}`) :
                null),
          st.incorporation ? h('div.imagery-field-block',
                               h('div.small.muted', 'How to incorporate:'),
                               h('p.comp-desc', st.incorporation)) :
                             null,
          st.style_text ? h('div.imagery-field-block',
                            h('div.small.muted', 'Visual style guidance:'),
                            h('p.comp-desc', st.style_text)) :
                          null,
          refs.length ?
              h('div.imagery-refs-row',
                h('span.small.muted',
                  `Style reference images (${refs.length}):`),
                h('div.imagery-ref-thumbs',
                  refs.map((r) => h('img.imagery-ref-thumb', {
                             src: r.url,
                             alt: r.name || 'Style reference',
                             title: r.caption || r.name || 'Style reference',
                           })))) :
              null,
          h('div.imagery-url-hint', h('code.small', urlPattern)),
          h('div.imagery-preview-bar', previewPromptIn, previewBtn),
          h('div.comp-card-actions',
            h('button.btn.btn-ghost.btn-sm', {
              type: 'button',
              title:
                  'Ask Revision Chat to add a Constitution rule for this imagery preset',
              onclick: () => {
                const promptText = st.target_slot === 'card_background' ?
                    `Add a rule to the constitution to incorporate the "${
                        st.name}" (style=${
                        st.slug}) preset as a full-bleed Card.backgroundImage (${
                        st.incorporation || 'in relevant cards'}) with scrim="${
                        st.scrim || 'auto'}".` :
                    st.target_slot === 'list_item_icon' ?
                    `Add a rule to the constitution to incorporate a generated SVG Icon using the "${
                        st.name}" (style=${
                        st.slug}) preset next to each list item (${
                        st.incorporation || 'in list rows'}).` :
                    `Add a rule to the constitution to incorporate the "${
                        st.name}" (style=${st.slug}) preset (${
                        st.incorporation || 'where appropriate'}).`;
                emit('chat-open', {text: promptText});
              },
            },
              icon('forum'), 'Add rule in Chat'),
            h('button.btn.btn-ghost.btn-sm', {
              type: 'button',
              onclick: () => openStyleModal(st, {isNew: false}),
            },
              icon('edit'), 'Edit'),
            h('button.btn.btn-quiet.btn-sm', {
              type: 'button',
              onclick: async () => {
                if (!(await confirmDialog(
                        `Delete "${st.name}"?`,
                        `Removes style preset "${st.slug}".`,
                        {confirmLabel: 'Delete', danger: true})))
                  return;
                await del(`/api/projects/${pid}/imagery/${st.id}`);
                styles = styles.filter((x) => x.id !== st.id);
                draw();
                toast(`Deleted ${st.name}`, 'good');
              },
            },
              'Delete'))));
  }

  function openStyleModal(initial, {isNew = false} = {}) {
    const st = {
      name: (initial && initial.name) || '',
      slug: (initial && initial.slug) || '',
      medium: (initial && initial.medium) || 'raster_image',
      target_slot: (initial && initial.target_slot) || 'card_background',
      aspect_ratio: (initial && initial.aspect_ratio) || '16:9',
      scrim: (initial && initial.scrim) || 'auto',
      incorporation: (initial && initial.incorporation) || '',
      style_text: (initial && initial.style_text) || '',
      reference_images: [...((initial && initial.reference_images) || [])],
    };

    const nameIn = h('input.input#imagery-modal-name', {
      placeholder: 'e.g. Weather Full-Bleed Background or List Item SVG Icon',
      value: st.name,
      oninput: (e) => {
        st.name = e.target.value;
        if (isNew && !slugTouched) {
          slugIn.value = st.name.toLowerCase()
                             .replace(/[^a-z0-9]+/g, '-')
                             .replace(/^-|-$/g, '')
                             .slice(0, 48);
          st.slug = slugIn.value;
        }
      },
    });
    let slugTouched = Boolean(st.slug && !isNew);
    const slugIn = h('input.input.mono#imagery-modal-slug', {
      placeholder: 'e.g. weather-bg or list-item-icon',
      value: st.slug,
      oninput: (e) => {
        slugTouched = true;
        st.slug = e.target.value;
      },
    });
    const mediumSel =
        h('select.input#imagery-modal-medium', {
          onchange: (e) => {
            st.medium = e.target.value;
            if (st.medium === 'svg_icon') {
              st.aspect_ratio = '1:1';
              aspectSel.value = '1:1';
              if (st.target_slot === 'card_background') {
                st.target_slot = 'list_item_icon';
                slotSel.value = 'list_item_icon';
              }
            }
          },
        },
          Object.entries(MEDIUM_LABELS)
              .map(
                  ([k, l]) =>
                      h('option', {value: k, selected: st.medium === k}, l)));

    const slotSel = h(
        'select.input#imagery-modal-slot', {
          onchange: (e) => {
            st.target_slot = e.target.value;
            if (st.target_slot === 'list_item_icon') {
              st.medium = 'svg_icon';
              mediumSel.value = 'svg_icon';
              st.aspect_ratio = '1:1';
              aspectSel.value = '1:1';
            } else if (st.target_slot === 'card_background') {
              st.medium = 'raster_image';
              mediumSel.value = 'raster_image';
            }
          },
        },
        Object.entries(SLOT_LABELS)
            .map(
                ([k, l]) => h(
                    'option', {value: k, selected: st.target_slot === k}, l)));

    const aspectSel =
        h('select.input#imagery-modal-aspect',
          {onchange: (e) => (st.aspect_ratio = e.target.value)},
          ['16:9', '4:3', '1:1', '3:4', '9:16'].map(
              (a) =>
                  h('option', {value: a, selected: st.aspect_ratio === a}, a)));

    const scrimSel =
        h('select.input#imagery-modal-scrim',
          {onchange: (e) => (st.scrim = e.target.value)},
          [
            ['auto', 'Auto gradient scrim (high-contrast text)'],
            ['dark', 'Dark scrim (white foreground text)'],
            ['light', 'Light frosted scrim (dark foreground text)'],
            ['none', 'None (no overlay scrim)'],
          ].map(([k,
                  l]) => h('option', {value: k, selected: st.scrim === k}, l)));

    const incorpIn =
        h('textarea.input#imagery-modal-incorporation', {
          rows: 2,
          placeholder:
              'How & where to incorporate, e.g. "Use a full-bleed background image of the current weather in weather widgets" or "Use a 24px generated SVG icon next to each list item row"',
          oninput: (e) => (st.incorporation = e.target.value),
        },
          st.incorporation);

    const styleIn =
        h('textarea.input#imagery-modal-style', {
          rows: 3,
          placeholder:
              'Visual style guidance (text), e.g. "Soft atmospheric gouache illustration with warm dusk gradients" or "2px rounded duotone outline icon using primary and primary_container colors"',
          oninput: (e) => (st.style_text = e.target.value),
        },
          st.style_text);

    const refsContainer = h('div.imagery-modal-refs');
    const fileInput = h('input', {
      type: 'file',
      accept: 'image/*',
      multiple: true,
      hidden: true,
      onchange: async (e) => {
        const files = Array.from(e.target.files || []);
        for (const f of files.slice(0, 6 - st.reference_images.length)) {
          const dataUrl = await fileToDataUrl(f);
          st.reference_images.push({
            data_url: dataUrl,
            url: dataUrl,
            name: f.name,
            caption: '',
          });
        }
        e.target.value = '';
        renderRefsList();
      },
    });

    function renderRefsList() {
      mount(
          refsContainer,
          st.reference_images.map(
              (r, i) => h(
                  'div.imagery-ref-item',
                  h('img.imagery-ref-thumb',
                    {src: r.url || r.data_url, alt: r.name || ''}),
                  h('input.input.input-sm', {
                    placeholder:
                        'Optional note on what to borrow from this reference\u2026',
                    value: r.caption || '',
                    oninput: (e) => (r.caption = e.target.value),
                  }),
                  h('button.icon-btn', {
                    type: 'button',
                    title: 'Remove reference image',
                    onclick: () => {
                      st.reference_images.splice(i, 1);
                      renderRefsList();
                    },
                  },
                    icon('close')))),
          st.reference_images.length < 6 ?
              h('button.btn.btn-ghost.btn-sm', {
                type: 'button',
                onclick: () => fileInput.click(),
              },
                icon('upload_file'), 'Upload style reference image(s)') :
              null,
          fileInput);
    }
    renderRefsList();

    modal({
      title: isNew ? 'New Imagery / Iconography Style' : `Edit ${st.name}`,
      wide: true,
      body: h(
          'div.form-stack',
          h('div.form-grid.form-grid-2',
            h('label.field', h('span.field-label', 'Preset Name'), nameIn),
            h('label.field',
              h('span.field-label', 'Style Slug (used in /gen/...)'), slugIn)),
          h('div.form-grid.form-grid-2',
            h('label.field', h('span.field-label', 'Medium'), mediumSel),
            h('label.field', h('span.field-label', 'Target Incorporation Slot'),
              slotSel)),
          h('div.form-grid.form-grid-2',
            h('label.field', h('span.field-label', 'Aspect Ratio'), aspectSel),
            h('label.field', h('span.field-label', 'Card Background Scrim'),
              scrimSel)),
          h('label.field',
            h('span.field-label', 'Incorporation Rule (where & how to use)'),
            incorpIn),
          h('label.field',
            h('span.field-label', 'Visual Style Guidance (text)'), styleIn),
          h('div.field',
            h('span.field-label',
              'Style Reference Images (multimodal input to Gemini)'),
            h('p.help.small',
              'Attach 1\u20136 reference images that exemplify the palette, brushwork, lighting, or icon stroke style you want Gemini to follow.'),
            refsContainer)),
      actions: [
        {label: 'Cancel', kind: 'ghost'},
        {
          label: isNew ? 'Create style preset' : 'Save changes',
          kind: 'primary',
          id: 'imagery-modal-save',
          onClick: async () => {
            if (!st.name.trim()) {
              toast('Enter a name for the style preset', 'warn');
              return false;
            }
            const payload = {
              name: st.name.trim(),
              slug: st.slug.trim(),
              medium: st.medium,
              target_slot: st.target_slot,
              aspect_ratio: st.aspect_ratio,
              scrim: st.scrim,
              incorporation: st.incorporation.trim(),
              style_text: st.style_text.trim(),
              reference_images: st.reference_images,
            };
            let saved;
            if (!isNew && initial && initial.id) {
              const res = await patch(
                  `/api/projects/${pid}/imagery/${initial.id}`, payload);
              saved = res.style;
            } else {
              const res = await post(`/api/projects/${pid}/imagery`, payload);
              saved = res.style;
            }
            await loadStyles();
            draw();
            toast(`Saved "${saved.name}" (style=${saved.slug})`, 'good');
            return true;
          },
        },
      ],
    });
  }

  return {
    actions,
    mount: mountTab,
    reload: async () => {
      await loadStyles();
      draw();
    },
    destroy: () => disposers.forEach((d) => d()),
  };
}
