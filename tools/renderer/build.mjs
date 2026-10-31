// Builds ../../web/vendor/a2ui.bundle.js (ESM) from entry.js.
// Maintainer-only step: end users never need Node; the bundle is committed.
import {build} from 'esbuild';
import {readFileSync, writeFileSync, mkdirSync} from 'node:fs';
const out = new URL('../../web/vendor/', import.meta.url);
mkdirSync(out, {recursive: true});
await build({
  entryPoints: [new URL('./entry.js', import.meta.url).pathname],
  bundle: true, format: 'esm', target: 'es2022', minify: true, sourcemap: false,
  legalComments: 'eof', outfile: new URL('a2ui.bundle.js', out).pathname,
  logLevel: 'info',
});
const pkg = (n) => JSON.parse(readFileSync(new URL(`./node_modules/${n}/package.json`, import.meta.url))).version;
writeFileSync(new URL('A2UI_VERSIONS.json', out), JSON.stringify({
  '@a2ui/web_core': pkg('@a2ui/web_core'), '@a2ui/lit': pkg('@a2ui/lit'),
  lit: pkg('lit'), zod: pkg('zod'), protocol: 'v1.0',
  note: 'Unmodified published packages bundled by tools/renderer/build.mjs',
}, null, 2) + '\n');
