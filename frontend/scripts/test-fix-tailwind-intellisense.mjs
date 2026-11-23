import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
// The fixture must live in the actual Tailwind workspace so IntelliSense loads its design system.
const dir = await mkdtemp(join(root, 'src/.tailwind-intellisense-test-'));
const file = join(dir, 'fixture.tsx');
const input = 'export const view = <div className="[@media_(max-width:_700px)]:mb-[27px] mt-[16px]" />;\n';
function run(...args) {
  const result = spawnSync(process.execPath, ['scripts/fix-tailwind-intellisense.mjs', ...args, relative(root, file)], { cwd: root, encoding: 'utf8', timeout: 60000 });
  assert.equal(result.status, 0, result.stderr || result.error?.message);
  return result.stdout;
}
try {
  await writeFile(file, input);
  assert.match(run(), /Found 2 replacement\(s\) in 1 file\(s\)/);
  assert.equal(await readFile(file, 'utf8'), input, 'dry run must not write');
  assert.match(run('--write'), /Applied 2 replacement\(s\) in 1 file\(s\)/);
  assert.equal(await readFile(file, 'utf8'), 'export const view = <div className="[@media_(max-width:_700px)]:mb-6.75 mt-4" />;\n');
  assert.match(run(), /Found 0 replacement\(s\)/, 'second run must be idempotent');

  const conflicting = 'export const view = <div className="grid grid grid bg-red-300 bg-blue-300 hover:grid hover:grid" />;\n';
  await writeFile(file, conflicting);
  assert.match(run('--duplicates'), /Found 3 replacement\(s\) in 1 file\(s\)/);
  assert.equal(await readFile(file, 'utf8'), conflicting, 'duplicate dry run must not write');
  assert.match(run('--duplicates', '--write'), /Applied 3 replacement\(s\) in 1 file\(s\)/);
  assert.equal(await readFile(file, 'utf8'), 'export const view = <div className="grid bg-red-300 bg-blue-300 hover:grid" />;\n');
  assert.match(run('--duplicates'), /Found 0 replacement\(s\)/, 'duplicate fixing must be idempotent');
  console.log('Tailwind IntelliSense fixer integration test passed');
} finally {
  await rm(dir, { recursive: true, force: true });
}
