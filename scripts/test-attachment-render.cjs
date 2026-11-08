// Attachments on the issue detail panel: seeded server + built frontend + real Chromium.
// Run from app/ after `cd frontend && npm run build`:
//   node scripts/test-attachment-render.cjs
//   SHOTS=artifacts/attachments node scripts/test-attachment-render.cjs
//
// What it protects (each of these was broken on dev.hoja.work before HOJA-175):
//   * an uploaded screenshot renders a preview, not a generic link icon;
//   * the preview is a real decoded image (naturalWidth > 0), not a broken-image glyph;
//   * a dead preview URL degrades to the document icon instead of a torn-page glyph;
//   * a PDF/CSV/Markdown attachment is listed as a document and never fetched as an image;
//   * a screenshot in a description or a comment renders from its relative upload URL.
const { chromium } = require('../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');
const scratch = path.join(repo, 'local-e2e/artifacts');
fs.mkdirSync(scratch, { recursive: true });
const dir = fs.mkdtempSync(path.join(scratch, 'attachments-'));
const db = path.join(dir, 'p.db');
const token = 'hoja_attachment_render_token';
const py = path.join(repo, '.venv/bin/python');
const run = code => {
  const r = spawnSync(py, ['-c', code], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  assert.equal(r.status, 0, r.stderr);
  return r.stdout;
};
/* A real `hoja_session` cookie, like /auth/google/finish mints: `<img src>` requests carry no
 * Authorization header, so an attachment preview only loads for a cookie session. */
const SESSION = run(`from hoja_server.db import make_engine,migrate,seed_tenant,organization_membership
from hoja_server.store import Store
from sqlalchemy import select
e=make_engine(${JSON.stringify(db)}); migrate(e)
s=seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
st=Store(e)
with e.connect() as c:
    identity=c.execute(select(organization_membership.c.identity_id).where(organization_membership.c.profile_user_id==s['user_id'])).scalar()
raw,_=st.create_session_for_membership(identity, s['user_id'], s['org_id'])
print(raw)
e.dispose()`).trim();
assert.ok(SESSION.length > 20, 'minted a browser session');

/* ---------- fixtures: synthetic bytes only, nothing personal ---------- */
function png(width, height, pixel) {
  const raw = Buffer.alloc((width * 3 + 1) * height);
  let o = 0;
  for (let y = 0; y < height; y++) {
    raw[o++] = 0;
    for (let x = 0; x < width; x++) {
      const [r, g, b] = pixel(x, y);
      raw[o++] = r; raw[o++] = g; raw[o++] = b;
    }
  }
  const chunk = (type, data) => {
    const len = Buffer.alloc(4); len.writeUInt32BE(data.length);
    const body = Buffer.concat([Buffer.from(type, 'ascii'), data]);
    const crc = Buffer.alloc(4); crc.writeUInt32BE(zlib.crc32 ? zlib.crc32(body) : crc32(body));
    return Buffer.concat([len, body, crc]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; ihdr[9] = 2;
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr),
    chunk('IDAT', zlib.deflateSync(raw)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}
function crc32(buf) {
  let c, crc = 0xffffffff;
  for (let n = 0; n < buf.length; n++) {
    c = (crc ^ buf[n]) & 0xff;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    crc = (crc >>> 8) ^ c;
  }
  return (crc ^ 0xffffffff) >>> 0;
}

const SCREENSHOT = png(240, 120, (x, y) => (y < 12 ? [24, 24, 27] : x < 120 ? [59, 130, 246] : [34, 197, 94]));
const BIG = png(1200, 800, (x, y) => [x % 256, y % 256, 160]);
const PDF = Buffer.from(
  '%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n' +
  '2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n' +
  '3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>\nendobj\n' +
  'trailer\n<< /Size 4 /Root 1 0 R >>\n%%EOF\n', 'utf8');

/* ---------- server ---------- */
const port = 19500 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
  { cwd: repo, env: { ...process.env, PYTHONPATH: repo, HOJA_WEB_ROOT: web, XDG_DATA_HOME: dir }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));
const gql = async (query, variables) => {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const b = await r.json(); if (b.errors) throw new Error(b.errors[0].message); return b.data;
};
const upload = async (name, type, body) => {
  const fd = new FormData();
  fd.append('file', new Blob([body], { type }), name);
  const r = await fetch(`${base}/api/uploads`, { method: 'POST', headers: { authorization: `Bearer ${token}` }, body: fd });
  const b = await r.json();
  return { status: r.status, ...b };
};

let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => {
  cond ? pass++ : fail++;
  console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`);
};
const shots = process.env.SHOTS;

const CELLS = [
  { theme: 'light', width: 1440, height: 900 },
  { theme: 'dark', width: 1440, height: 900 },
  { theme: 'light', width: 390, height: 844 },
  { theme: 'dark', width: 390, height: 844 },
];

(async () => {
  for (let i = 0; i < 80; i++) { try { await gql('{viewer{id}}'); break; } catch { await wait(250); } }
  const d = await gql('{viewer{id} teams{nodes{id key}}}');
  const team = d.teams.nodes[0];

  /* ---------- upload the four shapes of thing people attach ---------- */
  const shot = await upload('screenshot.png', 'image/png', SCREENSHOT);
  const big = await upload('wide-photo.png', 'image/png', BIG);
  const pdf = await upload('report.pdf', 'application/pdf', PDF);
  const csv = await upload('data.csv', 'text/csv', Buffer.from('id,name\n1,alpha\n', 'utf8'));
  ok(shot.status === 200 && /\.png$/.test(shot.url || ''), 'upload: a screenshot URL keeps its extension', String(shot.url));
  ok(csv.status === 200, 'upload: a CSV document is accepted', `${csv.status} ${csv.error || ''}`);
  assert.ok(shot.url && big.url && pdf.url && csv.url, 'every fixture uploaded');

  const made = await gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){issue{id identifier title}}}',
    { i: { title: 'Attachment render probe', teamId: team.id } });
  const issue = made.issueCreate.issue;
  const attach = async (url, title) => {
    const r = await gql('mutation($i:AttachmentCreateInput!){attachmentCreate(input:$i){success attachment{id contentType}}}', { i: { issueId: issue.id, url, title } });
    return r.attachmentCreate.attachment;
  };
  const shotRow = await attach(shot.url, 'screenshot.png');
  await attach(big.url, 'wide-photo.png');
  const pdfRow = await attach(pdf.url, 'report.pdf');
  await attach(csv.url, 'data.csv');
  // A preview URL that answers 404: the row must fall back, not show a torn page.
  const deadRow = await attach('/api/uploads/00000000000000000000000000000000.png', 'dead-preview.png');
  ok(shotRow.contentType === 'image/png', 'attachment: the stored media type comes back to the client', String(shotRow.contentType));
  ok(pdfRow.contentType === 'application/pdf', 'attachment: a document keeps its own type', String(pdfRow.contentType));

  await gql('mutation($id:String!,$i:IssueUpdateInput!){issueUpdate(id:$id,input:$i){success}}', {
    id: issue.id,
    i: { description: ['## Probe', '', 'Uploaded screenshot:', '', `![screenshot](${shot.url})`, '', `[report.pdf](${pdf.url})`].join('\n') },
  });
  await gql('mutation($i:CommentCreateInput!){commentCreate(input:$i){success}}', {
    i: { issueId: issue.id, body: ['Comment image:', '', `![in comment](${shot.url})`].join('\n') },
  });

  const browser = await chromium.launch({ headless: true });
  const issuePath = `/workspace/issue/${issue.identifier}`;

  for (const cell of CELLS) {
    await gql('mutation($k:String!,$v:String!){userPreferenceSet(key:$k,value:$v){success}}', { k: 'theme', v: cell.theme });
    const ctx = await browser.newContext({ viewport: { width: cell.width, height: cell.height }, deviceScaleFactor: cell.width < 500 ? 3 : 1, isMobile: cell.width < 500 });
    await ctx.addCookies([{ name: 'hoja_session', value: SESSION, domain: '127.0.0.1', path: '/', httpOnly: true, secure: false, sameSite: 'Lax' }]);
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    const tag = `${cell.theme} ${cell.width}x${cell.height}`;
    await page.goto(`${base}${issuePath}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('[aria-label="Attachments"] .lin-attach', { timeout: 15000 });
    await page.waitForSelector('.lin-comment .md img', { timeout: 15000 });
    // Previews are `loading="lazy"`: bring the panel into view before judging them, the
    // way a reader does. A row that never loads after this is a real render failure.
    await page.locator('[aria-label="Attachments"]').scrollIntoViewIfNeeded();
    await page.waitForTimeout(600);

    const state = await page.evaluate(() => {
      const rows = [...document.querySelectorAll('[aria-label="Attachments"] .lin-attach')].map(r => {
        const img = r.querySelector('img.thumb');
        return {
          title: r.querySelector('a')?.textContent || '',
          href: r.querySelector('a')?.getAttribute('href') || '',
          thumb: img ? { nw: img.naturalWidth, complete: img.complete } : null,
          icon: !!r.querySelector('svg'),
        };
      });
      const md = [...document.querySelectorAll('.md img.md-img')].map(i => ({
        src: i.getAttribute('src'), nw: i.naturalWidth, cw: Math.round(i.getBoundingClientRect().width),
      }));
      return {
        rows, md,
        theme: document.documentElement.dataset.theme,
        brokenImgs: [...document.querySelectorAll('img')].filter(i => i.complete && i.naturalWidth === 0).length,
        hscroll: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      };
    });

    const row = t => state.rows.find(r => r.title === t);
    ok(state.theme === cell.theme, `${tag}: theme applies`, state.theme);
    ok(!!row('screenshot.png')?.thumb, `${tag}: an uploaded screenshot has a preview`);
    ok(row('screenshot.png')?.thumb?.nw > 0, `${tag}: the preview is a decoded image, not a broken glyph`, JSON.stringify(row('screenshot.png')?.thumb));
    ok(row('wide-photo.png')?.thumb?.nw > 0, `${tag}: a wide screenshot previews too`, JSON.stringify(row('wide-photo.png')?.thumb));
    ok(!!row('report.pdf')?.icon && !row('report.pdf')?.thumb, `${tag}: a PDF is listed as a document, never fetched as an image`);
    ok(!!row('data.csv')?.icon && !row('data.csv')?.thumb, `${tag}: a CSV is listed as a document`);
    ok(/\.pdf$/.test(row('report.pdf')?.href || ''), `${tag}: a document link keeps its extension`, String(row('report.pdf')?.href));
    ok(!row('dead-preview.png')?.thumb && !!row('dead-preview.png')?.icon, `${tag}: a dead preview URL falls back to the icon`);
    ok(state.md.length === 2, `${tag}: description and comment images are both rendered`, `${state.md.length}`);
    ok(state.md.every(i => i.nw > 0), `${tag}: markdown images decode (relative upload URLs resolve)`, JSON.stringify(state.md));
    ok(state.md.every(i => i.cw > 0 && i.cw <= cell.width), `${tag}: markdown images fit the column`, JSON.stringify(state.md.map(i => i.cw)));
    ok(state.brokenImgs === 0, `${tag}: no broken image anywhere on the page`, `${state.brokenImgs}`);
    ok(!state.hscroll, `${tag}: no horizontal page scroll`);
    ok(errors.length === 0, `${tag}: no page errors`, errors.slice(0, 2).join(' | '));

    /* The real path a person takes: Attach -> Upload file -> pick a screenshot. This drives
     * POST /api/uploads and attachmentCreate through the UI, not through the test's own gql. */
    if (cell.theme === 'light' && cell.width === 1440) {
      await page.click('[aria-label="Attach file or link"]');
      const [chooser] = await Promise.all([
        page.waitForEvent('filechooser'),
        page.click('button:has-text("Upload file")'),
      ]);
      await chooser.setFiles({ name: 'ui-upload.png', mimeType: 'image/png', buffer: SCREENSHOT });
      await page.waitForFunction(
        () => [...document.querySelectorAll('[aria-label="Attachments"] .lin-attach a')].some(a => a.textContent === 'ui-upload.png'),
        null, { timeout: 15000 });
      await page.locator('[aria-label="Attachments"]').scrollIntoViewIfNeeded();
      await page.waitForFunction(() => {
        const row = [...document.querySelectorAll('[aria-label="Attachments"] .lin-attach')]
          .find(r => r.querySelector('a')?.textContent === 'ui-upload.png');
        const img = row?.querySelector('img.thumb');
        return !!img && img.complete && img.naturalWidth > 0;
      }, null, { timeout: 15000 });
      const uiRow = await page.evaluate(() => {
        const row = [...document.querySelectorAll('[aria-label="Attachments"] .lin-attach')]
          .find(r => r.querySelector('a')?.textContent === 'ui-upload.png');
        const img = row?.querySelector('img.thumb');
        return { href: row?.querySelector('a')?.getAttribute('href') || '', nw: img ? img.naturalWidth : 0 };
      });
      ok(/\.png$/.test(uiRow.href), 'ui upload: the panel stores an extension-bearing URL', uiRow.href);
      ok(uiRow.nw > 0, 'ui upload: the new screenshot previews without a reload', JSON.stringify(uiRow));
    }

    if (shots) {
      fs.mkdirSync(shots, { recursive: true });
      await page.locator('[aria-label="Attachments"]').scrollIntoViewIfNeeded();
      await page.screenshot({ path: path.join(shots, `attachments-${cell.theme}-${cell.width}.png`) });
    }

    await ctx.close();
  }

  await browser.close();
  server.kill('SIGKILL');
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail === 0 ? 0 : 1);
})().catch(async e => { console.error(e); server.kill('SIGKILL'); process.exit(1); });
