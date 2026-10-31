// "Report an issue": opens a prefilled GitHub issue form in a new tab.
//
// The body carries the app version, the current route and the recent client
// errors buffered by diagnostics.js. Nothing is sent anywhere until the user
// submits the GitHub form themselves.
import {collectClientContext} from './diagnostics.js';
import {store} from './store.js';

export const ISSUES_URL = 'https://github.com/posttool/a2ui-designer/issues/new';
// GitHub rejects very long prefilled URLs; keep well below ~8 KB.
const MAX_URL = 7000;

function appVersion() {
  return (store.appVersion || (store.user && store.user.app_version) ||
          'unknown');
}

function errorLines(errors) {
  return errors.slice(-8).reverse().map((e) => {
    const where = e.source ? ` (${e.source})` : '';
    return `- ${e.at} [${e.kind}] ${e.message}${where}`;
  });
}

/**
 * Builds the GitHub "new issue" URL.
 * opts: {title, details} — `details` is free text (e.g. an error message).
 */
export function reportIssueUrl(opts = {}) {
  const ctx = collectClientContext();
  const scope = ctx.scope || {};
  const env = ctx.environment || {};
  const title = String(opts.title || '').slice(0, 120);
  const head = [
    '### What happened',
    '',
    String(opts.details || '').slice(0, 1500) ||
        '<!-- Describe what you did and what you expected. -->',
    '',
    '### Environment',
    '',
    `- App version: ${appVersion()}`,
    `- Route: \`${scope.path || location.pathname}\``,
    `- View: ${scope.view || ''}`,
    `- Browser: ${env.user_agent || ''}`,
    `- Viewport: ${(env.viewport || {}).width || 0}x${
        (env.viewport || {}).height || 0}`,
  ];
  const errs = errorLines(ctx.console_errors || []);
  const build = (lines) => {
    const body = head.concat(
        lines.length ? ['', '### Recent client errors', '', ...lines] : []);
    return `${ISSUES_URL}?title=${encodeURIComponent(title)}&body=${
        encodeURIComponent(body.join('\n'))}`;
  };
  let lines = errs;
  let url = build(lines);
  while (url.length > MAX_URL && lines.length) {
    lines = lines.slice(0, -1);
    url = build(lines.map((l) => l.slice(0, 300)));
  }
  return url.length > MAX_URL ? url.slice(0, MAX_URL) : url;
}

/** Opens the prefilled issue form in a new tab. */
export function openReportIssue(opts = {}) {
  window.open(reportIssueUrl(opts), '_blank', 'noopener');
}
