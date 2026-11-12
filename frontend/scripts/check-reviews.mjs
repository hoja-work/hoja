#!/usr/bin/env node
/**
 * Reviews split-view invariants.
 *
 * The Reviews page mirrors Linear's two-pane reviews screen: a fixed 482px ledger
 * column on the left and the detail pane on the right. Those numbers are load-bearing
 * because the pane gets whatever is left of the viewport, and because styles.css
 * carries legacy rules for `.reviews-row` (a bordered 40px card with a 6px bottom
 * margin) that are still in the file; the split view overrides them later in the
 * cascade. A reordering or a "tidy up" of styles.css silently resurrects the card
 * styling and the rows stop being a flat 40px list.
 *
 * This check reads the LAST declaration block for each selector, i.e. the one that
 * actually wins the cascade here, and pins the numbers the layout depends on.
 *
 * HOJA-417: those numbers are measurements, not guesses. scripts/ui-probes/reviews-parity.mjs
 * measures linear.app/hoja/reviews and the target through one probe body at 1440x900 and
 * writes artifacts/reviews-parity-<ts>.json; the row is 40px, the group band 28px, the state
 * glyph 14px and filled (4 paths open / 5 draft, fill #43bc58 / #6b6f76), the glyph sits 18px
 * into the row and the timestamp ends 8px from its right edge. The axis is
 * Ready to merge / Open / Draft. Every one of those values was pinned here at the wrong
 * number before, so this file is re-derived from the artifact rather than edited to taste.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const tsx = readFileSync(join(root, 'src/main.tsx'), 'utf8');
const css = readFileSync(join(root, 'src/styles.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

const failures = [];

/** The @-rule (or '') that contains the block starting at `index`. */
function atRuleOf(index) {
  const stack = [];
  for (let i = 0; i < index; i++) {
    const c = css[i];
    if (c === '{') {
      let end = i - 1;
      while (end >= 0 && /\s/.test(css[end])) end--;
      let start = end;
      while (start >= 0 && !'{};'.includes(css[start])) start--;
      const head = css.slice(start + 1, end + 1).trim();
      stack.push(head.startsWith('@') ? head : null);
    } else if (c === '}') {
      stack.pop();
    }
  }
  const atRules = stack.filter(Boolean);
  return atRules.length ? atRules[atRules.length - 1].replace(/\s+/g, ' ') : '';
}

/** Every declaration block whose selector is exactly `sel`, in source order, with the
 *  @-rule it lives under ('' for the unconditional rules). */
function blocks(sel) {
  const out = [];
  let i = 0;
  while ((i = css.indexOf(sel + '{', i)) !== -1) {
    const prev = i === 0 ? '' : css[i - 1];
    if (prev && /[A-Za-z0-9_-]/.test(prev)) { i += sel.length; continue; }
    const end = css.indexOf('}', i);
    if (end === -1) break;
    out.push({ body: css.slice(i + sel.length + 1, end), at: i, media: atRuleOf(i) });
    i = end;
  }
  return out;
}

function parse(body) {
  const map = {};
  for (const part of body.split(';')) {
    const at = part.indexOf(':');
    if (at === -1) continue;
    map[part.slice(0, at).trim()] = part.slice(at + 1).trim();
  }
  return map;
}

/** The unconditional ("base") winning block as a property map. Media-query blocks are
 *  responsive overrides, so the pinned base geometry must be read from these only. */
function declared(sel) {
  const found = blocks(sel).filter((b) => b.media === '');
  if (!found.length) return null;
  return parse(found[found.length - 1].body);
}

/** Every declaration of `sel` under an @media whose condition contains `frag`. */
function mediaBlocks(sel, frag) {
  return blocks(sel)
    .filter((b) => b.media.includes('@media') && b.media.includes(frag))
    .map((b) => ({ media: b.media, props: parse(b.body) }));
}

function expect(sel, prop, want, why = 'The split-view ledger geometry (482px column, flat 40px rows, 28px group bar) depends on it.') {
  const map = declared(sel);
  if (!map) return failures.push(`styles.css: no ${sel} rule found`);
  const got = map[prop];
  if (got !== want) {
    failures.push(
      `styles.css: ${sel} { ${prop} } is ${got === undefined ? 'missing' : `"${got}"`}, expected "${want}". ` + why
    );
  }
}

/** 1. The ledger column is a pinned 482px pane on the left. */
expect('.reviews-col', 'width', '482px');
const col = declared('.reviews-col');
if (col && !/482px/.test(col.flex || '')) {
  failures.push(`styles.css: .reviews-col { flex } is "${col.flex}", expected a 482px basis (e.g. "0 0 482px"); a shrinkable column resizes the pane`);
}

/** 2. Rows are a flat 40px list row (measured), not the legacy bordered card. Every base
 *     block that sets a height must agree: three generations of `.reviews-row` live in
 *     styles.css, and a stale one is exactly how 38px came back before. */
expect('.reviews-row', 'height', '40px', 'Linear rows measure 40px at 1440x900 (artifacts/reviews-parity-*.json).');
for (const b of blocks('.reviews-row').filter((x) => x.media === '')) {
  const h = parse(b.body).height;
  if (h !== undefined && h !== '40px') {
    failures.push(`styles.css: an earlier .reviews-row block still sets height:"${h}"; every base block must say 40px or the cascade can resurrect the old value`);
  }
}
const row = declared('.reviews-row') || {};
const border = row.border;
if (border !== undefined && !/^(0|0px|none)$/.test(border)) {
  failures.push(`styles.css: .reviews-row { border } is "${border}"; rows must stay flat (no border) like Linear's review list`);
}
if (row['border-width'] !== undefined && !/^0(px)?$/.test(row['border-width'])) {
  failures.push(`styles.css: .reviews-row { border-width } is "${row['border-width']}"; rows must stay flat (no border)`);
}
if (row['margin-bottom'] !== undefined && !/^0(px)?$/.test(row['margin-bottom'])) {
  failures.push(`styles.css: .reviews-row { margin-bottom } is "${row['margin-bottom']}"; that is the legacy card spacing and it breaks the 40px rhythm`);
}

/** 3. The group bar is a 28px band with the reference's insets and 2px rhythm. */
expect('.reviews-group-head', 'height', '28px', 'Linear group bars measure 28px at 1440x900 (artifacts/reviews-parity-*.json).');
{
  const band = declared('.reviews-group-head') || {};
  const want = { padding: '0 9px 0 10px', margin: '2px 0 2px 10px', 'border-radius': '8px' };
  for (const [prop, value] of Object.entries(want)) {
    if (band[prop] !== value) {
      failures.push(`styles.css: .reviews-group-head { ${prop} } is "${band[prop] ?? 'missing'}", expected "${value}" (measured from the reference band)`);
    }
  }
}
for (const b of blocks('.reviews-group-head').filter((x) => x.media === '')) {
  const h = parse(b.body).height;
  if (h !== undefined && h !== '28px') {
    failures.push(`styles.css: an earlier .reviews-group-head block still sets height:"${h}"; every base block must say 28px`);
  }
}

/** 4. The two-pane structure exists: .reviews-col + .reviews-pane, in JSX and CSS. */
for (const cls of ['reviews-col', 'reviews-pane']) {
  if (!new RegExp(`className="${cls}"`).test(tsx)) {
    failures.push(`main.tsx: no element with className="${cls}"; the reviews split view lost a pane`);
  }
  if (!declared('.' + cls)) {
    failures.push(`styles.css: no .${cls} rule found; the reviews split view lost a pane`);
  }
}


/** 5. The detail pane is real: breadcrumb band, tab row, property rows, activity, composer. */
for (const cls of ['reviews-detail', 'reviews-detail-head', 'reviews-detail-tabs', 'reviews-prop', 'reviews-activity', 'reviews-composer']) {
  if (!new RegExp(`className="${cls}"`).test(tsx)) {
    failures.push(`main.tsx: no element with className="${cls}"; the review detail pane lost a section`);
  }
  if (!declared('.' + cls)) {
    failures.push(`styles.css: no .${cls} rule found; the review detail pane lost a section`);
  }
}
if (!/role="tab"/.test(tsx) || !/reviews-tab on/.test(tsx)) {
  failures.push('main.tsx: the detail pane lost its Overview/Diff tab row (role="tab" + the selected tab)');
}
if (!declared('.reviews-pane-empty')) {
  failures.push('styles.css: no .reviews-pane-empty rule found; the pane lost its "select a review" state');
}

/** 6. Pane geometry: 52px breadcrumb band, 44px tab row, 96px property label column. */
expect('.reviews-detail-head', 'height', '52px', 'The pane breadcrumb band is 52px in the measured reference.');
expect('.reviews-detail-tabs', 'height', '44px', 'The Overview/Diff tab row is 44px in the measured reference.');
expect('.reviews-prop dt', 'width', '96px', 'The property label column is a fixed 96px in the measured reference.');

/** 7. Rows select locally instead of navigating: the click target is a button, and the
 *     GitHub link is a sibling anchor so the row does not lose its external affordance. */
for (const cls of ['reviews-row-main', 'reviews-row-ext']) {
  if (!new RegExp(`className="${cls}"`).test(tsx)) {
    failures.push(`main.tsx: no element with className="${cls}"; a review row must select in place and keep an explicit "open on GitHub" link`);
  }
}
if (/<a className="reviews-row"/.test(tsx)) {
  failures.push('main.tsx: the ledger row is an <a> again; clicking a row must select it instead of leaving Hoja');
}
if (row['height'] === undefined) {
  failures.push('styles.css: .reviews-row lost its 40px height; only the click target inside it may change');
}

const gateFile = readFileSync(join(root, 'src/reviews-gate.ts'), 'utf8');
const glyphFile = readFileSync(join(root, 'src/reviews-glyphs.ts'), 'utf8');

/** 8. Grouping axis is the reference's: Ready to merge / Open / Draft, in that order, and
 *     the readiness rule is defined once in src/reviews-gate.ts. The axis, the gate and the
 *     null rule are asserted separately: an axis without a gate is a label, and a gate
 *     without the null rule is a claim Hoja cannot prove. */
if (!/REVIEW_GROUP_NAMES = \['Ready to merge', 'Open', 'Draft'\] as const/.test(gateFile)) {
  failures.push("reviews-gate.ts: the axis must be REVIEW_GROUP_NAMES = ['Ready to merge','Open','Draft'] as const — the measured reference order (Ready to merge 1 / Open 7 / Draft 16, no Merged or Closed bucket)");
}
if (!/export function reviewGroupOf\(/.test(gateFile) || !/export function reviewGroupsOf</.test(gateFile)) {
  failures.push('reviews-gate.ts: reviewGroupOf/reviewGroupsOf are the single definition of the bucket; both must be exported');
}
for (const [needle, why] of [
  ["['clean', 'mergeable']", 'the ready merge states must be clean/mergeable'],
  ["['passing']", 'the ready check state must be passing'],
  ["['approved']", 'the ready review decision must be approved'],
  ["reason: 'merge-state-unknown'", 'a null merge state must resolve to Open with a nameable reason'],
  ["reason: 'checks-unknown'", 'a null check state must resolve to Open'],
  ["reason: 'review-unknown'", 'a null review decision must resolve to Open — Hoja cannot prove "no required reviews" from a null'],
  ["group: 'Draft'", 'a draft state must be decided before readiness'],
  ["group: 'Terminal'", 'merged/closed must be Terminal, not a group'],
]) {
  if (!gateFile.includes(needle)) failures.push(`reviews-gate.ts: missing ${JSON.stringify(needle)} — ${why}`);
}
if (/const REVIEW_GROUPS=\['Open','Draft','Merged','Closed'\]/.test(tsx) || !/import \{reviewGroupsOf\} from '\.\/reviews-gate';/.test(tsx)) {
  failures.push("main.tsx: the ledger must import reviewGroupsOf from ./reviews-gate; a local REVIEW_GROUPS array is how the axis drifted from the reference");
}
if (!/reviewGroupsOf\(links\)/.test(tsx)) {
  failures.push('main.tsx: the ledger must group with reviewGroupsOf(links) so every link is accounted for');
}
if (!/terminal\.length/.test(tsx)) {
  failures.push('main.tsx: the terminal rows the queue does not list must be reported, not silently dropped');
}

/** 8b. The state glyph is the reference's: 14px, filled, per-state path sets. The previous
 *      icon was one stroked path at 12px — outline where Linear is solid. */
if (!/REVIEW_GLYPH_PULL: readonly string\[\] = \[/.test(glyphFile) || !/REVIEW_GLYPH_DRAFT: readonly string\[\] = \[/.test(glyphFile)) {
  failures.push('reviews-glyphs.ts: the filled path sets (REVIEW_GLYPH_PULL / REVIEW_GLYPH_DRAFT) are gone');
}
if (!/export function reviewGlyphPaths\(/.test(glyphFile)) {
  failures.push('reviews-glyphs.ts: reviewGlyphPaths(state) is the only glyph selector');
}
{
  const pullPaths = (glyphFile.slice(glyphFile.indexOf('REVIEW_GLYPH_PULL'), glyphFile.indexOf('REVIEW_GLYPH_DRAFT')).match(/^  'M/gm) || []).length;
  const draftPaths = (glyphFile.slice(glyphFile.indexOf('REVIEW_GLYPH_DRAFT')).match(/^  'M/gm) || []).length;
  if (pullPaths !== 4) failures.push(`reviews-glyphs.ts: the open glyph must keep the reference's 4 paths, found ${pullPaths}`);
  if (draftPaths !== 5) failures.push(`reviews-glyphs.ts: the draft glyph must keep the reference's 5 paths, found ${draftPaths}`);
}
{
  const glyphAt = tsx.indexOf('function ReviewGlyph(');
  const glyphEnd = glyphAt === -1 ? -1 : tsx.indexOf('\nfunction ', glyphAt + 20);
  const glyph = glyphAt === -1 ? '' : tsx.slice(glyphAt, glyphEnd === -1 ? undefined : glyphEnd);
  if (!glyph) failures.push('main.tsx: no ReviewGlyph component; the ledger row lost its state icon');
  if (/stroke=/.test(glyph)) failures.push('main.tsx: ReviewGlyph must not stroke anything; the reference glyph is filled');
  if (!/fill="currentColor"/.test(glyph)) failures.push('main.tsx: ReviewGlyph must fill its paths with currentColor so the state colour comes from CSS');
  if (!/fillRule="evenodd"/.test(glyph)) failures.push('main.tsx: ReviewGlyph paths must carry fillRule="evenodd" like the reference vectors');
  if (!/width="14" height="14"/.test(glyph)) failures.push('main.tsx: ReviewGlyph must be 14x14 (measured glyph box)');
}
{
  const g = declared('.reviews-glyph') || {};
  const svgRule = declared('.reviews-glyph svg') || {};
  if (g['border'] !== undefined && !/^0(px)?$/.test(g.border)) {
    failures.push(`styles.css: .reviews-glyph { border } is "${g.border}"; the reference glyph has no outline`);
  }
  if (g.width !== '14px' || g.height !== '14px') {
    failures.push(`styles.css: .reviews-glyph is ${g.width}x${g.height}; the measured glyph box is 14x14`);
  }
  if (svgRule.width !== '14px') {
    failures.push(`styles.css: .reviews-glyph svg { width } is "${svgRule.width ?? 'missing'}"; the reference renders the icon at 14px`);
  }
  const openGlyph = declared('.reviews-glyph[data-state="open"]') || {};
  if (!/^#[0-9a-f]{6}$/i.test(openGlyph.color || '')) {
    failures.push('styles.css: .reviews-glyph[data-state="open"] needs the measured colour (#43bc58)');
  }
}

/** 9. The detail pane only renders stored pull-request fields — so the ReviewsLinks query
 *     must ask for every one of them. A field the query omits silently arrives as
 *     undefined and the pane degrades to "no diffstat" / "no checks recorded" even
 *     though the server stores the value. This is the exact regression that shipped once. */
const PR_FIELDS = [
  'additions', 'deletions', 'baseRef', 'headRef', 'behind', 'ahead', 'mergeable', 'mergeState',
  'reviewDecision', 'reviewers', 'checksState', 'checksTotal', 'checksPassing', 'checksFailing',
  'body', 'mergedAt', 'githubUpdatedAt',
];
const PR_CORE_FIELDS = ['id', 'kind', 'externalId', 'url', 'title', 'state', 'author', 'sha', 'environment', 'createdAt', 'updatedAt'];
const queryAt = tsx.indexOf('query ReviewsLinks{');
if (queryAt === -1) {
  failures.push('main.tsx: no ReviewsLinks query found; the Reviews page lost its data source');
} else {
  const selection = tsx.slice(queryAt, tsx.indexOf('.then(', queryAt));
  for (const field of [...PR_CORE_FIELDS, ...PR_FIELDS]) {
    if (!new RegExp(`\\b${field}\\b`).test(selection)) {
      failures.push(`main.tsx: ReviewsLinks does not select githubLinks.${field}; the pane renders it, so it would regress to a labelled unknown while the server stores a real value`);
    }
  }
  if (!/githubLinks\s*\{/.test(selection)) {
    failures.push('main.tsx: ReviewsLinks must select githubLinks (the pull-request rows come from there)');
  }
}

/** 10. The TS type carries the same fields, so tsc keeps the render honest. */
const typeAt = tsx.indexOf('export type ReviewLink=');
const typeLine = typeAt === -1 ? '' : tsx.slice(typeAt, tsx.indexOf('};', typeAt) + 2);
if (!typeLine) {
  failures.push('main.tsx: no `export type ReviewLink=` line; the Reviews data contract is gone');
} else {
  for (const field of PR_FIELDS) {
    if (!new RegExp(`\\b${field}\\?:`).test(typeLine)) {
      failures.push(`main.tsx: ReviewLink is missing the optional ${field} field that the pane renders`);
    }
  }
}

/** 11. The pane renders the stored values instead of hardcoded unknowns. Each assertion
 *     pairs the real branch with the unknown branch, so deleting either one fails. */
const paneAt = tsx.indexOf('function ReviewsShell(');
if (paneAt === -1) {
  failures.push('main.tsx: no ReviewsShell component found');
} else {
  const paneEnd = tsx.indexOf('\nfunction ', paneAt + 30);
  const pane = tsx.slice(paneAt, paneEnd === -1 ? undefined : paneEnd);
  const need = (pattern, why) => { if (!pattern.test(pane)) failures.push(`main.tsx (ReviewsShell): ${why}`); };

  need(/reviewDiffstat\(sel\)/, 'the breadcrumb must branch on reviewDiffstat(sel) (additions/deletions) instead of always printing "no diffstat"');
  need(/reviews-diffstat-add[^]*\+\{sel\.additions\}/, 'the diffstat must print +additions in .reviews-diffstat-add');
  need(/reviews-diffstat-del[^]*−\{sel\.deletions\}/, 'the diffstat must print −deletions in .reviews-diffstat-del');
  need(/data-known="true"/, 'the real diffstat must be marked data-known="true" so the "no diffstat" pill styling does not leak onto real numbers');
  need(/checksTotal>0/, 'the Checks row must switch on checksTotal>0');
  need(/reviews-check-dot/, 'the Checks row needs a state dot when check runs exist');
  need(/checksPassing[^]*passing/, 'the Checks row must print "<n> passing"');
  need(/\{checksFailing\} facing|\{checksFailing\} failing/, 'the Checks row must print "<n> failing" when failing checks exist');
  need(/\{checksTotal\} total/, 'the Checks row must print the run total');
  need(/no checks recorded/, 'the Checks row must keep an explicit "no checks recorded" empty state');
  need(/hasRefs/, 'the Branch row must branch on hasRefs (baseRef/headRef)');
  need(/\{sel\.baseRef\|\|'\?'\}[^]*‹[^]*\{sel\.headRef\|\|'\?'\}/, 'the Branch row must render "baseRef ‹ headRef"');
  need(/aheadBehind/, 'the Branch row must render "N ahead / M behind" when those counts are non-null');
  need(/reviewerList/, 'the Reviewers row must render the stored reviewers list');
  need(/no reviews upstream/, 'the Reviewers row must say "no reviews upstream" when the list is empty');
  need(/\{bodyPreview\?<div className="reviews-desc">/, 'the description must render the stored body when present ({bodyPreview? <div className="reviews-desc">})');
  need(/reviewBodyPreview\(sel\?\.body\)/, 'the description preview must be derived from the stored body via reviewBodyPreview');
  need(/No pull request description is stored/, 'the description keeps its explicit empty state');
  need(/reviewStateLabel\(sel\.state\)/, 'Status must stay the primary value derived from the link state');
  need(/merge state: /, 'the GitHub merge state may only appear as a secondary tag next to the link state');
}

/** 12. The new diffstat/checks/refs styling exists and the diff colours are not the muted ink. */
for (const sel of ['.reviews-diffstat-add', '.reviews-diffstat-del', '.reviews-check-dot', '.reviews-refs', '.reviews-aheadbehind']) {
  if (!declared(sel)) failures.push(`styles.css: no ${sel} rule found; the rebuilt review detail row lost its styling`);
}
const add = declared('.reviews-diffstat-add') || {};
const del = declared('.reviews-diffstat-del') || {};
if (!/^#[0-9a-f]{6}$/i.test(add.color || '')) failures.push('styles.css: .reviews-diffstat-add needs an explicit green colour');
if (!/^#[0-9a-f]{6}$/i.test(del.color || '')) failures.push('styles.css: .reviews-diffstat-del needs an explicit red colour');
if ((add.color || '').toLowerCase() === (del.color || '').toLowerCase()) failures.push('styles.css: the additions and deletions colours must differ');

/** 13. Responsive contract, measured from Linear (not guessed):
 *      - the ledger column is capped, never a bare fixed width: 482px is a max, and it
 *        must be able to grow to the full page when the detail pane is gone;
 *      - the detail pane is dropped at 1227px and below (Linear: pane present at 1228,
 *        absent at 1227), and a selected review opens over the ledger instead;
 *      - the workspace sidebar stops being a column at 889px (Linear: 244px at 890,
 *        off-canvas at 880) and becomes the off-canvas drawer that already exists;
 *      - the page height can never be a bare 100dvh, which ignores the header.
 */
const colBase = declared('.reviews-col') || {};
if (colBase['max-width'] !== '482px') {
  failures.push(`styles.css: .reviews-col { max-width } is ${colBase['max-width'] === undefined ? 'missing' : `"${colBase['max-width']}"`}; 482px must be a max, not only a width, or the ledger cannot give space back`);
}
const colMedia = blocks('.reviews-col').filter((b) => b.media !== '');
for (const b of colMedia) {
  const props = parse(b.body);
  if (props.width && props.width !== 'auto' && props.width !== '482px') {
    failures.push(`styles.css: ${b.media} pins .reviews-col width="${props.width}"; responsive overrides must grow the column (flex/max-width), not pin a new fixed width`);
  }
}
if (colBase['min-width'] !== '0') {
  failures.push(`styles.css: .reviews-col needs min-width:0 so it can shrink below its 482px basis instead of overflowing`);
}

const onePane = mediaBlocks('.reviews-pane', 'max-width:1227px');
if (!onePane.length) {
  failures.push('styles.css: no @media (max-width:1227px) block for .reviews-pane; Linear drops the two-pane split at 1227px (pane present at 1228, absent at 1227)');
} else {
  const hide = onePane.find((b) => b.props.display === 'none');
  if (!hide) failures.push('styles.css: the 1227px block must set .reviews-pane { display:none }; that is the one-pane breakpoint');
  const overlay = mediaBlocks('.reviews-pane:has(.reviews-detail)', 'max-width:1227px');
  if (!overlay.length) {
    failures.push('styles.css: at one-pane widths a selected review must open over the ledger (.reviews-pane:has(.reviews-detail) inside the 1227px block)');
  } else if (overlay[0].props.position !== 'absolute' || overlay[0].props.inset !== '0') {
    failures.push('styles.css: the one-pane detail must be an absolute inset:0 overlay, not a column that squeezes the ledger');
  }
  const grow = mediaBlocks('.reviews-col', 'max-width:1227px')[0];
  if (!grow || grow.props['max-width'] !== 'none') {
    failures.push('styles.css: the 1227px block must release the ledger cap (.reviews-col { max-width:none }) so it becomes the full page');
  }
}

const drawer = mediaBlocks('body:has(.reviews-page) .sidebar', 'max-width:889px');
if (!drawer.length) {
  failures.push('styles.css: no @media (max-width:889px) drawer rule for the workspace sidebar; Linear collapses it between 890px (244px column) and 880px (off-canvas)');
} else {
  if (drawer[0].props.position !== 'fixed') failures.push('styles.css: below 889px the reviews sidebar must be position:fixed (a drawer), not a column');
  if (!/translateX\(-1/.test(drawer[0].props.transform || '')) {
    failures.push(`styles.css: the 889px drawer must start off-canvas (translateX(-105%)); got "${drawer[0].props.transform || 'none'}"`);
  }
  const opened = mediaBlocks('body:has(.reviews-page) .sidebar.open', 'max-width:889px');
  if (!opened.length || !/^(none|translateX\(0\)|translateX\(0px\))$/.test(opened[0].props.transform || '')) {
    failures.push('styles.css: the 889px drawer needs a .sidebar.open rule that brings it back on screen');
  }
}

const pageBase = declared('.reviews-page') || {};
if (/100dvh|100vh/.test(pageBase.height || '')) {
  failures.push(`styles.css: .reviews-page { height:"${pageBase.height}" } breaks as soon as the header is visible or collapses; take the height from the flex chain instead`);
}
if ((pageBase.flex || '') !== '1 1 auto') {
  failures.push(`styles.css: .reviews-page { flex } is "${pageBase.flex}", expected "1 1 auto" so the page fills whatever the shell header and any error banner leave`);
}
const mainBase = declared('body:has(.reviews-page) .main') || {};
if (mainBase['flex-direction'] !== 'column') {
  failures.push(`styles.css: body:has(.reviews-page) .main { flex-direction } is "${mainBase['flex-direction']}"; it must be a column so an error banner stacks above the ledger instead of squeezing it`);
}
for (const [sel, prop] of [['body:has(.reviews-page) .app', 'display'], ['body:has(.reviews-page) .main', 'display']]) {
  const d = declared(sel);
  if (!d) { failures.push(`styles.css: no ${sel} rule found; the reviews page lost its full-bleed shell`); continue; }
  if (prop === 'display' && d.display !== 'flex') failures.push(`styles.css: ${sel} { display } is "${d.display}", expected flex so the page height is derived from the shell`);
}

if (!/className="reviews-nav-btn"/.test(tsx)) {
  failures.push('main.tsx: the reviews header lost its drawer button; below 889px there is no other way to open the sidebar (the page topbar is hidden)');
}
if (!/className="reviews-pane-close"/.test(tsx)) {
  failures.push('main.tsx: the one-pane detail needs a back button (.reviews-pane-close); without it the overlay cannot be dismissed');
}

/** 14. The Diff tab renders the stored pull-request file list. It used to be a disabled
 *      "not available" tab; the server now stores `files { path additions deletions status
 *      patch previousPath }`, so the tab must be live, the query must select those fields,
 *      the renderer must colour-code + and - lines from the patch itself, and a file whose
 *      patch is null must say so instead of drawing an empty body. */
const DIFF_FIELDS = ['path', 'additions', 'deletions', 'status', 'patch', 'previousPath'];
if (queryAt !== -1) {
  const selection = tsx.slice(queryAt, tsx.indexOf('.then(', queryAt));
  const filesAt = selection.indexOf('files {');
  if (filesAt === -1) {
    failures.push('main.tsx: ReviewsLinks does not select githubLinks.files; the Diff tab would have no data to render');
  } else {
    const filesSel = selection.slice(filesAt, selection.indexOf('}', filesAt) + 1);
    for (const field of DIFF_FIELDS) {
      if (!new RegExp(`\\b${field}\\b`).test(filesSel)) {
        failures.push(`main.tsx: ReviewsLinks does not select files.${field}; the diff renderer reads it`);
      }
    }
  }
}
if (typeAt !== -1 && !/files\?:ReviewDiffFile\[\]\|null/.test(typeLine)) {
  failures.push('main.tsx: ReviewLink is missing `files?:ReviewDiffFile[]|null`');
}
const diffTypeAt = tsx.indexOf('export type ReviewDiffFile=');
if (diffTypeAt === -1) {
  failures.push('main.tsx: no `export type ReviewDiffFile=` line; the diff data contract is gone');
} else {
  const diffType = tsx.slice(diffTypeAt, tsx.indexOf('};', diffTypeAt) + 2);
  for (const field of DIFF_FIELDS) {
    if (!new RegExp(`\\b${field}[?]?:`).test(diffType)) {
      failures.push(`main.tsx: ReviewDiffFile is missing ${field}`);
    }
  }
}

const diffTabAt = tsx.indexOf('id="reviews-tab-diff"');
if (diffTabAt === -1) {
  failures.push('main.tsx: no Diff tab (id="reviews-tab-diff"); the pane lost its second view');
} else {
  const tabStart = tsx.lastIndexOf('<button', diffTabAt);
  const tabTag = tsx.slice(tabStart, tsx.indexOf('</button>', diffTabAt));
  if (/\bdisabled\b/.test(tabTag)) {
    failures.push('main.tsx: the Diff tab is disabled again; it must render the stored file list (or an explicit empty state)');
  }
  if (!/role="tab"/.test(tabTag) || !/aria-selected=\{paneTab==='diff'\}/.test(tabTag)) {
    failures.push('main.tsx: the Diff tab must be role="tab" with aria-selected bound to the pane state');
  }
  if (!/onClick=\{\(\)=>setPaneTab\('diff'\)\}/.test(tabTag)) {
    failures.push("main.tsx: clicking the Diff tab must switch the pane body (onClick={()=>setPaneTab('diff')})");
  }
  if (!/aria-controls="reviews-panel"/.test(tabTag)) {
    failures.push('main.tsx: the Diff tab must wire aria-controls="reviews-panel" (the tabpanel)');
  }
}
if (!/role="tabpanel" id="reviews-panel" aria-labelledby="reviews-tab-diff"/.test(tsx)) {
  failures.push('main.tsx: the diff view needs its own role="tabpanel" region labelled by the Diff tab');
}
if (!/onKeyDown=\{onTabKeyDown\}/.test(tsx) || !/ArrowRight/.test(tsx)) {
  failures.push('main.tsx: the tab row must stay keyboard-driven (arrow keys move between Overview and Diff)');
}

const shellAt = tsx.indexOf('function ReviewsShell(');
const shellEnd = shellAt === -1 ? -1 : tsx.indexOf('\nfunction ', shellAt + 30);
const shell = shellAt === -1 ? '' : tsx.slice(shellAt, shellEnd === -1 ? undefined : shellEnd);
const diffNeed = (pattern, why) => { if (!pattern.test(shell)) failures.push(`main.tsx (ReviewsShell diff): ${why}`); };
diffNeed(/\{files\.length\} \{files\.length===1\?'file':'files'\} changed/, 'the diff summary must print "<n> file(s) changed"');
diffNeed(/reviewFileTotals\(files\)/, 'the diff summary must total additions/deletions from the file list');
diffNeed(/f\.previousPath\?/, 'a renamed file must show its previousPath');
diffNeed(/reviewStatusLabel\(f\.status\)/, 'each file needs a status badge');
diffNeed(/reviews-diff-file-head[^]*aria-expanded=\{open\}/, 'each file must be collapsible (aria-expanded on its header button)');
diffNeed(/reviewPatchLines\(f\.patch\)/, 'the patch body must come from the stored patch, split into lines');
diffNeed(/reviewLineKind\(line\)/, 'each patch line must be classified before it is styled');
diffNeed(/`reviews-diff-line reviews-diff-\$\{kind\}`/, 'each patch line must carry reviews-diff-<kind> so + / - lines are colour-coded');
diffNeed(/Binary file or too large to display/, 'a file with patch:null must carry an explicit "binary or too large" note');
diffNeed(/No file data stored for this pull request/, 'an empty/absent file list must show an honest empty state instead of a fake diff');
for (const [kind, prefix] of [['add', '+'], ['del', '-']]) {
  if (!new RegExp(`if\\(line\\.startsWith\\('\\${prefix}'\\)\\)return '${kind}'`).test(tsx)) {
    failures.push(`main.tsx: a patch line starting with "${prefix}" must classify as "${kind}"`);
  }
}
if (!/if\(line\.startsWith\('@@'\)\)return 'hunk'/.test(tsx)) {
  failures.push("main.tsx: @@ hunk headers must classify as 'hunk' (muted), not as context");
}
for (const sel of ['.reviews-diff-add', '.reviews-diff-del', '.reviews-diff-hunk', '.reviews-diff-line', '.reviews-diff-patch']) {
  if (!declared(sel)) failures.push(`styles.css: no ${sel} rule found; the diff renderer lost its styling`);
}
const diffAdd = declared('.reviews-diff-add') || {};
const diffDel = declared('.reviews-diff-del') || {};
if (!/^#[0-9a-f]{6}$/i.test(diffAdd.color || '')) failures.push('styles.css: .reviews-diff-add needs an explicit green colour');
if (!/^#[0-9a-f]{6}$/i.test(diffDel.color || '')) failures.push('styles.css: .reviews-diff-del needs an explicit red colour');
if ((diffAdd.color || '').toLowerCase() === (diffDel.color || '').toLowerCase()) {
  failures.push('styles.css: + and - patch lines must not share a colour');
}
if ((declared('.reviews-diff-line') || {})['white-space'] !== 'pre') {
  failures.push('styles.css: .reviews-diff-line must preserve indentation with white-space:pre');
}

/** 15. The diff must scroll inside itself: a long line may not widen the page, at any
 *     width, including inside the <=1227px overlay where the pane is the whole page. */
const patchRule = declared('.reviews-diff-patch') || {};
if (patchRule['overflow-x'] !== 'auto') {
  failures.push(`styles.css: .reviews-diff-patch { overflow-x } is "${patchRule['overflow-x']}"; monospace lines must scroll in their own container`);
}
if ((declared('.reviews-diff') || {})['min-width'] !== '0' || (declared('.reviews-diff-scroll') || {})['min-width'] !== '0') {
  failures.push('styles.css: .reviews-diff/.reviews-diff-scroll need min-width:0 so the patch cannot push the pane wider than the page');
}
if ((declared('.reviews-diff') || {}).overflow !== 'hidden') {
  failures.push('styles.css: .reviews-diff must clip its own overflow; the pane never scrolls sideways');
}


if (failures.length) {
  console.error('reviews invariants failed:\n' + failures.map(f => '  ✗ ' + f).join('\n'));
  process.exit(1);
}
console.log('reviews invariants ok: 482px capped ledger, flat 40px rows, 28px group bar, filled 14px state glyphs, groups Ready to merge / Open / Draft from one gate, two panes above 1227px, one-pane overlay below it, 889px sidebar drawer, flex-derived page height, detail pane (52px breadcrumb, 44px tabs, 96px labels), ReviewsLinks selects all 17 pull-request detail fields and the pane renders them, and the Diff tab renders the stored file list (files{path,additions,deletions,status,patch,previousPath} in the query and the type, live tab, +green/-red patch lines inside their own horizontal scroller, patch:null note, honest empty state)');
