#!/usr/bin/env node
/**
 * HOJA-288 gate: user-facing vocabulary must say "task", not "issue".
 *
 * The rename shipped the GraphQL/MCP `task*` surface and the route redirect, but
 * copy is spread across JSX text and accessible attributes, where an identifier
 * grep cannot see it. This gate reads only the places a person or a screen
 * reader can actually see:
 *
 *   1. literal attribute values: aria-label / title / placeholder / alt
 *   2. JSX text nodes (`>some words<`) with no interpolation or nested tag
 *
 * Identifiers, CSS class names, GraphQL field names and import paths are NOT
 * user-visible, so they are deliberately out of scope. Connector descriptions
 * legitimately name a third-party provider's own "issues" (GitHub, Sentry) and
 * are allowlisted per file.
 *
 * Usage: node frontend/scripts/check-task-vocabulary.mjs
 * Exit 0 = clean, 1 = user-facing "issue" wording remains.
 */
import {readdirSync, readFileSync, statSync} from 'node:fs';
import {join, relative} from 'node:path';
import {fileURLToPath} from 'node:url';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const SRC = join(ROOT, 'src');

// Third-party provider copy: "issues" is that product's own noun here.
const ALLOWED_FILES = new Set(['ConnectorsSettings.tsx']);

const ATTR = /\b(aria-label|title|placeholder|alt)\s*=\s*"([^"]*)"/g;
const TEXT_TAG = />([^<>{}]+)</g;
const WORD = /\bissues?\b/i;

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (name.endsWith('.tsx')) out.push(p);
  }
  return out;
}

const problems = [];
for (const file of walk(SRC)) {
  const rel = relative(SRC, file);
  if (ALLOWED_FILES.has(rel)) continue;
  const text = readFileSync(file, 'utf8');
  text.split('\n').forEach((line, i) => {
    const hits = [];
    for (const m of line.matchAll(ATTR)) if (WORD.test(m[2])) hits.push(`attribute ${m[1]}="${m[2]}"`);
    for (const m of line.matchAll(TEXT_TAG)) {
      const prose = m[1].trim();
      // `>` also ends TypeScript comparisons; real prose never carries these.
      if (!WORD.test(prose) || /[;=()[\]{}?]/.test(prose)) continue;
      hits.push(`text "${prose}"`);
    }
    for (const h of hits) problems.push(`${rel}:${i + 1}: ${h}`);
  });
}

if (problems.length) {
  console.error(`User-facing "issue" wording remains (HOJA-288); say "task":\n`);
  for (const p of problems) console.error(`  - ${p}`);
  console.error(`\n${problems.length} occurrence(s).`);
  process.exit(1);
}
console.log('task vocabulary: clean');
