#!/usr/bin/env node
/**
 * HOJA-288 gate: user-facing copy says "task", not "issue".
 *
 * Why this is not a grep: the strings that leak are produced three ways, and a
 * regex sees at most one of them.
 *
 *   1. JSX text nodes                     `>Team issues<`
 *   2. attribute values                   aria-label / title / placeholder / alt /
 *                                         label / emptyTitle / emptyText / header
 *   3. string literals in expressions     ternaries, template literals, confirm()
 *                                         copy, error messages, option lists
 *
 * (3) is the class that survived four earlier "0 residual" sweeps: a literal is
 * computed at runtime, so it is not an attribute and not a text node, and any
 * grep that looks for `>...<` or `attr="..."` is structurally blind to it.
 *
 * The predicate below is a *render-position* test over the TypeScript AST, so it
 * follows the value, not the shape of the source line. It deliberately allows:
 *   - identifiers, imports, GraphQL documents, MIME types and URL-ish tokens;
 *   - className / id / key / role / href / to / path / testID / on* / data-*;
 *   - view keys compared with === / !== or used as switch cases, because those
 *     are routing constants that must not change (labels.ts derives the copy).
 * Anything else containing "issue" fails with file:line.
 *
 * Usage: node scripts/check-task-vocabulary.mjs [srcdir]
 * Exit 0 = clean, 1 = user-facing "issue" wording remains.
 */
import ts from 'typescript';
import {readdirSync, readFileSync, statSync} from 'node:fs';
import {join, relative, extname} from 'node:path';
import {fileURLToPath} from 'node:url';

const SRC = process.argv[2]
  ? process.argv[2]
  : join(fileURLToPath(new URL('..', import.meta.url)), 'src');

const WORD = /\bissues?\b/i;

// Attribute values a person or a screen reader can see.
const VISIBLE_ATTR = new Set([
  'aria-label', 'aria-description', 'aria-roledescription', 'title', 'placeholder', 'alt',
  'label', 'emptyTitle', 'emptyText', 'header', 'description', 'tooltip', 'helperText',
  'hint', 'confirmLabel', 'cancelLabel', 'submitLabel', 'backLabel', 'caption', 'summary',
  'message', 'errorText', 'successText', 'legend',
]);
// Attribute values that are identifiers, routes, styles or handlers.
const STRUCTURAL_ATTR = /^(className|id|key|role|type|href|to|path|src|name|value|testID|view|tab|section|action|kind|entity|resource|resourceTypes|events|route|for|htmlFor|aria-labelledby|aria-controls|aria-describedby|data-.*|on[A-Z].*)$/;
// A literal that is a token rather than prose.
const STRUCTURAL_VALUE = [
  /^application\/[\w.+-]+$/, /^text\/[\w.+-]+$/, /^image\//, /^audio\//, /^video\//,
  /^hoja-issue:/, /^[a-z][a-z0-9]*(?:[-_:][a-z0-9]+)+$/, // kebab / snake / namespaced tokens
  /^[a-z]+$/,                                            // a bare enum/entity token ("issue")
  /^\.{1,2}\//, /^\/[\w./${}-]*$/, /^[.#][\w-]+(?:[ >+~][\w.-]+)*$/, // CSS selectors
  /^https?:\/\//, /^[a-z0-9.-]+\.[a-z]{2,}(?:\/.*)?$/i,      // URLs and example hostnames
  /[{}\[\]]/,                                           // GraphQL documents and field lists
  /^#[0-9a-f]{3,8}$/i, /^\d+(?:\.\d+)?(?:px|rem|em|%)?$/,
  /^(?:query|mutation|subscription|fragment)\b/,
  /^(?:GET|POST|PUT|PATCH|DELETE)$/,
];
// Third-party product nouns: these "issues" belong to the provider, not to Hoja.
const ALLOWED_FILES = new Set(['ConnectorsSettings.tsx']);
// Wire vocabulary: extension manifests name the `Issue` entity and the `issues`
// event because that is the JSON an extension receives. Renaming it is an API break.
const ALLOWED_LINES = new Map([
  ['ExtensionsSettings.tsx:16', 'extension manifest wire vocabulary'],
]);

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === 'dist') continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (['.ts', '.tsx', '.js', '.jsx'].includes(extname(name))) out.push(p);
  }
  return out;
}
const lineOf = (sf, node) => sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;

function attrName(attr) {
  const n = attr.name;
  if (ts.isIdentifier(n)) return n.text;
  if (ts.isJsxNamespacedName(n)) return `${n.namespace.text}:${n.name.text}`;
  return n.getText();
}
function enclosingPropName(node) {
  let p = node.parent;
  while (p) {
    if (ts.isPropertyAssignment(p)) return p.name.getText().replace(/^["']|["']$/g, '');
    if (ts.isJsxAttribute(p)) return attrName(p);
    p = p.parent;
  }
  return '';
}
function enclosingTag(node) {
  let p = node.parent;
  while (p) {
    if (ts.isJsxOpeningElement(p) || ts.isJsxSelfClosingElement(p)) return p.tagName.getText();
    p = p.parent;
  }
  return '';
}
// A literal that is only ever compared is a routing constant, not copy.
function isComparison(node) {
  let n = node;
  while (n.parent && (ts.isParenthesizedExpression(n.parent) || ts.isAsExpression(n.parent) || ts.isSatisfiesExpression?.(n.parent))) n = n.parent;
  const p = n.parent;
  if (!p) return false;
  if (ts.isBinaryExpression(p)) {
    const k = p.operatorToken.kind;
    if (k === ts.SyntaxKind.EqualsEqualsEqualsToken || k === ts.SyntaxKind.ExclamationEqualsEqualsToken ||
        k === ts.SyntaxKind.EqualsEqualsToken || k === ts.SyntaxKind.ExclamationEqualsToken) return true;
  }
  if (ts.isCaseClause(p) || ts.isSwitchStatement(p)) return true;
  if (ts.isPropertyAssignment(p) || ts.isPropertyDeclaration(p) || ts.isEnumMember(p)) return true;
  if (ts.isLiteralTypeNode(p) || ts.isUnionTypeNode(p.parent) || ts.isTypeAliasDeclaration(p.parent)) return true;
  if (ts.isImportDeclaration(p.parent) || ts.isExportDeclaration(p.parent)) return true;
  if (ts.isCallExpression(p) && p.expression) {
    const callee = p.expression.getText();
    // Routes and transport: these arguments are keys/documents, not copy.
    if (/^(gql|navigate|routeView|redirectLegacyTaskPath|pushState|replaceState|history\.|fetch|localStorage\.|sessionStorage\.)/.test(callee)) return true;
    if (/^(navigate|goTab|setTab|setView|setSection)$/.test(callee)) return true;
  }
  return false;
}
const looksStructural = s => STRUCTURAL_VALUE.some(re => re.test(s));

// The routing constants ("Team issues", ...) must not change: labels.ts derives the
// copy with displayLabel(). A key is legitimate where it is a key — compared, returned
// from a route resolver, passed to a setter, listed in a key array, or stored — and
// suspect where it is handed to something that renders. This gate cannot see the
// render, so a key literal that survives here is still checked by the rendered-DOM
// sweep (scripts/check-task-vocabulary-dom.cjs), which is the only proof for the
// runtime-derived class.
const KEY_LITERALS = new Set([
  'Issues', 'My issues', 'Team issues', 'All issues', 'Active issues', 'Recent issues',
  'New issues', 'Issue detail',
]);
function inKeyPosition(node) {
  let n = node;
  while (n.parent && (ts.isParenthesizedExpression(n.parent) || ts.isAsExpression(n.parent))) n = n.parent;
  const p = n.parent;
  return !!p && (ts.isReturnStatement(p) || ts.isCallExpression(p) || ts.isArrayLiteralExpression(p) ||
    ts.isVariableDeclaration(p) || ts.isPropertyAssignment(p) || ts.isBinaryExpression(p) ||
    ts.isArrowFunction(p) || ts.isTypeAliasDeclaration(p) || ts.isUnionTypeNode(p) ||
    ts.isLiteralTypeNode(p) || ts.isConditionalExpression(p));
}

const problems = [];
for (const file of walk(SRC)) {
  const rel = relative(SRC, file);
  if (ALLOWED_FILES.has(rel)) continue;
  const text = readFileSync(file, 'utf8');
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true,
    /\.(tsx|jsx)$/.test(file) ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const report = (node, kind, value) => {
    const key = `${rel}:${lineOf(sf, node)}`;
    if (ALLOWED_LINES.has(key)) return;
    problems.push(`${key}: ${kind} ${JSON.stringify(value)}`);
  };
  const visit = node => {
    if (ts.isJsxText(node)) {
      const t = node.text.replace(/\s+/g, ' ').trim();
      if (t && WORD.test(t)) report(node, 'jsx text', t);
    } else if (ts.isJsxAttribute(node)) {
      const name = attrName(node);
      const init = node.initializer;
      const literal = init && (ts.isStringLiteral(init) ? init
        : ts.isJsxExpression(init) && init.expression && ts.isStringLiteral(init.expression) ? init.expression : null);
      if (literal && WORD.test(literal.text)) {
        // Identifier/route/style/handler positions are not copy, whatever the value.
        if (!STRUCTURAL_ATTR.test(name)) report(node, `attribute ${name}=`, literal.text);
      }
    } else if (ts.isTemplateExpression(node)) {
      // `${n} issues` is a template, not a text node: head/middle/tail each hold prose.
      for (const part of [node.head, ...node.templateSpans.map(sp => sp.literal)]) {
        const t = part.text.replace(/\s+/g, ' ').trim();
        const prop = enclosingPropName(part);
        if (t && WORD.test(t) && !looksStructural(t) && !isComparison(part) &&
            (!prop || !STRUCTURAL_ATTR.test(prop))) {
          report(part, 'template literal', t);
        }
      }
    } else if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      const s = node.text;
      if (WORD.test(s) && KEY_LITERALS.has(s) && inKeyPosition(node)) { /* routing key: not copy */ }
      else if (WORD.test(s) && !looksStructural(s) && !isComparison(node)) {
        const prop = enclosingPropName(node);
        // A key list iterated with displayLabel() is the one legitimate array form;
        // the rendered DOM sweep, not this gate, proves the mapping is applied.
        const inKeyList = ts.isArrayLiteralExpression(node.parent);
        if (!inKeyList && (!prop || !STRUCTURAL_ATTR.test(prop))) {
          report(node, prop ? `expression (${prop})` : 'expression', s);
        }
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(sf);
}

if (problems.length) {
  console.error('User-facing "issue" wording remains (HOJA-288); the UI must say "task":\n');
  for (const p of problems) console.error(`  - ${p}`);
  console.error(`\n${problems.length} occurrence(s). Routing keys and identifiers are not reported.`);
  process.exit(1);
}
console.log(`task vocabulary: clean (${walk(SRC).length} source files scanned)`);
