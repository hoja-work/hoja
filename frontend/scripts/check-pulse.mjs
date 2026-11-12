#!/usr/bin/env node
/**
 * Pulse gate (HOJA-420).
 *
 * Three independent questions, none answered by an opinion:
 *
 *  1. COMPOSITION — does PulsePage consume the design primitives, and does its CSS read
 *     the measured tokens instead of restating them? A literal here is a second source
 *     of truth; the screen drifts the moment the token moves.
 *  2. CONTRACT — does every degraded capability say so? Pulse has no ranking, no custom
 *     feeds and no comments in Hoja; a silent approximation would be a lie.
 *  3. GEOMETRY — a fixture built from the same classes is measured in a real browser at
 *     390/1024/1440. Tab, era band, avatar and property label must equal the tokens, and
 *     document.scrollWidth must not exceed innerWidth.
 *
 * Run against a pristine tree with `--root <frontend dir>` to prove the gate fails before
 * the change: node scripts/check-pulse.mjs --root /path/to/untouched/app/frontend
 *
 *   node scripts/check-pulse.mjs               # source + browser
 *   node scripts/check-pulse.mjs --no-browser  # source only
 */
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const argRoot = (() => { const i = process.argv.indexOf('--root'); return i === -1 ? null : process.argv[i + 1]; })();
const frontend = argRoot ? argRoot : join(here, '..');
const repo = join(frontend, '..', '..');
const noBrowser = process.argv.includes('--no-browser');
const fail = [];
const ok = (m) => console.log('  ok  ' + m);
const read = (p) => readFileSync(p, 'utf8');

const tsx = read(join(frontend, 'src/main.tsx'));
const styles = read(join(frontend, 'src/styles.css'));
const tokensCss = read(join(frontend, 'src/design/tokens.css'));
const designCss = read(join(frontend, 'src/design/design.css'));

/* ---------------------------------------------------------------- 1. composition ---- */
const start = tsx.indexOf('function PulsePage(');
const end = tsx.indexOf('export function StatusIcon');
if (start === -1 || end === -1 || end < start) fail.push('main.tsx: PulsePage not found; the gate cannot inspect it');
const pulse = start !== -1 && end > start ? tsx.slice(start, end) : '';

const CONSUMERS = [
  [/<TabStrip\b/, 'TabStrip is not rendering the view tabs'],
  [/<Tab\b/, 'Tab is not rendering a view tab'],
  [/<GroupBar\b[^>]*as="h2"/, 'GroupBar is not rendering the era band at heading level 2'],
  [/<PropertyRow\b[^>]*className="pulse-prop"/, 'PropertyRow is not rendering the metadata rows'],
  [/<EmptyState\b[^>]*className="pulse-status"/, 'EmptyState is not rendering the no-update state'],
  [/<ScrollArea\b[^>]*className="pulse-scroll"/, 'ScrollArea is not owning the feed scroll'],
];
for (const [re, why] of CONSUMERS) if (!re.test(pulse)) fail.push('main.tsx: ' + why);
if (/<SplitPane\b/.test(pulse)) {
  fail.push('main.tsx: Pulse mounts SplitPane, but Pulse is a single-column feed. ' +
    'Reusing the numbers does not mean reusing a ledger/detail layout; record the omission in PULSE-SPEC.md.');
}
if (!fail.length) ok('Pulse composes TabStrip/Tab/GroupBar/PropertyRow/EmptyState/ScrollArea and does not fake a split view');

/* The measured contracts. Each token is named with the artifact it came from. */
const MEASURED = {
  '--ds-feed-width': '776px',
  '--ds-feed-tab-height': '24px',
  '--ds-feed-tab-radius': '9999px',
  '--ds-feed-tab-padding': '0 8px',
  '--ds-feed-avatar-size': '24px',
  '--ds-feed-meta-size': '12px',
  '--ds-feed-meta-line': '16.8px',
  '--ds-feed-page-title-size': '13px',
  '--ds-group-bar-height': '28px',
  '--ds-prop-label-width': '96px',
};
const tokens = {};
for (const m of tokensCss.matchAll(/(--ds-[a-z0-9-]+)\s*:\s*([^;]+);/g)) tokens[m[1]] = m[2].trim();
for (const [name, want] of Object.entries(MEASURED)) {
  if (tokens[name] !== want) fail.push(`tokens.css: ${name} is ${tokens[name] === undefined ? 'missing' : `"${tokens[name]}"`}, expected "${want}"`);
}
if (!Object.keys(MEASURED).some((n) => tokens[n] === undefined)) ok('measured Pulse tokens are present with their artifact values');

const BOUND = [
  ['.pulse-heading', 'height', '--ds-tabstrip-height'],
  ['.pulse-tabs .ds-tab', 'height', '--ds-feed-tab-height'],
  ['.pulse-tabs .ds-tab', 'padding', '--ds-feed-tab-padding'],
  ['.pulse-tabs .ds-tab', 'border-radius', '--ds-feed-tab-radius'],
  ['.pulse-era', 'min-height', '--ds-group-bar-height'],
  ['.pulse-avatar', 'width', '--ds-feed-avatar-size'],
  ['.pulse-changes .ds-prop-label', 'width', '--ds-prop-label-width'],
  ['.pulse-content', 'width', '--ds-feed-width'],
];
for (const [sel, prop, token] of BOUND) {
  const at = styles.indexOf(sel + '{');
  if (at === -1) { fail.push(`styles.css: no ${sel} rule`); continue; }
  const body = styles.slice(at, styles.indexOf('}', at));
  if (!new RegExp(`(^|[;{])\\s*${prop}\\s*:\\s*[^;]*var\\(${token}\\)`).test(body)) {
    fail.push(`styles.css: ${sel} { ${prop} } does not read var(${token}); the number is restated`);
  }
}
const pulseStart = styles.indexOf('.workspace-content:has(.pulse-page)');
const pulseEnd = styles.indexOf('.pulse-approx-note{');
const pulseCss = pulseStart === -1 ? '' : styles.slice(pulseStart, pulseEnd === -1 ? styles.length : pulseEnd);
if (pulseCss && /#[0-9a-fA-F]{3,8}\b/.test(pulseCss)) fail.push('styles.css: the Pulse block hard-codes a hex colour; use an app token');
if (!fail.some((f) => f.startsWith('styles.css:'))) ok('Pulse CSS reads the measured tokens and hard-codes no hex colour');

/* ------------------------------------------------------------- 2. honest surfaces ---- */
const NOTES = [
  ['ranking is not available', 'the For me / Popular ranking approximation is not labelled'],
  ['Custom Pulse views are not available', 'the custom-feed control does not explain its absence'],
  ['Comments on project updates are not available', 'the comments affordance does not explain its absence'],
  ['subscriptions are not available', 'the subscription control does not explain its absence'],
  ['Task activity is separate from Pulse', 'the empty state does not distinguish Pulse from task activity'],
];
for (const [needle, why] of NOTES) if (!pulse.includes(needle)) fail.push('main.tsx: ' + why);
if (!fail.some((f) => f.startsWith('main.tsx: '))) ok('every degraded capability is labelled instead of silently approximated');

/* ----------------------------------------------------------------- 3. geometry ---- */
const report = () => {
  if (fail.length) {
    console.error('pulse gate FAILED:');
    for (const f of fail) console.error('  ✗ ' + f);
    process.exit(1);
  }
};
report();
if (noBrowser) { console.log('browser geometry skipped (--no-browser)'); process.exit(0); }

const cdp = join(repo, 'scripts/ui-probes/cdp-tab.mjs');
const outDir = join(repo, 'artifacts', 'tmp-pulse');
mkdirSync(outDir, { recursive: true });

const FIXTURE = `<!doctype html><html><head><meta charset="utf-8"><style>
body{margin:0;background:var(--bg);font-family:Inter,system-ui,sans-serif}
${tokensCss}
${designCss}
${styles}
</style></head><body>
<main class="workspace-content"><div class="main"><section class="pulse-page" aria-label="Pulse">
<header class="pulse-heading"><h1>Pulse</h1></header>
<div class="ds-tabstrip pulse-tabs" role="tablist" aria-label="Pulse views">
<button class="ds-tab" role="tab" aria-selected="true">For me</button>
<button class="ds-tab" role="tab" aria-selected="false">Popular</button>
<button class="ds-tab" role="tab" aria-selected="false">Recent</button>
</div>
<div class="ds-scroll pulse-scroll"><div class="pulse-content">
<h2 class="ds-group-bar pulse-era">Recent</h2>
<article class="pulse-post"><header class="pulse-post-heading"><div>
<h3><a href="#">Hoja Cloud</a></h3>
<div class="pulse-meta"><span class="pulse-health">Project on track</span><span class="pulse-avatar" aria-hidden="true">L</span><span>Luis</span><time>Today</time></div>
</div></header><div class="pulse-post-body"><p>Update body.</p></div>
<dl class="pulse-changes"><div class="ds-prop pulse-prop"><dt class="ds-prop-label">Lead</dt><dd class="ds-prop-value">Luis</dd></div></dl>
</article></div></div>
</section></div></main></body></html>`;
const fixtureFile = join(outDir, 'pulse.html');
writeFileSync(fixtureFile, FIXTURE);

const EXPR = `
const rect = (sel) => { const el = document.querySelector(sel); if (!el) return null; const b = el.getBoundingClientRect(); return {w:+b.width.toFixed(2),h:+b.height.toFixed(2)}; };
const cs = (sel, prop) => { const el = document.querySelector(sel); return el ? getComputedStyle(el).getPropertyValue(prop).trim() : null; };
const token = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
return {
  tab: rect('.pulse-tabs .ds-tab'), era: rect('.pulse-era'), avatar: rect('.pulse-avatar'),
  metaRow: rect('.pulse-meta'), propRow: rect('.pulse-changes .ds-prop'),
  propLabel: rect('.pulse-changes .ds-prop-label'), content: rect('.pulse-content'),
  tokens: {tabH:token('--ds-feed-tab-height'), bandH:token('--ds-group-bar-height'), avatar:token('--ds-feed-avatar-size'), propW:token('--ds-prop-label-width'), width:token('--ds-feed-width'), propMinH:token('--ds-prop-min-height')},
  tabPadding: cs('.pulse-tabs .ds-tab','padding'), tabRadius: cs('.pulse-tabs .ds-tab','border-radius'),
  scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth,
  overflow: document.documentElement.scrollWidth > window.innerWidth,
};`;

const px = (s) => parseFloat(s);
for (const vp of ['390x844', '1024x768', '1440x900']) {
  let raw;
  try {
    raw = JSON.parse(execFileSync(process.execPath, [cdp, '--url', 'file://' + fixtureFile, '--wait', '.pulse-page', '--settle', '400', '--viewport', vp, '--expr', EXPR], { encoding: 'utf8' }));
  } catch (e) { fail.push(`geometry ${vp}: probe failed (${e.message.trim()})`); continue; }
  if (raw.error) { fail.push(`geometry ${vp}: ${raw.error}`); continue; }
  const v = raw.value;
  if (!v) { fail.push(`geometry ${vp}: no value returned`); continue; }
  if (v.overflow) fail.push(`geometry ${vp}: horizontal overflow (${v.scrollWidth} > ${v.innerWidth})`);
  if (v.tab?.h !== px(v.tokens.tabH)) fail.push(`geometry ${vp}: tab height ${v.tab?.h} != ${v.tokens.tabH}`);
  if (v.era?.h !== px(v.tokens.bandH)) fail.push(`geometry ${vp}: era band height ${v.era?.h} != ${v.tokens.bandH}`);
  if (v.avatar?.w !== px(v.tokens.avatar)) fail.push(`geometry ${vp}: avatar ${v.avatar?.w} != ${v.tokens.avatar}`);
  if (v.metaRow?.h !== px(v.tokens.avatar)) fail.push(`geometry ${vp}: meta row height ${v.metaRow?.h} != ${v.tokens.avatar}`);
  if (!(v.propRow?.h >= px(v.tokens.propMinH))) fail.push(`geometry ${vp}: property row height ${v.propRow?.h} < ${v.tokens.propMinH}`);
  if (v.propLabel?.w !== px(v.tokens.propW)) fail.push(`geometry ${vp}: property label ${v.propLabel?.w} != ${v.tokens.propW}`);
  if (vp === '1440x900' && v.content?.w !== px(v.tokens.width)) fail.push(`geometry 1440: content width ${v.content?.w} != ${v.tokens.width}`);
  writeFileSync(join(outDir, `pulse-${vp}.json`), JSON.stringify({ viewport: vp, value: v, capturedAt: new Date().toISOString() }, null, 2) + '\n');
}
if (!fail.some((f) => f.startsWith('geometry'))) ok('geometry at 390/1024/1440: tab=24, section band=28, meta row=24, property row>=20, avatar=24, label=96, feed=776, zero horizontal overflow');
report();
console.log('pulse gate ok');
