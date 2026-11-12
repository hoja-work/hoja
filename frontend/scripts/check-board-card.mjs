#!/usr/bin/env node
/**
 * Board card invariants.
 *
 * This check used to pin the "verified card composition": a `.kanban-card-wrap` wrapper,
 * a props row whose first child was absolutely positioned by `> :first-child`, and a
 * `.kanban-move` button pushed out of the card. That composition is gone — the card is one
 * flat surface built from Tailwind utilities over a vertical list of property rows — but
 * the reasons the check existed still hold, so it now pins the invariants of the current
 * card instead:
 *
 *   - the card is a single surface, not a card inside a wrapper;
 *   - the props row keeps its 28px row rhythm from styles.css;
 *   - the status control is the first row, because that is the order the card reads in;
 *   - no move button is rendered; the Alt + → keyboard path lives on the card-open button;
 *   - sub-task nesting (HOJA-33) is carried by the card itself, so `data-depth` and its
 *     rail rule have to stay.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const tsx = readFileSync(join(root, 'src/main.tsx'), 'utf8');
const css = readFileSync(join(root, 'src/styles.css'), 'utf8');
const flatCss = css.replace(/\s+/g, '');

const failures = [];

/** 1. The card is one flat surface. */
const cardRoot = tsx.match(/<div className="kanban-card([^"]*)"/);
if (!cardRoot) {
  failures.push('main.tsx: no `.kanban-card` root element found');
} else {
  const classes = cardRoot[1];
  for (const utility of ['rounded-2xl', 'border-line', 'bg-panel']) {
    if (!classes.includes(utility)) failures.push(`card root: the flat surface lost its \`${utility}\` utility`);
  }
}
if (tsx.includes('kanban-card-wrap')) {
  failures.push('main.tsx: the `.kanban-card-wrap` wrapper came back; the card is a single surface now');
}

/** 2. The props row keeps the row rhythm declared in styles.css. */
if (!tsx.includes('kanban-card-props')) {
  failures.push('main.tsx: no `.kanban-card-props` row found');
}
if (!flatCss.includes('.kanban-card-props>label,.kanban-card-props>div{display:flex;align-items:center;gap:8px;height:28px')) {
  failures.push('styles.css: the props row lost its 28px row rhythm (`>label,>div` height)');
}

/** 3. The status control is the first row of the props row. */
const propsStart = tsx.indexOf('className="kanban-card-props');
if (propsStart !== -1) {
  const row = tsx.slice(propsStart, tsx.indexOf('<label', propsStart));
  if (!row.includes('<StatusPicker')) failures.push('card props row: the status control is no longer the first row');
}

/** 4. No move button; the keyboard path stays on the card-open button. */
if (tsx.includes('kanban-move')) failures.push('main.tsx: the `.kanban-move` button is back; the card must not show a move affordance');
if (!tsx.includes("e.altKey&&e.key==='ArrowRight'")) failures.push('main.tsx: the Alt + → keyboard path left the card-open button');

/** 5. Sub-task nesting is carried by the card itself. */
if (!/data-depth=\{depth\}/.test(tsx)) failures.push('main.tsx: the card no longer marks its sub-task depth (`data-depth`)');
if (!flatCss.includes('.kanban-card[data-depth]:not([data-depth="0"])::before')) {
  failures.push('styles.css: the sub-task rail rule is gone, so nested cards lose their indent marker');
}

if (failures.length) {
  console.error('board card invariants failed:\n' + failures.map((f) => '  ✗ ' + f).join('\n'));
  process.exit(1);
}
console.log('board card invariants ok: one flat surface, 28px rows, status first, no move hint, nesting kept');
