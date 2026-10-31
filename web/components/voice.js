// Browser speech for immersive teaching: SpeechRecognition (speech → text,
// interim + final results), hold-Space push-to-talk, and speechSynthesis for
// Gemini's short spoken replies. No audio leaves the browser except to the
// browser vendor's recognizer; the app only ever sees text.

const SECURE_HINT =
    'Voice needs https or localhost \u2014 run ./run.sh --https';

/** {ok, reason}: whether the mic can be used in this page. */
export function voiceSupport() {
  if (typeof window === 'undefined') return {ok: false, reason: 'No browser'};
  if (!window.isSecureContext) return {ok: false, reason: SECURE_HINT};
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Rec) {
    return {
      ok: false,
      reason:
          'This browser has no speech recognition \u2014 use Chrome, or type instead (T)'
    };
  }
  return {ok: true, reason: ''};
}

/**
 * Voice({onInterim(text), onFinal(text), onState(listening), onError(msg)})
 * start()/stop() control one listening window; every final result is
 * reported once via onFinal.
 */
export class Voice {
  constructor({onInterim, onFinal, onState, onError, lang} = {}) {
    this.onInterim = onInterim || (() => {});
    this.onFinal = onFinal || (() => {});
    this.onState = onState || (() => {});
    this.onError = onError || (() => {});
    this.lang = lang || navigator.language || 'en-US';
    this.support = voiceSupport();
    this.listening = false;
    this.speakEnabled = true;
    this.rec = null;
    this.wantStop = false;
  }

  get available() {
    return this.support.ok;
  }

  _make() {
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    const rec = new Rec();
    rec.continuous = true;
    rec.interimResults = true;
    rec.lang = this.lang;
    rec.onresult = (e) => {
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        const text = (r[0] && r[0].transcript) || '';
        if (r.isFinal) {
          const t = text.trim();
          if (t) this.onFinal(t);
        } else {
          interim += text;
        }
      }
      this.onInterim(interim.trim());
    };
    rec.onerror = (e) => {
      if (e.error === 'no-speech' || e.error === 'aborted') return;
      const msg = e.error === 'not-allowed' ?
          'Microphone permission was denied' :
          `Speech recognition error: ${e.error}`;
      this.onError(msg);
    };
    rec.onend = () => {
      this.listening = false;
      this.onInterim('');
      this.onState(false);
    };
    return rec;
  }

  start() {
    if (!this.available || this.listening) return;
    try {
      if (window.speechSynthesis) window.speechSynthesis.cancel();
      this.rec = this._make();
      this.rec.start();
      this.listening = true;
      this.onState(true);
    } catch (e) {
      this.onError(String(e.message || e));
    }
  }

  stop() {
    if (!this.rec) return;
    try {
      this.rec.stop();
    } catch {
    }
  }

  toggle() {
    if (this.listening)
      this.stop();
    else
      this.start();
  }

  /** Speaks a short reply when voice output is on. */
  speak(text) {
    if (!this.speakEnabled || !text || !window.speechSynthesis) return;
    try {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = this.lang;
      u.rate = 1.05;
      window.speechSynthesis.speak(u);
    } catch {
    }
  }

  destroy() {
    this.stop();
    if (window.speechSynthesis) window.speechSynthesis.cancel();
    this.rec = null;
  }
}

function isTypingTarget(t) {
  return t &&
      (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' ||
       t.tagName === 'SELECT' || t.isContentEditable);
}

/**
 * Hold Space to talk: keydown starts listening, keyup stops. Ignored while
 * typing in a field or when `enabled()` returns false. Returns a cleanup fn.
 */
export function bindPushToTalk(voice, {enabled = () => true} = {}) {
  let held = false;
  function down(e) {
    if (e.code !== 'Space' || e.repeat || isTypingTarget(e.target)) return;
    if (!enabled() || !voice.available) return;
    e.preventDefault();
    held = true;
    voice.start();
  }
  function up(e) {
    if (e.code !== 'Space' || !held) return;
    e.preventDefault();
    held = false;
    voice.stop();
  }
  document.addEventListener('keydown', down);
  document.addEventListener('keyup', up);
  return () => {
    document.removeEventListener('keydown', down);
    document.removeEventListener('keyup', up);
  };
}
