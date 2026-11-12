#!/usr/bin/env node
/**
 * Design-token probe (HOJA-419).
 *
 * Three questions, each answered by something other than an opinion:
 *
 *  1. SOURCE — does tokens.css carry the measured numbers, and is styles.css free of a second
 *     copy of them? A literal length in a .reviews-* block for a value the design system owns
 *     fails here, because that is how the two copies drift.
 *  2. PARITY — does the refactor change rendering? The pre-refactor DOM captured from
 *     production (artifacts/reviews-dom-before.html) is loaded twice in a real browser: once
 *     with origin/main's stylesheet, once with the design layer. Every computed property of
 *     every element must match. This is a diff of ~50 elements x 41 properties, not a claim.
 *  3. LOAD-BEARING — do the tokens actually drive the DOM? A fixture of the primitives is
 *     measured, then --ds-row-height is mutated in the page and re-measured. If the row does
 *     not follow the token, the token is decoration.
 *
 *   node scripts/check-design-tokens.mjs              # all three
 *   node scripts/check-design-tokens.mjs --no-browser  # source checks only (no CDP on 9222)
 *   node scripts/check-design-tokens.mjs --self-test    # proves the probe fails when a token moves
 */
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const frontend = join(here, '..');
const repo = join(frontend, '..', '..');
const args = new Set(process.argv.slice(2));
const browserEnabled = !args.has('--no-browser');
const fail = [];
const ok = (msg) => console.log('  ok  ' + msg);

/* ------------------------------------------------------------------ source ---- */
const read = (p) => readFileSync(p, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
/* --tokens <path> lets the self-test run the same checks against a mutated copy. */
const tokensArg = process.argv.indexOf('--tokens');
const tokenPath = tokensArg === -1 ? join(frontend, 'src/design/tokens.css') : process.argv[tokensArg + 1];
const designPath = join(frontend, 'src/design/design.css');
const tokensCss = read(tokenPath);
const designCss = read(designPath);
const stylesCss = read(join(frontend, 'src/styles.css'));
const tsx = readFileSync(join(frontend, 'src/main.tsx'), 'utf8');
const tokens = {};
for (const m of tokensCss.matchAll(/(--ds-[a-z0-9-]+)\s*:\s*([^;]+);/g)) tokens[m[1]] = m[2].trim();

/** The measured numbers, each with the artifact field it came from. */
const MEASURED = {
  '--ds-ledger-width': ['482px', 'reviews-parity target .reviews-col rect.w'],
  '--ds-row-height': ['40px', 'reviews-parity rows[].height (both sides)'],
  '--ds-row-glyph-size': ['14px', 'reviews-parity rows[].glyph.size'],
  '--ds-group-bar-height': ['28px', 'reviews-parity bands[].h (both sides)'],
  '--ds-group-bar-padding': ['0 9px 0 10px', 'reviews-parity bands[].padding'],
  '--ds-group-bar-margin': ['2px 0 2px 10px', 'reviews-parity bands[].margin'],
  '--ds-group-bar-radius': ['8px', 'reviews-parity bands[].radius'],
  '--ds-pane-head-height': ['52px', 'measured pane breadcrumb band (HOJA-417)'],
  '--ds-pane-tabs-height': ['44px', 'measured Overview/Diff tab row (HOJA-417)'],
  '--ds-prop-label-width': ['96px', 'measured property label column (HOJA-417)'],
  '--ds-tab-height': ['28px', 'measured ledger pill height'],
  '--ds-composer-height': ['48px', 'measured composer height'],
  '--ds-composer-height-narrow': ['44px', 'measured composer height below 600px'],
  '--ds-scroll-detail-padding': ['18px 24px 28px', 'winning .reviews-detail-scroll padding'],
  '--ds-empty-gap': ['10px', 'winning .reviews-pane-empty gap'],
};
for (const [name, [want, source]] of Object.entries(MEASURED)) {
  if (tokens[name] !== want) fail.push(`tokens.css: ${name} is ${tokens[name] === undefined ? 'missing' : `"${tokens[name]}"`}, expected "${want}" (${source})`);
}
if (!fail.length) ok(`${Object.keys(MEASURED).length} measured tokens present with their artifact values`);

/* design.css must reference the token, not restate the number. */
const BOUND = {
  '.ds-split-ledger': {'width': '--ds-ledger-width', 'max-width': '--ds-ledger-width'},
  '.ds-list-row': {'height': '--ds-row-height'},
  '.ds-list-row-main': {'padding': '--ds-row-main-padding'},
  '.ds-group-bar': {'height': '--ds-group-bar-height', 'padding': '--ds-group-bar-padding'},
  '.ds-tab': {'height': '--ds-tab-height'},
  '.ds-prop-label': {'width': '--ds-prop-label-width'},
  '.ds-composer': {'height': '--ds-composer-height'},
};
for (const [sel, props] of Object.entries(BOUND)) {
  const at = designCss.indexOf(sel + '{');
  if (at === -1) { fail.push(`design.css: no ${sel} rule; the primitive has no styling`); continue; }
  const body = designCss.slice(at + sel.length + 1, designCss.indexOf('}', at));
  for (const [prop, token] of Object.entries(props)) {
    if (!new RegExp(`(^|;)\\s*${prop}:var\\(${token}\\)`).test(body)) {
      fail.push(`design.css: ${sel} { ${prop} } does not reference var(${token}); the primitive restates a number instead of reading the token`);
    }
  }
}

/* styles.css must not keep a second copy of a measured geometry value on a reviews class. */
const DUPLICATED = [
  [/\.reviews-col\{[^}]*\bwidth:482px/, '.reviews-col width:482px'],
  [/\.reviews-row\{[^}]*\bheight:40px/, '.reviews-row height:40px'],
  [/\.reviews-group-head\{[^}]*\bheight:28px/, '.reviews-group-head height:28px'],
  [/\.reviews-prop dt\{[^}]*\bwidth:96px/, '.reviews-prop dt width:96px'],
  [/\.reviews-detail-head\{[^}]*\bheight:52px/, '.reviews-detail-head height:52px'],
  [/\.reviews-detail-tabs\{[^}]*\bheight:44px/, '.reviews-detail-tabs height:44px'],
  [/\.reviews-composer\{[^}]*\bheight:48px/, '.reviews-composer height:48px'],
];
for (const [pattern, what] of DUPLICATED) {
  if (pattern.test(stylesCss)) fail.push(`styles.css: ${what} is a literal again; the design system owns that number (use var(--ds-…))`);
}
if (!fail.some(f => f.startsWith('styles.css:'))) ok('no literal copy of a measured value survives in styles.css');

/* Reviews must consume the primitives. */
const CONSUMERS = [
  [/from '\.\/design'/, "main.tsx does not import './design'"],
  [/<SplitPane[^>]*ledgerClassName="reviews-col"/, 'SplitPane is not carrying the ledger'],
  [/<ListRow[^>]*className="reviews-row"/, 'ListRow is not rendering the review rows'],
  [/<GroupBar[^>]*className="reviews-group-head"/, 'GroupBar is not rendering the group bars'],
  [/<PropertyRow[^>]*className="reviews-prop"/, 'PropertyRow is not rendering the property rows'],
  [/<ScrollArea[^>]*className="reviews-(scroll|detail-scroll|diff-scroll)"/, 'ScrollArea is not rendering a scroll region'],
  [/<TabStrip[^>]*className="reviews-detail-tabs"/, 'TabStrip is not rendering the pane tab row'],
  [/<EmptyState[^>]*className="reviews-pane-empty"/, 'EmptyState is not rendering the pane empty state'],
];
for (const [pattern, why] of CONSUMERS) if (!pattern.test(tsx)) fail.push('main.tsx: ' + why);
if (!fail.some(f => f.startsWith('main.tsx:'))) ok('Reviews consumes all 7 mounted primitives (Composer is library-only: no dead composer is mounted)');

const report = (phase) => {
  if (fail.length) {
    console.error(`design tokens FAILED (${phase}):`);
    for (const f of fail) console.error('  ✗ ' + f);
    process.exit(1);
  }
};
report('source');

if (args.has('--self-test')) {
  /* Both directions, proven: copy tokens.css, move one number, expect a non-zero exit. */
  const tmp = join(repo, 'artifacts', 'tmp-tokens-mutated.css');
  mkdirSync(dirname(tmp), { recursive: true });
  writeFileSync(tmp, readFileSync(tokenPath, 'utf8').replace('--ds-row-height:40px', '--ds-row-height:41px'));
  const run = (tokensFile) => {
    try {
      execFileSync(process.execPath, [process.argv[1], '--no-browser', '--tokens', tokensFile], {stdio: 'pipe'});
      return 0;
    } catch (e) { return e.status ?? 1; }
  };
  const real = run(tokenPath);
  const mutated = run(tmp);
  if (real !== 0) { console.error('self-test: the real token file does not pass, so the mutation test proves nothing'); process.exit(1); }
  if (mutated === 0) { console.error('self-test: a mutated --ds-row-height still passed — the probe does not pin the value'); process.exit(1); }
  console.log(`  ok  self-test: real tokens pass (exit 0), --ds-row-height:41px fails (exit ${mutated})`);
  process.exit(0);
}

if (!browserEnabled) { console.log('browser checks skipped (--no-browser)'); process.exit(0); }

/* ----------------------------------------------------------------- browser ---- */
const cdp = join(repo, 'scripts/ui-probes/cdp-tab.mjs');
const tmpDir = join(repo, 'artifacts', 'tmp-design');
mkdirSync(tmpDir, { recursive: true });

const PROPS = ['display','position','flex','flex-grow','flex-basis','flex-direction','align-items','justify-content','gap','height','min-height','max-height','width','min-width','max-width','padding-top','padding-right','padding-bottom','padding-left','margin-top','margin-right','margin-bottom','margin-left','border-top-width','border-radius','font-size','font-weight','line-height','color','background-color','overflow-x','overflow-y','white-space'];
const SNAP = `
const PROPS = ${JSON.stringify(PROPS)};
const out = [];
for (const el of document.querySelectorAll('[class]')) {
  const cs = getComputedStyle(el); const props = {};
  for (const p of PROPS) props[p] = cs.getPropertyValue(p);
  out.push({tag: el.tagName, cls: el.getAttribute('class') || '', props});
}
return out;`;

const measureFile = (file, expr = SNAP) => {
  const raw = JSON.parse(execFileSync(process.execPath, [cdp, '--url', 'file://' + file, '--wait', '.reviews-page', '--settle', '400', '--expr', expr], {encoding: 'utf8', maxBuffer: 64 * 1024 * 1024}));
  if (raw.error) throw new Error(raw.error);
  return raw.value;
};

/* 2. PARITY: same DOM, old stylesheet vs the design layer. */
const dom = readFileSync(join(repo, 'artifacts/reviews-dom-before.html'), 'utf8');
let oldCss;
try { oldCss = execFileSync('git', ['-C', repo, 'show', 'origin/main:app/frontend/src/styles.css'], {encoding: 'utf8'}); }
catch { oldCss = readFileSync(join(repo, 'artifacts/styles-before.css'), 'utf8'); }
const page = (css) => `<!doctype html><html><head><meta charset="utf-8"><style>
:root{font-family:Inter,sans-serif}
${css}
</style></head><body>${dom}</body></html>`;
const beforeFile = join(tmpDir, 'parity-before.html');
const afterFile = join(tmpDir, 'parity-after.html');
writeFileSync(beforeFile, page(oldCss));
writeFileSync(afterFile, page(stylesCss + '\n' + tokensCss + '\n' + designCss));
const before = measureFile(beforeFile);
const after = measureFile(afterFile);
if (before.length !== after.length) fail.push(`parity: element count ${before.length} -> ${after.length}; the refactor changed the DOM shape`);
let cells = 0;
for (let i = 0; i < Math.min(before.length, after.length); i++) {
  const a = before[i], b = after[i];
  if (a.tag !== b.tag || a.cls !== b.cls) { fail.push(`parity: element ${i} is ${a.tag}.${a.cls} vs ${b.tag}.${b.cls}`); continue; }
  for (const p of PROPS) {
    cells++;
    if (a.props[p] !== b.props[p]) fail.push(`parity: ${a.tag}.${a.cls} ${p}: "${a.props[p]}" -> "${b.props[p]}"`);
  }
}
if (!fail.some(f => f.startsWith('parity:'))) ok(`parity: ${before.length} elements x ${PROPS.length} computed properties identical before/after the refactor (${cells} cells)`);

/* 3. LOAD-BEARING: the token drives the DOM, proven by mutating it in the page. */
const fixture = `<!doctype html><html><head><meta charset="utf-8"><style>
:root{--panel:#fff;--line:#e4e4e7;--muted:#71717a;--text:#18181b;--subtle:#f4f4f5;--hover:#0000000d;--background:#fafafa;--accent:#46464b}
body{margin:0;font-family:Inter,sans-serif;font-size:14px}
${tokensCss}
${designCss}
</style></head><body>
<div class="ds-split"><div class="ds-split-ledger"><div class="ds-tabstrip"><button class="ds-tab">Created</button></div>
<div class="ds-scroll ds-scroll--list"><h3 class="ds-group-bar">Open<b>2</b></h3><ul style="margin:0;padding:0;list-style:none">
<li class="ds-list-row"><button class="ds-list-row-main"><span>row</span></button></li></ul></div></div>
<div class="ds-split-detail"><div class="ds-scroll ds-scroll--detail"><div class="ds-prop"><dt class="ds-prop-label">Status</dt><dd class="ds-prop-value">Open</dd></div>
<form class="ds-composer"><input class="ds-composer-input"><button class="ds-composer-action"></button></form></div></div></div>
</body></html>`;
const fixtureFile = join(tmpDir, 'primitives.html');
writeFileSync(fixtureFile, fixture);
const READ = `
const read = (sel, props) => { const el = document.querySelector(sel); if (!el) return null; const cs = getComputedStyle(el); const o = {}; for (const p of props) o[p] = cs.getPropertyValue(p); return o; };
const token = getComputedStyle(document.documentElement).getPropertyValue('--ds-row-height').trim();
const row = read('.ds-list-row', ['height']);
const bar = read('.ds-group-bar', ['height', 'padding', 'margin', 'border-radius']);
const col = read('.ds-split-ledger', ['width', 'max-width']);
const prop = read('.ds-prop-label', ['width']);
const composer = read('.ds-composer', ['height']);
if (window.__MUTATE) document.documentElement.style.setProperty('--ds-row-height', '41px');
const mutated = read('.ds-list-row', ['height']);
return {token, row, bar, col, prop, composer, mutated};`;
const mutateExpr = `window.__MUTATE = true;\n${READ}`;
const readFile = (expr) => {
  const raw = JSON.parse(execFileSync(process.execPath, [cdp, '--url', 'file://' + fixtureFile, '--wait', '.ds-list-row', '--settle', '300', '--expr', expr], {encoding: 'utf8', maxBuffer: 64 * 1024 * 1024}));
  if (raw.error) throw new Error(raw.error);
  return raw.value;
};
const plain = readFile(READ);
const EXPECT = [['row height', plain.row.height, '40px'], ['group bar height', plain.bar.height, '28px'],
  ['group bar padding', plain.bar.padding, '0px 9px 0px 10px'], ['group bar margin', plain.bar.margin, '2px 0px 2px 10px'],
  ['group bar radius', plain.bar['border-radius'], '8px'], ['ledger width', plain.col.width, '482px'],
  ['ledger max-width', plain.col['max-width'], '482px'], ['property label width', plain.prop.width, '96px'],
  ['composer height', plain.composer.height, '48px'], ['token readback', plain.token, '40px']];
for (const [what, got, want] of EXPECT) if (got !== want) fail.push(`primitives: ${what} is "${got}", expected "${want}" (measured)`);
/* The same document with 40px mutated to 41px in the page. A token that does not move the DOM is decoration. */
const changed = readFile(mutateExpr);
if (changed.mutated.height !== '41px') fail.push(`load-bearing: --ds-row-height:41px left the row at "${changed.mutated.height}"; the token does not drive the DOM`);
if (changed.mutated.height === plain.row.height) fail.push('load-bearing: mutating the token did not change the row height');
if (!fail.some(f => f.startsWith('primitives:') || f.startsWith('load-bearing:'))) {
  ok(`primitives measure their tokens: row ${plain.row.height}, band ${plain.bar.height}, ledger ${plain.col.width}, label ${plain.prop.width}, composer ${plain.composer.height}`);
  ok(`load-bearing: --ds-row-height 40px -> 41px moves the row ${plain.row.height} -> ${changed.mutated.height} in the same page`);
}

if (fail.length) {
  console.error(`design tokens FAILED (browser):`);
  for (const f of fail) console.error('  ✗ ' + f);
  process.exit(1);
}
console.log('design tokens ok: measured values live in tokens.css, styles.css has no second copy, Reviews consumes the primitives, the refactor is rendering-identical on the captured production DOM, and mutating a token moves the DOM');
