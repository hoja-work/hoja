#!/usr/bin/env node
/** State-shape source lint (HOJA-746).
 *
 * `w-full` plus a horizontal margin makes a box margin-wider than its parent. Inside any
 * `overflow-x-hidden` ancestor (the sidebar nav, cards) the far edge is cut, so a hover
 * pill renders rounded on one side and square on the other, and the clipper gains a
 * hidden horizontal scroll that focus can shift. Inset the box with margins and let it
 * stretch (block or flex-column parent), or keep `w-full` and inset with padding.
 *
 * The rendered counterpart is scripts/test-state-shape.cjs, which checks hover and
 * focus-visible paint against every clipping ancestor in the running app.
 *
 * Usage: node scripts/check-state-shape-source.mjs [--self-test] [srcDir]
 */
import {readFileSync, readdirSync} from 'node:fs';
import {dirname, join, relative} from 'node:path';
import {fileURLToPath} from 'node:url';

const frontend = join(dirname(fileURLToPath(import.meta.url)), '..');
const HMARGIN = /^-?(mx|ml|mr|ms|me)-(?!auto$|0$|px$)/;

export function findWidthOverflow(source) {
  const hits = [];
  for (const m of source.matchAll(/["'`]([^"'`\n]*\bw-full\b[^"'`\n]*)["'`]/g)) {
    // Only unprefixed utilities apply together in the same state; variants
    // (shell-phone:, rail-collapsed:, hover:) are separate contexts.
    const bare = m[1].split(/\s+/).filter(c => c && !c.includes(':'));
    if (!bare.includes('w-full')) continue;
    const margins = bare.filter(c => HMARGIN.test(c));
    if (margins.length) hits.push({line: source.slice(0, m.index).split('\n').length, classes: ['w-full', ...margins].join(' ')});
  }
  return hits;
}

const walk = d => readdirSync(d, {withFileTypes: true}).flatMap(e =>
  e.isDirectory() ? walk(join(d, e.name)) : /\.(tsx|ts|jsx|js)$/.test(e.name) ? [join(d, e.name)] : []);

function selfTest() {
  const bad = `<button className="flex items-center justify-between w-full h-7 mr-1 ml-1 rounded-md">`;
  const ok = [
    `<button className="flex items-center justify-between h-7 mr-1 ml-1 rounded-md">`,
    `<button className="w-full mx-auto">`,
    `<button className="w-full mx-0 px-1">`,
    `<div className="w-full shell-phone:mx-2">`,
  ];
  if (findWidthOverflow(bad).length !== 1) throw new Error('self-test: missed w-full + ml/mr');
  for (const s of ok) if (findWidthOverflow(s).length) throw new Error(`self-test: false positive on ${s}`);
  if (findWidthOverflow(`cn("w-full", "-mx-2")`).length) throw new Error('self-test: classes in separate strings are not one element');
  console.log('ok check-state-shape-source self-test');
}

const args = process.argv.slice(2);
if (args.includes('--self-test')) selfTest();
else {
  const src = args[0] || join(frontend, 'src');
  const failures = [];
  for (const f of walk(src)) for (const h of findWidthOverflow(readFileSync(f, 'utf8')))
    failures.push(`${relative(process.cwd(), f)}:${h.line}  ${h.classes}  (box is wider than its parent by its margins; drop w-full or move the inset into padding)`);
  if (failures.length) { console.error(failures.join('\n')); console.error(`FAIL check-state-shape-source: ${failures.length}`); process.exit(1); }
  console.log('ok check-state-shape-source');
}
