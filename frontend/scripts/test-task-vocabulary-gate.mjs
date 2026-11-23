#!/usr/bin/env node
/**
 * Mutation test for check-task-vocabulary.mjs (HOJA-288).
 *
 * A green vocabulary gate is only evidence if it goes red on the defect it claims
 * to catch. Four earlier sweeps reported "0 residual" while visible copy survived,
 * so this harness injects one defect per *static* render modality into a copy of
 * src/ and requires the gate to fail and name the file. It also injects the
 * near-misses that must stay green, so the gate cannot pass by flagging everything.
 *
 * The runtime-derived class (a label computed from a routing constant and then
 * rendered) is deliberately NOT here: a static predicate cannot prove what a
 * variable is used for. scripts/check-task-vocabulary-dom.cjs covers it against
 * the rendered DOM.
 *
 * Usage: node scripts/test-task-vocabulary-gate.mjs
 */
import {cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';

const HERE = dirname(fileURLToPath(import.meta.url));
const GATE = join(HERE, 'check-task-vocabulary.mjs');
const SRC = join(HERE, '..', 'src');

const defects = [
  ['jsx text node', 'main.tsx', `const __probe = <p>Team issues</p>;\n`],
  ['visible attribute', 'main.tsx', `const __probe = <button aria-label="Create new issue" />;\n`],
  ['string literal in expression', 'main.tsx', `const __probe = () => confirm('Delete this issue?');\n`],
  ['template literal with interpolation', 'main.tsx', `const __n = 3; const __probe = \`\${__n} issues failed\`;\n`],
  // Both of these hid real copy from the first gate revision: a property value
  // ("displayText: 'Summarize my open issues'") and a [key, copy] tuple array.
  ['property value copy', 'main.tsx', `const __probe = [{displayText: 'Summarize my open issues'}];\n`],
  ['tuple array copy', 'main.tsx', `const __probe = [['issueCreated', 'Issue created for me']];\n`],
];
const nearMisses = [
  ['routing key comparison', 'main.tsx', `const __v = 'Team issues'; if (__v === 'Team issues') void 0;\n`],
  ['routing key in a display-mapped list', 'main.tsx', `const __probe = ['Overview', 'All issues'];\n`],
  ['routing key returned by the resolver', 'routes.ts', `export const __probe = () => 'Team issues';\n`],
  ['GraphQL document', 'main.tsx', `const __probe = 'query X{issue(id:$id){id}}';\n`],
  ['entity discriminator', 'main.tsx', `const __probe = 'issue';\n`],
  ['import path and CSS selector', 'main.tsx', `const __probe = '.my-issue-row'; import './graph/tasks';\n`],
];

const runGate = dir => spawnSync(process.execPath, [GATE, dir], {encoding: 'utf8'});
let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };
const withCopy = fn => {
  const dir = mkdtempSync(join(tmpdir(), 'vocab-'));
  cpSync(SRC, dir, {recursive: true});
  try { return fn(dir); } finally { rmSync(dir, {recursive: true, force: true}); }
};

for (const [name, relFile, payload] of defects) {
  withCopy(dir => {
    const file = join(dir, relFile);
    writeFileSync(file, payload + readFileSync(file, 'utf8'));
    const r = runGate(dir);
    ok(r.status === 1 && r.stderr.includes(relFile), `${name}: gate fails and names ${relFile}`, `exit ${r.status}`);
  });
}
for (const [name, relFile, payload] of nearMisses) {
  withCopy(dir => {
    const file = join(dir, relFile);
    writeFileSync(file, payload + readFileSync(file, 'utf8'));
    const r = runGate(dir);
    ok(r.status === 0, `${name}: gate stays green`, r.stderr.split('\n').filter(Boolean).slice(0, 2).join(' | '));
  });
}
const clean = runGate(SRC);
ok(clean.status === 0, 'current src/ is clean', clean.stderr.split('\n')[0] || clean.stdout.trim());

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
