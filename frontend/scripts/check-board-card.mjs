#!/usr/bin/env node
/**
 * Board card invariants.
 *
 * styles.css pins the status control with `.kanban-card-props > :first-child`, which is
 * positional: whatever is first child of the props row gets absolutely positioned into a
 * 20px slot at the top-left of the title. Inserting the project chip before the status
 * control therefore clipped the project chip's text and moved the status dot into the row
 * — the bug that shipped as "I still don't see the project".
 *
 * Positional CSS is fine; positional CSS plus reorderable JSX is not. This check keeps the
 * one ordering assumption that CSS relies on.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const tsx = readFileSync(join(root, 'src/main.tsx'), 'utf8');
const css = readFileSync(join(root, 'src/styles.css'), 'utf8');

const failures = [];

/** 1. The props row must keep the status control as its first child. */
const propsStart = tsx.indexOf('className="kanban-card-props"');
if (propsStart === -1) {
  failures.push('main.tsx: no .kanban-card-props row found');
} else {
  // the row ends at the closing of the label that follows it in source order
  const row = tsx.slice(propsStart, tsx.indexOf('</div>\n   {issue.assignee', propsStart));
  const status = row.indexOf('<StatusPicker');
  const projectChip = row.indexOf('kanban-chip-project');
  if (status === -1) failures.push('card props row: StatusPicker is missing');
  if (projectChip === -1) failures.push('card props row: project chip is missing');
  if (status !== -1 && projectChip !== -1 && projectChip < status) {
    failures.push(
      'card props row: the project chip is before the status control. ' +
      'styles.css pins `> :first-child` as the status slot, so the chip would be ' +
      'absolutely positioned and its text clipped. Keep StatusPicker first.'
    );
  }
}

/** 2. A pinned first child only makes sense if the CSS still pins something. */
if (!/\.kanban-card-props>\*:first-child\{/.test(css.replace(/\s+/g, ''))) {
  failures.push('styles.css: the status-control pin (`> :first-child`) is gone; either restore it or drop the ordering rule from this check');
}

/** 3. The move hint must stay out of the card composition. */
if (!/\.kanban-move\{[^}]*left:-9999px/.test(css.replace(/\n/g, ''))) {
  failures.push('styles.css: .kanban-move is no longer pushed out of the card; the "Move Alt + →" hint would show on every card');
}

if (failures.length) {
  console.error('board card invariants failed:\n' + failures.map(f => '  ✗ ' + f).join('\n'));
  process.exit(1);
}
console.log('board card invariants ok: status first, project chip present, move hint hidden');
