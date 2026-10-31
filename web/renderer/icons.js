// Basic Catalog icon names -> Material Symbols Rounded ligatures.
// {glyph, fill} — fill 1 renders the filled variant (FILL axis).

const MAP = {
  accountCircle: ['account_circle'],
  add: ['add'],
  arrowBack: ['arrow_back'],
  arrowForward: ['arrow_forward'],
  attachFile: ['attach_file'],
  calendarToday: ['calendar_today'],
  call: ['call'],
  camera: ['photo_camera'],
  check: ['check'],
  close: ['close'],
  delete: ['delete'],
  download: ['download'],
  edit: ['edit'],
  event: ['event'],
  error: ['error'],
  fastForward: ['fast_forward', 1],
  favorite: ['favorite', 1],
  favoriteOff: ['favorite', 0],
  folder: ['folder'],
  help: ['help'],
  home: ['home'],
  info: ['info'],
  locationOn: ['location_on'],
  lock: ['lock'],
  lockOpen: ['lock_open'],
  mail: ['mail'],
  menu: ['menu'],
  moreVert: ['more_vert'],
  moreHoriz: ['more_horiz'],
  notificationsOff: ['notifications_off'],
  notifications: ['notifications'],
  pause: ['pause', 1],
  payment: ['credit_card'],
  person: ['person'],
  phone: ['phone'],
  photo: ['photo'],
  play: ['play_arrow', 1],
  print: ['print'],
  refresh: ['refresh'],
  rewind: ['fast_rewind', 1],
  search: ['search'],
  send: ['send'],
  settings: ['settings'],
  share: ['share'],
  shoppingCart: ['shopping_cart'],
  skipNext: ['skip_next', 1],
  skipPrevious: ['skip_previous', 1],
  star: ['star', 1],
  starHalf: ['star_half', 1],
  starOff: ['star', 0],
  stop: ['stop', 1],
  upload: ['upload'],
  visibility: ['visibility'],
  visibilityOff: ['visibility_off'],
  volumeDown: ['volume_down'],
  volumeMute: ['volume_mute'],
  volumeOff: ['volume_off'],
  volumeUp: ['volume_up'],
  warning: ['warning'],
};

export const BASIC_ICON_NAMES = Object.keys(MAP);

/**
 * Returns {glyph, fill, known}. Unknown names are snake_cased as a best
 * effort.
 */
export function iconGlyph(name) {
  const s = String(name || '').trim();
  const hit = MAP[s];
  if (hit) return {glyph: hit[0], fill: hit[1] ?? 0, known: true};
  const snake = s.replace(/([a-z0-9])([A-Z])/g, '$1_$2')
                    .replace(/[\s-]+/g, '_')
                    .toLowerCase();
  return {glyph: snake || 'help', fill: 0, known: false};
}
