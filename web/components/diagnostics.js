// Client-side ring buffers for recent API calls, console/unhandled errors, and
// toasts, plus route/context inspection for the "Report an issue" link.
import {store} from './store.js';

const MAX_API_CALLS = 30;
const MAX_ERRORS = 25;
const MAX_TOASTS = 15;

const apiCalls = [];
const clientErrors = [];
const recentToasts = [];
let installed = false;

function pushBounded(arr, item, max) {
  arr.push(item);
  if (arr.length > max) arr.splice(0, arr.length - max);
}

export function installDiagnostics() {
  if (installed || typeof window === 'undefined') return;
  installed = true;

  window.addEventListener('error', (e) => {
    pushBounded(
        clientErrors, {
          at: new Date().toISOString(),
          kind: 'error',
          message: String(e.message || e.error || 'Window error').slice(0, 500),
          source: e.filename ? `${e.filename}:${e.lineno || 0}` : undefined,
          stack: e.error && e.error.stack ?
              String(e.error.stack).slice(0, 1500) :
              undefined,
        },
        MAX_ERRORS);
  });

  window.addEventListener('unhandledrejection', (e) => {
    const r = e.reason;
    pushBounded(
        clientErrors, {
          at: new Date().toISOString(),
          kind: 'unhandledrejection',
          message:
              String((r && (r.message || r)) || 'Unhandled promise rejection')
                  .slice(0, 500),
          stack: r && r.stack ? String(r.stack).slice(0, 1500) : undefined,
        },
        MAX_ERRORS);
  });

  const origConsoleError = console.error.bind(console);
  console.error = (...args) => {
    try {
      const msg = args.map((a) => {
                        if (a instanceof Error) return a.message || String(a);
                        if (typeof a === 'object' && a !== null) {
                          try {
                            return JSON.stringify(a);
                          } catch {
                            return String(a);
                          }
                        }
                        return String(a);
                      })
                      .join(' ')
                      .slice(0, 500);
      const errObj = args.find((a) => a instanceof Error);
      pushBounded(
          clientErrors, {
            at: new Date().toISOString(),
            kind: 'console.error',
            message: msg,
            stack: errObj && errObj.stack ?
                String(errObj.stack).slice(0, 1500) :
                undefined,
          },
          MAX_ERRORS);
    } catch {
      // ignore
    }
    origConsoleError(...args);
  };
}

export function recordApiCall(entry) {
  pushBounded(
      apiCalls, {
        at: new Date().toISOString(),
        method: entry.method || 'GET',
        url: entry.url || '',
        status: entry.status ?? 0,
        ms: entry.ms ?? 0,
        error: entry.error ? String(entry.error).slice(0, 300) : undefined,
      },
      MAX_API_CALLS);
}

export function recordToast(entry) {
  pushBounded(
      recentToasts, {
        at: new Date().toISOString(),
        kind: entry.kind || 'info',
        message: String(entry.message || '').slice(0, 300),
      },
      MAX_TOASTS);
}

export function recordClientError(message, stack) {
  pushBounded(
      clientErrors, {
        at: new Date().toISOString(),
        kind: 'manual',
        message: String(message || '').slice(0, 500),
        stack: stack ? String(stack).slice(0, 1500) : undefined,
      },
      MAX_ERRORS);
}

export function detectRouteScope() {
  const path = typeof location !== 'undefined' ? location.pathname : '/';
  const search = typeof location !== 'undefined' ? location.search : '';
  const params = new URLSearchParams(search);
  const segs = path.split('/').filter(Boolean);
  const scope = {
    url: typeof location !== 'undefined' ? location.href : path,
    path: path + search,
    view: (typeof document !== 'undefined' && document.body &&
           document.body.dataset.view) ||
        '',
    project_id: (store.project && store.project.id) || null,
    run_id: null,
    output_id: params.get('o') || null,
    import_id: null,
    component_id: null,
    session_id: null,
    effect_id: null,
    selected_component: store.reviewSelection || null,
  };

  if (segs[0] === 'p' && segs[1]) {
    scope.project_id = decodeURIComponent(segs[1]);
    const sec = segs[2];
    const id = segs[3] ? decodeURIComponent(segs[3]) : null;
    if (sec === 'runs' && id) scope.run_id = id;
    if (sec === 'review' && id) scope.run_id = id;
    if (sec === 'imports' && id) scope.import_id = id;
    if (sec === 'effects' && id) scope.effect_id = id;
    if (sec === 'teach' && id) scope.session_id = id;
    if (sec === 'design' && segs[3] === 'components' && segs[4]) {
      scope.component_id = decodeURIComponent(segs[4]);
    }
  }
  if (!scope.output_id && store.reviewSelection &&
      store.reviewSelection.output_id) {
    scope.output_id = store.reviewSelection.output_id;
  }
  if (!scope.run_id && store.reviewSelection && store.reviewSelection.run_id) {
    scope.run_id = store.reviewSelection.run_id;
  }
  if (!scope.run_id && scope.project_id && store.lastRunId &&
      store.lastRunId[scope.project_id] &&
      (scope.view === 'review' || scope.view === 'run_detail')) {
    scope.run_id = store.lastRunId[scope.project_id];
  }
  return scope;
}

export function collectClientContext(overrideScope = {}) {
  const scope = {...detectRouteScope(), ...(overrideScope || {})};
  const nav = typeof navigator !== 'undefined' ? navigator : {};
  const win = typeof window !== 'undefined' ? window : {};
  const dark =
      win.matchMedia && win.matchMedia('(prefers-color-scheme: dark)').matches;
  return {
    scope,
    environment: {
      captured_at: new Date().toISOString(),
      user_agent: nav.userAgent || '',
      language: nav.language || '',
      platform: nav.platform || '',
      viewport: {
        width: win.innerWidth || 0,
        height: win.innerHeight || 0,
        dpr: win.devicePixelRatio || 1,
      },
      prefers_color_scheme: dark ? 'dark' : 'light',
    },
    api_calls: [...apiCalls],
    console_errors: [...clientErrors],
    recent_toasts: [...recentToasts],
  };
}
