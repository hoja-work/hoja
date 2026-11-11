// Offline regression test for the ui-probes click helper and dark-contrast.cjs. Run from the repo root:
//   node scripts/test-dark-contrast-locator.cjs
// It serves a fixture at / that mirrors the My issues tab strip (nav.my-issue-tabs with
// <button role="tab">) plus an ordinary labelled button, then checks:
//   1. the historical locator page.getByRole('button', { name: 'Created', exact: true }) matches nothing,
//      which is the reported defect: an explicit role="tab" overrides the implicit button role;
//   2. clickControl resolves the tab by role and the ordinary button by role (the near-miss that must
//      keep working);
//   3. a missing label fails fast (< 30s) and names the labels that do exist;
//   4. dark-contrast.cjs end-to-end with "Created", a missing label, and "-" behaves per the report.
const { chromium } = require('../local-e2e/node_modules/playwright');
const { clickControl } = require('./ui-probes/click-control.cjs');
const { spawn } = require('node:child_process');
const http = require('node:http');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const FIXTURE = `<!doctype html><html><head><meta charset="utf-8"><style>
body{background:#fff;color:#000;font:14px system-ui;margin:0;padding:8px}
.my-issue-tabs{display:flex;gap:6px}.my-issue-tabs>button{background:#fff;color:#000;border:1px solid #000;padding:6px 10px}
</style></head><body>
<nav class="my-issue-tabs" aria-label="My issue views">
  <button role="tab" aria-selected="true">Assigned</button>
  <button role="tab" aria-selected="false">Created</button>
  <button role="tab" aria-selected="false">Subscribed</button>
  <button role="tab" aria-selected="false">Activity</button>
</nav>
<div><button>Settings</button></div>
<p>Contrast fixture text</p>
</body></html>`;

let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };

// A scripted <button role="tab"> is only as good as the browser's view of it, so assert on the real
// accessibility tree rather than on the fixture string.
async function runProbe(port, label) {
  const started = Date.now();
  const child = spawn(process.execPath, [path.join(repo, 'scripts/ui-probes/dark-contrast.cjs'), label, '4.5'], {
    cwd: repo,
    env: { ...process.env, PROBE_URL: `http://127.0.0.1:${port}`, PROBE_TOKEN: 'fixture-token' },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let stdout = '', stderr = '';
  child.stdout.on('data', d => { stdout += d; });
  child.stderr.on('data', d => { stderr += d; });
  const status = await new Promise(resolve => {
    const timer = setTimeout(() => { child.kill('SIGKILL'); resolve('TIMEOUT'); }, 75000);
    child.on('close', code => { clearTimeout(timer); resolve(code); });
  });
  return { status, stdout, stderr, ms: Date.now() - started };
}

(async () => {
  const server = http.createServer((req, res) => {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    res.end(FIXTURE);
  });
  await new Promise(r => server.listen(0, '127.0.0.1', r));
  const port = server.address().port;
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${port}/`);

    const asButton = await page.getByRole('button', { name: 'Created', exact: true }).count();
    const asTab = await page.getByRole('tab', { name: 'Created', exact: true }).count();
    ok(asButton === 0, 'legacy role=button locator matches no tab (defect reproduced)', `button=${asButton} tab=${asTab}`);
    ok(asTab === 1, 'role=tab locator matches exactly one tab');

    ok(await clickControl(page, 'Created') === 'role=tab', 'clickControl clicks the tab by role');
    ok(await clickControl(page, 'Settings') === 'role=button', 'clickControl still clicks an ordinary labelled button');
    const selected = await page.getByRole('tab', { selected: true }).first().innerText().catch(() => '');
    ok(typeof selected === 'string', 'tab strip remains queryable after the click', `selected=${JSON.stringify(selected)}`);

    const t0 = Date.now();
    let err = null;
    try { await clickControl(page, 'Nonexistent'); } catch (e) { err = e; }
    const missingMs = Date.now() - t0;
    ok(err !== null, 'a missing label throws instead of hanging');
    ok(missingMs < 30000, 'the missing label fails fast', `${missingMs}ms`);
    ok(err && /"Nonexistent"/.test(err.message), 'the error names the missing control');
    ok(err && /"Assigned"/.test(err.message), 'the error lists the labels that do exist');

    const clicked = await runProbe(port, 'Created');
    ok(clicked.status === 0, 'dark-contrast.cjs Created exits 0 on a passing fixture', `status=${clicked.status} ${clicked.stderr.slice(0, 200)}`);
    ok(/view=Created checked=\d+ below 4\.5:0/.test(clicked.stdout), 'dark-contrast.cjs Created reports a checked count', clicked.stdout.split('\n')[0]);
    ok(clicked.ms < 60000, 'dark-contrast.cjs Created completes under 60s', `${clicked.ms}ms`);

    const missing = await runProbe(port, 'Nonexistent');
    ok(missing.status !== 0 && missing.status !== 'TIMEOUT', 'dark-contrast.cjs with a missing control exits non-zero', `status=${missing.status}`);
    ok(/"Nonexistent"/.test(missing.stderr), 'dark-contrast.cjs names the missing control on stderr', missing.stderr.split('\n')[0]);
    ok(missing.ms < 30000, 'dark-contrast.cjs fails the missing control within 30s', `${missing.ms}ms`);

    const dash = await runProbe(port, '-');
    ok(dash.status === 0, 'dark-contrast.cjs "-" audits the default view', `status=${dash.status} ${dash.stderr.slice(0, 200)}`);
    ok(/view=- checked=\d+ below 4\.5:0/.test(dash.stdout), 'dark-contrast.cjs "-" reports 0 failures', dash.stdout.split('\n')[0]);
  } finally {
    await browser.close();
    server.close();
  }
  console.log(`\n${fail ? 'FAIL' : 'PASS'}: ${pass} checks passed, ${fail} failed`);
  process.exitCode = fail ? 1 : 0;
})().catch(e => { console.error(e); process.exitCode = 1; });
