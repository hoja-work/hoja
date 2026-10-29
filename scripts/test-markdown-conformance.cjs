// CommonMark conformance for the app's real Markdown component.
//
// Oracle: the official CommonMark 0.31.2 spec corpus (652 examples), vendored at
// scripts/fixtures/commonmark-spec-0.31.2.json and fetched from spec.commonmark.org.
// The component is transpiled from frontend/src/markdown.tsx and rendered with
// renderToStaticMarkup, so this measures the code the app ships, not a copy of it.
//
// What is compared: element structure and text. Two documented deltas are applied to the
// expected tree so the comparison is meaningful rather than an attribute diff:
//   1. headings are demoted by two levels (h1 -> h3 ... clamped at h6),
//   2. attributes we add for safety (rel/target on links, class names) are not compared.
// Raw HTML: react-markdown deliberately drops it, so examples whose source contains raw HTML
// constructs are reported separately instead of counted as passes.
//
// Run from the repo root: node scripts/test-markdown-conformance.cjs [--verbose] [--limit N]
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {pathToFileURL} = require('node:url');

const repo = path.resolve(__dirname, '..');
const frontend = path.join(repo, 'frontend');
const fixture = path.join(repo, 'scripts/fixtures/commonmark-spec-0.31.2.json');
const SPEC_SHA256 = 'd431b29d97b6f73e69d547109cf5081578fac931e72afe95639ebe766c1b2a20';
const verbose = process.argv.includes('--verbose');
const limitArg = process.argv.indexOf('--limit');
const limit = limitArg > 0 ? Number(process.argv[limitArg + 1]) : Infinity;

if (!fs.existsSync(fixture)) {
  console.error(`missing corpus: ${path.relative(repo, fixture)}`);
  console.error('refresh it with: curl -o scripts/fixtures/commonmark-spec-0.31.2.json https://spec.commonmark.org/0.31.2/spec.json');
  process.exit(2);
}
const corpus = JSON.parse(fs.readFileSync(fixture, 'utf8'));
const sha = crypto.createHash('sha256').update(fs.readFileSync(fixture)).digest('hex');
if (sha !== SPEC_SHA256) {
  console.error(`corpus checksum changed: ${sha} (expected ${SPEC_SHA256}); re-review the corpus before trusting results`);
  process.exit(2);
}

/* ---------- the component under test ---------- */

const ts = require(path.join(frontend, 'node_modules/typescript'));
const outDir = path.join(frontend, '.md-conformance');
fs.mkdirSync(outDir, {recursive: true});
const source = fs.readFileSync(path.join(frontend, 'src/markdown.tsx'), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX,
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
    esModuleInterop: true,
    isolatedModules: true,
  },
  fileName: 'markdown.tsx',
}).outputText;
const compiledPath = path.join(outDir, 'markdown.mjs');
fs.writeFileSync(compiledPath, compiled);

const React = require(path.join(frontend, 'node_modules/react'));
const {renderToStaticMarkup} = require(path.join(frontend, 'node_modules/react-dom/server'));

/* ---------- HTML -> comparable tree ---------- */

const VOID = new Set(['br', 'img', 'hr', 'input', 'meta', 'link', 'source', 'wbr']);
const BLOCK = new Set(['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'pre', 'blockquote',
  'table', 'thead', 'tbody', 'tr', 'td', 'th', 'div', 'section', 'article', 'hr', 'figure', 'figcaption']);
const ENTITIES = {amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: '\u00a0'};

function decodeEntities(text) {
  return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, body) => {
    if (body[0] === '#') {
      const code = body[1] === 'x' || body[1] === 'X' ? parseInt(body.slice(2), 16) : parseInt(body.slice(1), 10);
      return Number.isFinite(code) ? String.fromCodePoint(code) : whole;
    }
    return Object.prototype.hasOwnProperty.call(ENTITIES, body) ? ENTITIES[body] : whole;
  });
}

function parseAttrs(raw) {
  const attrs = {};
  if (!raw) return attrs;
  const re = /([\w:-]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
  let m;
  while ((m = re.exec(raw))) attrs[m[1].toLowerCase()] = decodeEntities(m[2] ?? m[3] ?? m[4] ?? '');
  return attrs;
}

const TAG = /<!--[\s\S]*?-->|<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[\w:-]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+))?)*)\s*\/?>/g;

function toTree(html) {
  const root = {tag: '#root', attrs: {}, children: []};
  const stack = [root];
  let last = 0;
  let m;
  const push = (node) => stack[stack.length - 1].children.push(node);
  const pushText = (raw, inPre) => {
    if (!raw) return;
    const decoded = decodeEntities(raw);
    if (!inPre && !decoded.trim()) return; // pretty-printing indentation, not content
    push({text: inPre ? decoded : decoded.replace(/\s+/g, ' '), inPre});
  };
  TAG.lastIndex = 0;
  while ((m = TAG.exec(html))) {
    const inPre = stack.some((n) => n.tag === 'pre');
    if (m.index > last) pushText(html.slice(last, m.index), inPre);
    last = TAG.lastIndex;
    if (m[0].startsWith('<!--')) continue;
    if (m[1]) {
      const tag = m[1].toLowerCase();
      for (let i = stack.length - 1; i > 0; i--) {
        if (stack[i].tag === tag) { stack.length = i; break; }
      }
      continue;
    }
    const tag = m[2].toLowerCase();
    const node = {tag, attrs: parseAttrs(m[3]), children: []};
    push(node);
    if (!VOID.has(tag) && !m[0].endsWith('/>')) stack.push(node);
  }
  const tailInPre = stack.some((n) => n.tag === 'pre');
  if (last < html.length) pushText(html.slice(last), tailInPre);
  return root;
}

function demote(tag) {
  const level = Number(tag.slice(1));
  return `h${Math.min(6, (Number.isFinite(level) ? level : 6) + 2)}`;
}

function signature(node, {expected = false} = {}) {
  if (typeof node.text === 'string') return `#${node.text}`;
  const children = node.children.map((c) => signature(c, {expected})).filter((c) => c !== null);
  if (node.tag === '#root') return children;
  let tag = node.tag;
  if (expected && /^h[1-6]$/.test(tag)) tag = demote(tag);
  if (tag === 'a') {
    const href = node.attrs.href ?? '';
    return ['a', href, ...children];
  }
  if (tag === 'img') {
    return ['img', node.attrs.src ?? '', node.attrs.alt ?? '', ...children];
  }
  return [tag, ...children];
}

function treeOf(html, opts) {
  const sig = signature(toTree(html), opts);
  // Our component wraps the body in <div class="md md--compact" data-md="compact">.
  if (Array.isArray(sig) && sig.length === 1 && Array.isArray(sig[0]) && sig[0][0] === 'div') return sig[0].slice(1);
  return sig;
}

const isTag = (n) => Array.isArray(n);
const isA = (n) => isTag(n) && n[0] === 'a';
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// Drops the href from every anchor so a URL stripped for safety does not look like a structure bug.
function withoutHrefs(nodes) {
  return nodes.map((n) => {
    if (!isTag(n)) return n;
    if (isA(n)) return ['a', ...withoutHrefs(n.slice(2))];
    return [n[0], ...withoutHrefs(n.slice(n[0] === 'img' ? 3 : 1))];
  });
}
const hrefCount = (nodes) => nodes.reduce((sum, n) => sum + (isA(n) ? 1 : 0) + (isTag(n) ? hrefCount(n.slice(isA(n) ? 2 : n[0] === 'img' ? 3 : 1)) : 0), 0);
const emptyHrefs = (nodes) => nodes.reduce((sum, n) => sum + (isA(n) && n[1] === '' ? 1 : 0) + (isTag(n) ? emptyHrefs(n.slice(isA(n) ? 2 : n[0] === 'img' ? 3 : 1)) : 0), 0);

function flatText(nodes) {
  return nodes.map((n) => (isTag(n) ? flatText(n.slice(isA(n) ? 2 : n[0] === 'img' ? 3 : 1)) : n.slice(1))).join('');
}
function shape(nodes) {
  const merged = [];
  for (const n of withoutAnchors(nodes)) {
    if (!isTag(n)) { if (merged.length && merged[merged.length - 1] === 'T') continue; merged.push('T'); continue; }
    merged.push([n[0], ...shape(n.slice(1))]);
  }
  return merged;
}
// remark-gfm adds autolink literals for bare URLs and emails, which CommonMark has no concept of.
function withoutAnchors(nodes) {
  return nodes.flatMap((n) => {
    if (!isTag(n)) return [n];
    if (isA(n)) return withoutAnchors(n.slice(2));
    return [[n[0], ...withoutAnchors(n.slice(n[0] === 'img' ? 3 : 1))]];
  });
}

/* ---------- run ---------- */

// A raw HTML construct: a block tag / comment / doctype at the start of a line, or an inline tag.
// Autolinks like <https://x> or <foo+@bar> are excluded because a tag name is never followed by
// ":", "+", "\\" or "@" in this pattern.
const RAW_HTML = /<[!?]|<!--|<\/?[a-zA-Z][\w-]*(?=[\s/>]|$)/;

let pass = 0, skip = 0, fail = 0;
const failures = [];
const skips = new Map();

(async () => {
  const mod = await import(pathToFileURL(compiledPath).href);
  const examples = corpus.slice(0, Number.isFinite(limit) ? limit : corpus.length);
  for (const example of examples) {
    let html;
    try {
      html = renderToStaticMarkup(React.createElement(mod.Markdown, {text: example.markdown, density: 'compact'}));
    } catch (err) {
      fail++; failures.push({example, why: `threw: ${err.message}`}); continue;
    }
    const want = treeOf(example.html, {expected: true});
    const got = treeOf(html, {expected: false});
    if (eq(want, got)) { pass++; continue; }
    const skipped = (reason) => {
      skip++;
      skips.set(reason, (skips.get(reason) || 0) + 1);
      if (verbose) console.log(`SKIP #${example.example} [${example.section}] ${reason}`);
    };
    if (RAW_HTML.test(example.markdown)) { skipped('raw HTML dropped'); continue; }
    // react-markdown's urlTransform blanks protocols outside http/https/mailto/xmpp/irc/relative.
    // The visible text must survive; only the href may be lost.
    if (emptyHrefs(got) > 0 && emptyHrefs(want) === 0 && eq(withoutHrefs(want), withoutHrefs(got))) {
      if (flatText(want) !== flatText(got)) {
        fail++; failures.push({example, why: 'href stripped but text changed', want: JSON.stringify(want), got: JSON.stringify(got)});
      } else skipped('unsafe href stripped');
      continue;
    }
    // remark-gfm autolink literals: the only difference is an added <a> around the same text.
    if (hrefCount(got) > hrefCount(want) && flatText(want) === flatText(got) && eq(shape(withoutAnchors(want)), shape(withoutAnchors(got)))) {
      skipped('gfm autolink literal');
      continue;
    }
    fail++;
    failures.push({example, want: JSON.stringify(want), got: JSON.stringify(got)});
  }

  for (const f of failures.slice(0, verbose ? 50 : 10)) {
    console.log(`\nFAIL #${f.example.example} [${f.example.section}]`);
    console.log('  markdown: ' + JSON.stringify(f.example.markdown));
    console.log('  expected: ' + (f.why || f.want));
    console.log('  actual:   ' + (f.why ? '' : f.got));
  }
  console.log(`\nCommonMark 0.31.2 (${corpus.length} examples, sha256 ${sha.slice(0, 12)}…)`);
  console.log(`  passed:  ${pass}`);
  console.log(`  skipped: ${skip}`);
  for (const [reason, count] of [...skips].sort((a, b) => b[1] - a[1])) console.log(`           ${count} × ${reason}`);
  console.log(`  failed:  ${fail}`);

  /* ---------- our contract on top of the spec ---------- */
  // Everything the corpus deliberately skips is asserted here, plus the deltas we add
  // (heading demotion, safe links, table scrolling, issue chips, size cap, parse cache).
  const contract = [];
  const check = (name, ok, detail = '') => contract.push({name, ok, detail});
  const render = (text, density = 'compact') => renderToStaticMarkup(React.createElement(mod.Markdown, {text, density}));

  const attack = render('<img src=x onerror="window.__pwned=1"> <script>window.__pwned=1</script>');
  check('raw HTML is inert text, not nodes', !/<(img|script)/i.test(attack) && attack.includes('&lt;script&gt;') || attack.includes('&lt;img'), attack.slice(0, 120));

  const js = render('[click me](javascript:window.__pwned=1)');
  check('javascript: href is stripped, label kept', js.includes('click me') && !/href="javascript:/i.test(js), js.slice(0, 120));

  const data = render('[x](data:text/html;base64,PHNjcmlwdD4=)');
  check('data: href is stripped', !/href="data:/i.test(data), data.slice(0, 120));

  const external = render('[docs](https://example.com/a)');
  check('external link opens safely', /target="_blank"/.test(external) && /rel="[^"]*noopener[^"]*"/.test(external) && /nofollow/.test(external), external);

  const internal = render('[settings](/workspace/settings)');
  check('internal link stays in the tab', !/target=/.test(internal) && /href="\/workspace\/settings"/.test(internal), internal);

  const headings = render('# One\n\n## Two\n\n##### Five\n\n###### Six');
  check('headings demote to h3-h6 and never emit h1/h2', !/<h[12][\s>]/.test(headings) && headings.includes('<h3') && headings.includes('<h6'), headings);

  const chip = render('Blocked by TREBOL-12 today.');
  check('bare issue identifiers become chips', chip.includes('agent-issue-chip') && chip.includes('href="/workspace/issue/TREBOL-12"'), chip);

  const fenced = render('Example: `TREBOL-12` in code.\n\n```\nTREBOL-13\n```');
  check('issue identifiers inside code stay literal', !fenced.includes('/workspace/issue/TREBOL-12') && !fenced.includes('/workspace/issue/TREBOL-13'), fenced);

  const table = render('| a | b |\n| - | - |\n| 1 | 2 |');
  check('tables get their own scroll box', table.includes('class="md-table"') && table.includes('<table>'), table);

  const tasks = render('- [x] done\n- [ ] todo');
  check('task list checkboxes are read-only', (tasks.match(/disabled/g) || []).length === 2 && /aria-label="Completed task"/.test(tasks) && /aria-label="Open task"/.test(tasks), tasks);

  const long = 'x'.repeat(20000 + 1);
  const capped = render(long);
  check('oversized bodies render verbatim instead of parsing', capped.includes('data-md-plain="true"') && capped.includes(long), `${capped.length} bytes`);

  mod.resetMarkdownStats();
  render('cache probe body');
  render('cache probe body');
  const stats = mod.markdownStats();
  check('identical bodies are parsed once', stats.parses === 1 && stats.hits === 1, JSON.stringify(stats));

  const failed = contract.filter((c) => !c.ok);
  console.log(`\nRenderer contract (${contract.length} checks)`);
  for (const c of contract) console.log(`  ${c.ok ? 'PASS' : 'FAIL'} ${c.name}${c.ok ? '' : ` [${c.detail}]`}`);
  console.log(`  failed: ${failed.length}`);

  process.exit(fail === 0 && failed.length === 0 ? 0 : 1);
})();
