#!/usr/bin/env node
/**
 * Mutation matrix for the reviews guards.
 * Layout mutations run in compiled browser DOM, without editing application source.
 * Glyph/gate mutations retain the semantic checks and restore source in finally.
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
const gatePath = join(root, 'src/reviews-gate.ts');
const glyphPath = join(root, 'src/reviews-glyphs.ts');

const mutations = [
  {
    /* HOJA-500: the glyph is pinned as PATH DATA. Reverting the one-character transcription
     * slip must be caught — a path-count assertion cannot see it, which is why the old guard
     * was green with the wrong shape. */
    name: 'draft glyph loses a digit (14.6631 -> 14.663)',
    file: glyphPath,
    from: 'C2.75018 14.6631 3.0859',
    to: 'C2.75018 14.663 3.0859',
    runner: ['scripts/check-reviews.mjs', '--no-browser'],
    expect: 'REVIEW_GLYPH_DRAFT path data is not the reference vector set',
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

const originals = new Map([[gatePath, readFileSync(gatePath, 'utf8')], [glyphPath, readFileSync(glyphPath, 'utf8')]]);
let failures = 0;

const rendered = run(['scripts/check-design-tokens.mjs', '--self-test']);
if (rendered.code !== 0) { console.error(rendered.out); process.exit(1); }
console.log(rendered.out.trim());
const baseline = run(['scripts/check-reviews.mjs', '--no-browser']);
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
