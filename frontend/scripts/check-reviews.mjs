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
 * styling and the rows stop being a flat 38px list.
 *
 * This check reads the LAST declaration block for each selector, i.e. the one that
 * actually wins the cascade here, and pins the three numbers the layout depends on.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const tsx = readFileSync(join(root, 'src/main.tsx'), 'utf8');
const css = readFileSync(join(root, 'src/styles.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');

const failures = [];

/** Every declaration block whose selector is exactly `sel`, in source order. */
function blocks(sel) {
  const out = [];
  let i = 0;
  while ((i = css.indexOf(sel + '{', i)) !== -1) {
    const prev = i === 0 ? '' : css[i - 1];
    if (prev && /[A-Za-z0-9_-]/.test(prev)) { i += sel.length; continue; }
    const end = css.indexOf('}', i);
    if (end === -1) break;
    out.push({ body: css.slice(i + sel.length + 1, end), at: i });
    i = end;
  }
  return out;
}

/** The winning block (last in cascade order) as a property map. */
function declared(sel) {
  const found = blocks(sel);
  if (!found.length) return null;
  const map = {};
  for (const part of found[found.length - 1].body.split(';')) {
    const at = part.indexOf(':');
    if (at === -1) continue;
    map[part.slice(0, at).trim()] = part.slice(at + 1).trim();
  }
  return map;
}

function expect(sel, prop, want, why = 'The split-view ledger geometry (482px column, flat 38px rows, 22px group bar) depends on it.') {
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

/** 2. Rows are a flat 38px list row, not the legacy bordered card. */
expect('.reviews-row', 'height', '38px');
const row = declared('.reviews-row') || {};
const border = row.border;
if (border !== undefined && !/^(0|0px|none)$/.test(border)) {
  failures.push(`styles.css: .reviews-row { border } is "${border}"; rows must stay flat (no border) like Linear's review list`);
}
if (row['border-width'] !== undefined && !/^0(px)?$/.test(row['border-width'])) {
  failures.push(`styles.css: .reviews-row { border-width } is "${row['border-width']}"; rows must stay flat (no border)`);
}
if (row['margin-bottom'] !== undefined && !/^0(px)?$/.test(row['margin-bottom'])) {
  failures.push(`styles.css: .reviews-row { margin-bottom } is "${row['margin-bottom']}"; that is the legacy card spacing and it breaks the 38px rhythm`);
}

/** 3. The group bar is a 22px band. */
expect('.reviews-group-head', 'height', '22px');

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
if (!/role="tab"/.test(tsx) || !/className="reviews-tab on"/.test(tsx)) {
  failures.push('main.tsx: the detail pane lost its Overview/Diff tab row (role="tab" + the selected tab)');
}
if (!/reviews-tab[^"]*"\s+disabled/.test(tsx)) {
  failures.push('main.tsx: the Diff tab must stay disabled while no diff data is stored, not link out or pretend to load');
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
  failures.push('styles.css: .reviews-row lost its 38px height; only the click target inside it may change');
}

/** 8. Grouping axis is Linear's, minus the bucket we cannot derive: Open, Draft, then the
 *     two terminal states we do know, Merged and Closed. "Ready to merge" needs a
 *     mergeability signal a githubLink does not carry. */
if (!/REVIEW_GROUPS=\['Open','Draft','Merged','Closed'\] as const/.test(tsx)) {
  failures.push("main.tsx: review groups must be REVIEW_GROUPS=['Open','Draft','Merged','Closed']; do not silently claim a readiness bucket");
}
if (!/st==='draft'\s*\?\s*'Draft'/.test(tsx) || !/st==='merged'\s*\?\s*'Merged'/.test(tsx) || !/st==='closed'\s*\?\s*'Closed'\s*:\s*'Open'/.test(tsx)) {
  failures.push('main.tsx: reviewGroupOf must derive Draft from state=draft, Merged from state=merged, Closed from state=closed, and Open otherwise');
}
if (!/REVIEW_GROUPS\.map\(/.test(tsx)) {
  failures.push('main.tsx: the ledger must render its groups from REVIEW_GROUPS so the order stays Open, Draft, Merged, Closed');
}

if (failures.length) {
  console.error('reviews invariants failed:\n' + failures.map(f => '  ✗ ' + f).join('\n'));
  process.exit(1);
}
console.log('reviews invariants ok: 482px ledger, flat 38px rows, 22px group bar, two panes, detail pane (52px breadcrumb, 44px tabs, 96px labels)');
