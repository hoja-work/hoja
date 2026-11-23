#!/usr/bin/env node
/** Reviews data/behavior contracts plus real Tailwind-rendered layout checks.
 * No assertion depends on a screen or design CSS selector existing. Browser checks
 * compile the current ReviewsShell and primitives, not a frozen legacy DOM.
 */
import {createHash} from 'node:crypto';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';
import {checkRenderedDesign} from './check-design-tokens.mjs';
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const tsx = readFileSync(join(root, 'src/main.tsx'), 'utf8');
const failures = [];
let actions = '';
try {
  actions = readFileSync(join(root, 'src/reviews/ReviewActions.tsx'), 'utf8');
} catch {
  failures.push('src/reviews/ReviewActions.tsx is missing; the four write actions do not exist');
}
if (!/import \{ReviewActions\} from '\.\/reviews\/ReviewActions';/.test(tsx)) {
  failures.push('main.tsx: ReviewActions is not imported; the four write actions are not mounted');
}
if (!/<ReviewActions linkId=\{sel\.id\} gql=\{gql\}/.test(tsx)) {
  failures.push('main.tsx: ReviewActions must bind the selected link id and the app gql helper');
}
for (const m of ['reviewApprove', 'reviewRequestChanges', 'reviewComment', 'reviewMerge']) {
  if (!actions.includes(m)) failures.push(`ReviewActions.tsx: mutation ${m} is missing`);
}
if (!/Confirm squash merge/.test(actions) || !/gate\.sha/.test(actions)) {
  failures.push('ReviewActions.tsx: the merge confirm must print the exact sha it is merging');
}
if (/Merging from Hoja is not connected yet|Commenting on pull requests is not connected yet/.test(tsx)) {
  failures.push('main.tsx: a dead "not connected" control is back; the action is wired, so the placeholder must not render');
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
  /* HOJA-500: a path count cannot see a transcribed typo. Ours matched 4/5 path counts and
   * still carried `14.663` where the reference vector has `14.6631`. Compare the bytes.
   * Checksums are of the live reference's svg path[d] set, state for state. */
  const pathsOf = (name) => {
    const from = glyphFile.indexOf(name);
    const to = glyphFile.indexOf('];', from);
    return from === -1 || to === -1 ? [] : [...glyphFile.slice(from, to).matchAll(/'(M[^']+)'/g)].map((m) => m[1]);
  };
  const hashOf = (paths) => createHash('sha256').update(paths.join('\n')).digest('hex');
  for (const [name, want] of [
    ['REVIEW_GLYPH_PULL', '0bd8836dbd1bcc8d620b0b5fe71acd69cc9ca4ff89ac26bb5ddb1831015f645d'],
    ['REVIEW_GLYPH_DRAFT', 'f3a3f095aa0a584c3edfb13a74579b32c1761f4c12d315e59fe41e6e71eebc1b'],
  ]) {
    const got = hashOf(pathsOf(name));
    if (got !== want) {
      failures.push(`reviews-glyphs.ts: ${name} path data is not the reference vector set (sha256 ${got.slice(0, 12)}…, expected ${want.slice(0, 12)}…) — the glyph shape must be the measured one, byte for byte`);
    }
  }
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
      failures.push(`main.tsx: ReviewsLinks does not select ${field}; the pane renders it, so it would regress to a labelled unknown while the server stores a real value`);
    }
  }
  if (!/pullRequests\s*\(/.test(selection) || !/issue\s*\{\s*identifier\s*\}/.test(selection)) {
    failures.push('main.tsx: ReviewsLinks must read pullRequests and select issue { identifier } — the pane labels each row with the task it belongs to');
  }
  // The queue must not be derived from the newest N tasks again. That derivation emptied the
  // pane once more than N tasks had been touched, while every linked pull request still existed.
  if (/issues\s*\(\s*first\s*:/.test(selection)) {
    failures.push('main.tsx: ReviewsLinks is reading issues(first:…) again; the queue must come from pullRequests so task recency cannot hide linked pull requests');
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
  need(/\{bodyPreview\?<div className="[^"]+">/, 'the description must render the stored body when present ({bodyPreview? <div className="reviews-desc">})');
  need(/reviewBodyPreview\(sel\?\.body\)/, 'the description preview must be derived from the stored body via reviewBodyPreview');
  need(/No pull request description is stored/, 'the description keeps its explicit empty state');
  need(/reviewStateLabel\(sel\.state\)/, 'Status must stay the primary value derived from the link state');
  need(/merge state: /, 'the GitHub merge state may only appear as a secondary tag next to the link state');
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
    failures.push('main.tsx: ReviewsLinks does not select files; the Diff tab would have no data to render');
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
  const tabStart = tsx.lastIndexOf('<', diffTabAt);
  let tagEnd = diffTabAt;
  while (tagEnd < tsx.length && !(tsx[tagEnd] === '>' && tsx[tagEnd - 1] !== '=')) tagEnd++;
  const tabTag = tsx.slice(tabStart, tagEnd + 1);
  // A disabled: Tailwind variant inside className is not a disabled attribute.
  const tabAttributes = tabTag.replace(/className="[^"]*"/g, '');
  if (/\sdisabled(?:\s|=|\/?>)/.test(tabAttributes)) {
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
diffNeed(/data-kind=\{kind\}/, 'each patch line must carry reviews-diff-<kind> so + / - lines are colour-coded');
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

if (failures.length) {
  console.error('reviews invariants failed:\n' + failures.map(f => '  ✗ ' + f).join('\n'));
  process.exit(1);
}
console.log('reviews data contracts ok: gate, filled reference glyphs, stored detail fields, write actions, accessible live Diff tabs and honest empty states');
if (!process.argv.includes('--no-browser')) await checkRenderedDesign();
else console.log('SKIPPED rendered layout/interaction checks (--no-browser); source checks alone are not a full pass');
