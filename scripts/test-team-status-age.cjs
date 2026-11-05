// Independent, end-to-end proof that the Team issues list row answers "how long has
// this status been held?" on hover and focus.
//   cd frontend && npm run build
//   node scripts/test-team-status-age.cjs
// The fixture is written straight into SQLite (2d 3h before the run), so the assertion
// does not trust the module under test: it knows the raw timestamp and the expected
// sentence, then reads what the browser actually paints.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const playwrightCandidates = [
  process.env.PLAYWRIGHT_MODULE,
  '../local-e2e/node_modules/playwright',
  '/Users/swarm/Work/Clover/clinear/local-e2e/node_modules/playwright',
].filter(Boolean);
const playwrightPath = playwrightCandidates.find(p => fs.existsSync(path.join(p, 'package.json')) || fs.existsSync(p));
assert.ok(playwrightPath, `playwright not found; set PLAYWRIGHT_MODULE (tried ${playwrightCandidates.join(', ')})`);
const { chromium } = require(playwrightPath);

const repo = path.resolve(__dirname, '..');
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');
const dir = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'status-age-'));
const db = path.join(dir, 'status-age.db');
const token = 'hoja_status_age_token';
const py = process.env.HOJA_PYTHON || 'python3';
const AGE_MS = (2 * 24 + 3) * 60 * 60 * 1000; // 2d 3h
const since = new Date(Date.now() - AGE_MS).toISOString();

const seed = spawnSync(py, ['-c', `from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()
import sqlite3
c=sqlite3.connect(${JSON.stringify(db)})
row=c.execute("select i.id,s.name from issue i join workflow_state s on s.id=i.state_id where s.type in ('started','unstarted') order by i.created_at limit 1").fetchone()
assert row, 'seed produced no active issue'
c.execute("update issue set title='Status age probe', state_changed_at=?, updated_at=? where id=?", (${JSON.stringify(since)}, ${JSON.stringify(since)}, row[0]))
c.commit()
print(row[1])`], { cwd: repo, env: {...process.env, PYTHONPATH: repo}, encoding: 'utf8' });
assert.equal(seed.status, 0, seed.stderr);
const stateName = seed.stdout.trim().split('\n').pop();

const port = 19090 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(py, ['-m', 'hoja_server.cli', 'serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
  { cwd: repo, env: {...process.env, HOJA_WEB_ROOT: web}, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));

const fmt = ms => {
  const m = Math.floor(ms / 60000), h = Math.floor(m / 60), d = Math.floor(h / 24);
  if (d > 0) return `${d}d ${h % 24}h`;
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m`;
  return 'just now';
};
const expected = `In ${stateName} for ${fmt(AGE_MS)}`;

(async () => {
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch {} await wait(250); }
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({viewport: {width: 1280, height: 800}});
    await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    await page.goto(`${base}/workspace/team/TREBOL/active`);
    await page.getByText('Status age probe', {exact: true}).waitFor({timeout: 15000});

    const row = page.locator('.lin-row', {hasText: 'Status age probe'}).first();
    const icon = row.locator('.lin-status-age');
    await icon.waitFor();

    const aria = await icon.getAttribute('aria-label');
    assert.ok(aria?.includes(expected), `aria-label missing expected age: ${aria}`);

    await icon.hover();
    const tip = row.locator('.lin-status-age-tip');
    await tip.waitFor({state: 'visible', timeout: 3000});
    assert.equal((await tip.textContent())?.trim(), expected, 'hover tooltip text differs from the raw timestamp');

    await page.mouse.move(0, 0);
    await tip.waitFor({state: 'hidden', timeout: 3000});
    await icon.focus();
    await tip.waitFor({state: 'visible', timeout: 3000});
    assert.equal((await tip.textContent())?.trim(), expected, 'focus tooltip text differs from the raw timestamp');

    // The symbol must not have swallowed row navigation.
    await row.locator('button.title').click();
    await page.waitForURL(/\/workspace\/issue\/TREBOL-\d+/, {timeout: 10000});

    console.log(`ok   row renders "${expected}" on hover and focus (state read from SQLite, rendered by the built app)`);
    console.log('ok   title click still opens the issue');
  } finally {
    await browser?.close();
    server.kill();
    fs.rmSync(dir, {recursive: true, force: true});
  }
})().catch(error => { console.error(error); server.kill(); process.exitCode = 1; });
