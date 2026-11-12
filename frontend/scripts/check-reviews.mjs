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

if (failures.length) {
  console.error('reviews invariants failed:\n' + failures.map(f => '  ✗ ' + f).join('\n'));
  process.exit(1);
}
console.log('reviews invariants ok: 482px ledger, flat 38px rows, 22px group bar, two panes, detail pane (52px breadcrumb, 44px tabs, 96px labels), ReviewsLinks selects all 17 pull-request detail fields and the pane renders them');
