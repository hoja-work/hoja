#!/usr/bin/env node
/** Apply Tailwind IntelliSense's canonical-class suggestions, or exact duplicate conflicts.
 * Run from app/frontend: node scripts/fix-tailwind-intellisense.mjs [--duplicates] [--write] [--root-font-size 16] [paths...]
 * --duplicates selects ONLY identical class tokens in a single class list (not CSS conflicts).
 * Default is a dry run. Files are discovered via git, so ignored files are never modified.
 */
import { spawn, execFileSync } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import { resolve, relative, extname } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));
const args = process.argv.slice(2);
const write = args.includes('--write');
const duplicates = args.includes('--duplicates');
const fontArg = args.indexOf('--root-font-size');
const rootFontSize = fontArg < 0 ? 16 : Number(args[fontArg + 1]);
if (!Number.isFinite(rootFontSize) || rootFontSize <= 0) throw Error('Invalid --root-font-size');
const paths = args.filter((arg, i) => arg !== '--write' && arg !== '--duplicates' && arg !== '--root-font-size' && (fontArg < 0 || i !== fontArg + 1));
const extensions = new Map([['.tsx', 'typescriptreact'], ['.ts', 'typescript'], ['.jsx', 'javascriptreact'], ['.js', 'javascript'], ['.html', 'html'], ['.css', 'css']]);
const tracked = execFileSync('git', ['ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', ...(paths.length ? paths : ['src'])], { cwd: root }).toString().split('\0').filter(Boolean);
const files = tracked.filter(p => extensions.has(extname(p)) && !p.includes('/node_modules/'));

// The published language server is the same diagnostic engine used by Tailwind IntelliSense in Zed.
const server = spawn(process.execPath, [resolve(root, 'node_modules/@tailwindcss/language-server/bin/tailwindcss-language-server'), '--stdio'], { cwd: root, stdio: ['pipe', 'pipe', 'pipe'] });
let buffer = Buffer.alloc(0), nextId = 1;
const pending = new Map(), listeners = new Map();
let stderr = '';
server.stderr.on('data', b => { stderr += b.toString(); });
function send(message) {
  const body = Buffer.from(JSON.stringify(message));
  server.stdin.write(`Content-Length: ${body.length}\r\n\r\n`);
  server.stdin.write(body);
}
function request(method, params) {
  const id = nextId++;
  send({ jsonrpc: '2.0', id, method, params });
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}
function notify(method, params) { send({ jsonrpc: '2.0', method, params }); }
function once(method, accept = () => true) {
  return new Promise(resolve => {
    const list = listeners.get(method) || [];
    list.push({ accept, resolve });
    listeners.set(method, list);
  });
}
function receive(msg) {
  if (msg.id != null && !msg.method) {
    const task = pending.get(msg.id);
    if (task) { pending.delete(msg.id); msg.error ? task.reject(Error(msg.error.message)) : task.resolve(msg.result); }
  } else if (msg.method && msg.id != null) {
    // Server asks the client for per-document configuration and dynamic capability registration.
    let result = null;
    if (msg.method === 'workspace/configuration') result = msg.params.items.map(({ section }) => section === 'tailwindCSS' ? { rootFontSize, lint: { suggestCanonicalClasses: 'warning' } } : {});
    send({ jsonrpc: '2.0', id: msg.id, result });
  } else if (msg.method) {
    const list = listeners.get(msg.method) || [];
    const index = list.findIndex(item => item.accept(msg.params));
    if (index >= 0) { list.splice(index, 1)[0].resolve(msg.params); }
  }
}
server.stdout.on('data', chunk => {
  buffer = Buffer.concat([buffer, chunk]);
  while (true) {
    const end = buffer.indexOf('\r\n\r\n');
    if (end < 0) break;
    const length = /Content-Length:\s*(\d+)/i.exec(buffer.subarray(0, end).toString())?.[1];
    if (!length) throw Error('Invalid language-server response');
    const size = Number(length);
    if (buffer.length < end + 4 + size) break;
    receive(JSON.parse(buffer.subarray(end + 4, end + 4 + size).toString()));
    buffer = buffer.subarray(end + 4 + size);
  }
});
server.on('exit', (code) => {
  for (const task of pending.values()) task.reject(Error(`Language server exited (${code}): ${stderr}`));
});
function offset(text, position) {
  let start = 0;
  for (let i = 0; i < position.line; i++) {
    const newline = text.indexOf('\n', start);
    if (newline < 0) throw Error('Invalid diagnostic line');
    start = newline + 1;
  }
  return start + position.character; // LSP offsets and JS string indexes are both UTF-16.
}
function apply(text, diagnostics, uri) {
  const edits = [];
  if (duplicates) {
    const redundant = new Set();
    for (const diagnostic of diagnostics) {
      if (diagnostic.code !== 'cssConflict' || diagnostic.source !== 'tailwindcss') continue;
      const start = offset(text, diagnostic.range.start), end = offset(text, diagnostic.range.end);
      const token = text.slice(start, end);
      // Related ranges are supplied by IntelliSense for conflicts within the same class list.
      // Only compare literal, byte-identical tokens: bg-red-300 and bg-blue-300 stay untouched.
      for (const related of diagnostic.relatedInformation || []) {
        const otherStart = offset(text, related.location.range.start);
        const otherEnd = offset(text, related.location.range.end);
        if (related.location.uri !== uri || token !== related.message || token !== text.slice(otherStart, otherEnd)) continue;
        redundant.add(Math.max(start, otherStart));
      }
    }
    for (const start of redundant) {
      const diagnostic = diagnostics.find(d => d.code === 'cssConflict' && offset(text, d.range.start) === start);
      if (!diagnostic) continue;
      const end = offset(text, diagnostic.range.end);
      // Consume one adjacent horizontal separator, never markup, quotes or a line break.
      const from = start > 0 && /[ \t]/.test(text[start - 1]) ? start - 1 : start;
      const to = from === start && /[ \t]/.test(text[end] || '') ? end + 1 : end;
      edits.push({ start: from, end: to, replacement: '' });
    }
  } else for (const diagnostic of diagnostics) {
    if (diagnostic.code !== 'suggestCanonicalClasses' || diagnostic.source !== 'tailwindcss') continue;
    const replacement = diagnostic.suggestions?.[0];
    if (typeof replacement !== 'string') continue;
    const start = offset(text, diagnostic.range.start), end = offset(text, diagnostic.range.end);
    const original = text.slice(start, end);
    if (!diagnostic.message.includes(`\`${original}\` can be written as \`${replacement}\``)) throw Error(`Unexpected diagnostic at ${start}: ${diagnostic.message}`);
    edits.push({ start, end, replacement });
  }
  edits.sort((a, b) => b.start - a.start);
  for (let i = 1; i < edits.length; i++) if (edits[i].end > edits[i - 1].start) throw Error('Overlapping fixes');
  for (const { start, end, replacement } of edits) text = text.slice(0, start) + replacement + text.slice(end);
  return { text, count: edits.length };
}
async function main() {
  const rootUri = pathToFileURL(root).href;
  await request('initialize', {
    processId: process.pid, rootUri, workspaceFolders: [{ name: 'frontend', uri: rootUri }], initializationOptions: { testMode: true },
    capabilities: { workspace: { configuration: true, workspaceFolders: true }, textDocument: { publishDiagnostics: {}, codeAction: {} } },
  });
  notify('initialized', {});
  await Promise.race([once('@/tailwindCSS/serverReady'), new Promise((_, reject) => setTimeout(() => reject(Error(`Tailwind server did not become ready: ${stderr}`)), 30000))]);
  let total = 0, changed = 0;
  for (const path of files) {
    const full = resolve(root, path), uri = pathToFileURL(full).href;
    const source = await readFile(full, 'utf8');
    const ready = once('@/tailwindCSS/documentReady', p => p.uri === uri);
    const diagnostics = once('textDocument/publishDiagnostics', p => p.uri === uri);
    notify('textDocument/didOpen', { textDocument: { uri, languageId: extensions.get(extname(path)), version: 1, text: source } });
    await Promise.race([ready, new Promise((_, reject) => setTimeout(() => reject(Error(`Timeout opening ${path}: ${stderr}`)), 30000))]);
    const result = await Promise.race([diagnostics, new Promise((_, reject) => setTimeout(() => reject(Error(`Timeout diagnosing ${path}: ${stderr}`)), 30000))]);
    const fixed = apply(source, result.diagnostics, uri);
    if (fixed.count) {
      console.log(`${relative(root, full)}: ${fixed.count} ${duplicates ? 'duplicate removal(s)' : 'canonical replacement(s)'}${write ? ' applied' : ' available'}`);
      if (write) await writeFile(full, fixed.text);
      changed++; total += fixed.count;
    }
    notify('textDocument/didClose', { textDocument: { uri } });
  }
  console.log(`${write ? 'Applied' : 'Found'} ${total} replacement(s) in ${changed} file(s).${write ? '' : ' Pass --write to apply.'}`);
  await request('shutdown', null);
  notify('exit');
}
try { await main(); } catch (error) { console.error(error); process.exitCode = 1; } finally { server.kill(); }
