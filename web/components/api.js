// Fetch wrapper: JSON in/out, error toasts, 401 → login.
import {recordApiCall} from './diagnostics.js';
import {toast} from './ui.js';

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn;
}

/**
 * api('GET', '/api/projects') / api('POST', url, body, {quiet:true})
 * Returns parsed JSON (or text for non-JSON). Throws ApiError.
 */
export async function api(method, url, body, opts = {}) {
  const init = {method, headers: {}, credentials: 'same-origin'};
  // No-arg POSTs go without a body: handlers that never read it would leave
  // the bytes on the keep-alive connection and break the next request.
  const emptyObj = body && typeof body === 'object' && !Array.isArray(body) &&
      Object.keys(body).length === 0;
  if (body !== undefined && !emptyObj) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  if (opts.signal) init.signal = opts.signal;
  const t0 = (typeof performance !== 'undefined' && performance.now) ?
      performance.now() :
      Date.now();
  let res;
  try {
    res = await fetch(url, init);
  } catch (e) {
    if (e.name === 'AbortError') throw e;
    const ms = Math.round(
        ((typeof performance !== 'undefined' && performance.now) ?
             performance.now() :
             Date.now()) -
        t0);
    recordApiCall({method, url, status: 0, ms, error: e.message});
    if (!opts.quiet) toast(`Network error: ${e.message}`, 'bad');
    throw new ApiError(e.message, 0, null);
  }
  const ms = Math.round(
      ((typeof performance !== 'undefined' && performance.now) ?
           performance.now() :
           Date.now()) -
      t0);
  const ct = res.headers.get('content-type') || '';
  let data = null;
  if (ct.includes('application/json')) {
    try {
      data = await res.json();
    } catch (_) {
      data = null;
    }
  } else {
    data = await res.text();
  }
  if (!res.ok) {
    const msg = (data && data.error) ||
        (typeof data === 'string' && data.slice(0, 200)) ||
        `${res.status} ${res.statusText}`;
    recordApiCall({method, url, status: res.status, ms, error: msg});
    if (res.status === 401 && !opts.allow401) {
      onUnauthorized();
    } else if (!opts.quiet) {
      toast(msg, 'bad');
    }
    throw new ApiError(msg, res.status, data);
  }
  recordApiCall({method, url, status: res.status, ms});
  return data;
}

export const get = (url, opts) => api('GET', url, undefined, opts);
export const post = (url, body = {}, opts) => api('POST', url, body, opts);
export const put = (url, body, opts) => api('PUT', url, body, opts);
export const patch = (url, body, opts) => api('PATCH', url, body, opts);
export const del = (url, opts) => api('DELETE', url, undefined, opts);

export function qs(params) {
  const p = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== '') p.set(k, v);
  });
  const s = p.toString();
  return s ? `?${s}` : '';
}

/** Poll a job until done/failed. onProgress(job) called each tick. */
export async function pollJob(jobId, onProgress, intervalMs = 1500) {
  for (;;) {
    const {job} = await get(`/api/jobs/${jobId}`, {quiet: true});
    onProgress && onProgress(job);
    if (job.status === 'done' || job.status === 'failed') return job;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// Small shared caches for rarely-changing reference data.
const cache = new Map();
export async function cached(url) {
  if (!cache.has(url)) {
    const p = get(url).catch((e) => {
      cache.delete(url);
      throw e;
    });
    cache.set(url, p);
  }
  return cache.get(url);
}
export function invalidate(url) {
  cache.delete(url);
}
