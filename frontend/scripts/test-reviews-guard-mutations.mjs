#!/usr/bin/env node
/**
 * Mutation matrix for the reviews guards (HOJA-417).
 *
 * A green check proves nothing until it has been shown to go red for the right reason. The
 * previous guard pinned 38px rows and 22px group bars — it was a passing test of the wrong
 * number. This harness reverts each pinned value in turn, asserts the guard fails with a
 * message that names that value, restores it, and asserts the guard passes again. It also
 * mutates the gate rule itself and requires the table test to catch it.
 *
 * Every write is restored in a finally block, and the file content is compared at the end,
 * so an interrupted run cannot leave a mutated source behind.
 *
 *   node scripts/test-reviews-guard-mutations.mjs
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const cssPath = join(root, 'src/styles.css');
const gatePath = join(root, 'src/reviews-gate.ts');

const mutations = [
  {
    name: 'row height 40px -> 38px',
    file: cssPath,
    from: 'gap:0;height:40px;margin:0;padding:0 4px 0 6px;border:0;background:none;position:relative',
    to: 'gap:0;height:38px;margin:0;padding:0 4px 0 6px;border:0;background:none;position:relative',
    runner: ['scripts/check-reviews.mjs'],
    expect: '.reviews-row { height } is "38px", expected "40px"',
  },
  {
    name: 'group bar 28px -> 22px',
    file: cssPath,
    from: 'gap:6px;height:28px;margin:2px 0 2px 10px',
    to: 'gap:6px;height:22px;margin:2px 0 2px 10px',
    runner: ['scripts/check-reviews.mjs'],
    expect: '.reviews-group-head { height } is "22px", expected "28px"',
  },
  {
    name: 'band rhythm 2px -> 8px',
    file: cssPath,
    from: 'margin:2px 0 2px 10px;padding:0 9px 0 10px',
    to: 'margin:8px 0 0 10px;padding:0 9px 0 10px',
    runner: ['scripts/check-reviews.mjs'],
    expect: '.reviews-group-head { margin } is "8px 0 0 10px", expected "2px 0 2px 10px"',
  },
  {
    name: 'glyph 14px -> 12px',
    file: cssPath,
    from: '.reviews-glyph svg{width:14px;height:14px;display:block}',
    to: '.reviews-glyph svg{width:12px;height:12px;display:block}',
    runner: ['scripts/check-reviews.mjs'],
    expect: '.reviews-glyph svg { width } is "12px"',
  },
  {
    name: 'glyph filled -> stroked',
    file: cssPath,
    from: '.reviews-glyph{width:14px;height:14px;flex:0 0 14px;border:0;border-radius:0;',
    to: '.reviews-glyph{width:14px;height:14px;flex:0 0 14px;border:1.5px solid currentColor;border-radius:0;',
    runner: ['scripts/check-reviews.mjs'],
    expect: '.reviews-glyph { border }',
  },
  {
    name: 'gate treats a null review decision as approved',
    file: gatePath,
    from: "if (reviewDecision === null) return { group: 'Open', reason: 'review-unknown' };",
    to: "if (reviewDecision === null) return { group: 'Ready to merge', reason: 'clean+passing+approved' };",
    runner: ['--experimental-strip-types', 'scripts/test-reviews-gate.mjs'],
    expect: 'review decision null (cannot prove approval)',
  },
  {
    name: 'gate lets drafts into readiness',
    file: gatePath,
    from: "if (state === 'draft') return { group: 'Draft', reason: 'draft' };",
    to: "if (state === 'draft' && false) return { group: 'Draft', reason: 'draft' };",
    runner: ['--experimental-strip-types', 'scripts/test-reviews-gate.mjs'],
    expect: 'draft beats a fully green signal',
  },
];

const run = (args) => {
  try {
    return { code: 0, out: execFileSync(process.execPath, args, { cwd: root, encoding: 'utf8', stdio: 'pipe' }) };
  } catch (e) {
    return { code: e.status ?? 1, out: `${e.stdout || ''}${e.stderr || ''}` };
  }
};

const originals = new Map([[cssPath, readFileSync(cssPath, 'utf8')], [gatePath, readFileSync(gatePath, 'utf8')]]);
let failures = 0;

const baseline = run(['scripts/check-reviews.mjs']);
const baselineGate = run(['--experimental-strip-types', 'scripts/test-reviews-gate.mjs']);
if (baseline.code !== 0 || baselineGate.code !== 0) {
  console.error('refusing to mutate: the guards are not green to begin with');
  process.exit(1);
}
console.log('  ✓ baseline: check-reviews + gate test are green');

try {
  for (const m of mutations) {
    const before = readFileSync(m.file, 'utf8');
    if (!before.includes(m.from)) {
      failures++;
      console.error(`  ✗ ${m.name}: anchor not found, the mutation would be a no-op`);
      continue;
    }
    writeFileSync(m.file, before.replace(m.from, m.to));
    const mutated = run(m.runner);
    writeFileSync(m.file, before);
    const restored = run(m.runner);

    if (mutated.code === 0) {
      failures++;
      console.error(`  ✗ ${m.name}: guard stayed green — it does not pin this value`);
    } else if (!mutated.out.includes(m.expect)) {
      failures++;
      console.error(`  ✗ ${m.name}: failed, but not for the pinned reason\n      want: ${m.expect}\n      got:  ${mutated.out.trim().split('\n')[0]}`);
    } else if (restored.code !== 0) {
      failures++;
      console.error(`  ✗ ${m.name}: guard did not go green again after restore`);
    } else {
      console.log(`  ✓ ${m.name}: red → "${m.expect.slice(0, 64)}…" → green`);
    }
  }
} finally {
  for (const [file, text] of originals) {
    if (readFileSync(file, 'utf8') !== text) writeFileSync(file, text);
  }
}

for (const [file, text] of originals) {
  if (readFileSync(file, 'utf8') !== text) {
    failures++;
    console.error(`  ✗ ${file} was left mutated`);
  }
}

if (failures) {
  console.error(`reviews guard mutations: ${failures} failing mutation(s)`);
  process.exit(1);
}
console.log(`reviews guard mutations ok: ${mutations.length}/${mutations.length} reverts caught, sources restored`);
