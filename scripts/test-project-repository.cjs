#!/usr/bin/env node
/* A project's repository, set on the project page, is where its agent tasks work (HOJA-602).
 *
 *   project page -> Repository -> acme/website -> a task in the project, delegated to an agent
 *   -> the runner's lease names acme/website on agent/<task>, "from project Website"
 * Needs app/.venv, app/frontend/dist and Playwright (PLAYWRIGHT_MODULE or local-e2e).
 */
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '../local-e2e/node_modules/playwright');

const repo = path.resolve(__dirname, '..');
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'project-repo-'));
const db = path.join(dir, 'p.db');
const shots = process.env.SHOTS || dir;
const token = 'project-repo-admin';
const py = path.join(repo, '.venv/bin/python');
const seeded = JSON.parse(spawnSync(py, ['-c', `import json
from hoja_server.db import make_engine, migrate, seed_tenant, organization
from hoja_server import runner_api
e = make_engine(${JSON.stringify(db)}); migrate(e)
i = seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada',
                user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=False)
with e.begin() as c:
    c.execute(organization.update().values(agents_enabled=True))
_r, t = runner_api.mint_enrollment_token(e, organization_id=i['org_id'], created_by=i['user_id'])
print(json.dumps({'team': i['team_id'], 'enrollment': t}))`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' }).stdout);

const port = 19950 + Math.floor(Math.random() * 40);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)],
  { cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: path.join(repo, 'frontend/dist'), PYTHONPATH: repo, HOJA_SECRET_KEY: 'pr' }, stdio: 'ignore' });
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const gql = async (query, variables) => {
  const r = await fetch(`${base}/graphql`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: JSON.stringify({ query, variables }) });
  const b = await r.json(); if (b.errors) throw new Error(b.errors[0].message); return b.data;
};
let pass = 0, fail = 0;
const ok = (c, label, d = '') => { c ? pass++ : fail++; console.log(`${c ? 'PASS' : 'FAIL'} ${label}${d ? ` [${d}]` : ''}`); };

(async () => {
  let browser;
  try {
    for (let i = 0; i < 100 && !(await fetch(`${base}/ready`).catch(() => null))?.ok; i++) await wait(300);
    const project = (await gql(`mutation($t: String!) { projectCreate(input: { name: "Website", teamIds: [$t] }) { project { id slugId url } } }`, { t: seeded.team })).projectCreate.project;
    browser = await chromium.launch();
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.addInitScript((t) => localStorage.setItem('hoja_token', t), token);
    await page.goto(`${base}/`);
    await page.locator('[data-nav-item]', { hasText: /^Projects$/ }).first().click();
    await page.getByText('Website', { exact: true }).first().click();
    const field = page.locator('[data-project-repository]');
    await field.waitFor({ timeout: 20000 });
    await field.getByText('None: agent tasks use a scratch folder').waitFor({ timeout: 10000 });
    ok(true, 'a project without a repository says what that means');
    await field.getByRole('button').first().click();
    await field.getByLabel('Project repository').fill('acme/website');
    await field.getByRole('button', { name: 'Save' }).click();
    await page.locator('[data-project-repository="acme/website"]').waitFor({ timeout: 10000 });
    ok(true, 'the repository is set from the project page');
    await page.screenshot({ path: path.join(shots, 'project-repository.png') });

    const agent = (await gql(`mutation { agentMemberCreate(input: { name: "Builder" }) { agentMember { id userId } } }`)).agentMemberCreate.agentMember;
    await gql(`mutation($id: String!, $t: String!) { agentPolicyUpdate(agentMemberId: $id, input: { teams: [$t] }) { success } }`, { id: agent.id, t: seeded.team });
    const task = (await gql(`mutation($i: IssueCreateInput!) { issueCreate(input: $i) { issue { identifier } } }`,
      { i: { teamId: seeded.team, title: 'Fix the header', projectId: project.id, delegateId: agent.userId } })).issueCreate.issue;
    const identity = await (await fetch(`${base}/runner/register`, { method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name: 'box', enrollment_token: seeded.enrollment, harnesses: ['pi', 'hoja-coder'] }) })).json();
    const lease = (await (await fetch(`${base}/runner/lease`, { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${identity.token}` },
      body: JSON.stringify({ runner_id: identity.runner_id, poll_timeout_ms: 0 }) })).json()).lease;
    ok(lease && lease.repo && lease.repo.repo === 'acme/website', 'the task in that project is leased with its repository', JSON.stringify(lease && lease.repo));
    ok(lease.repo.branch === `agent/${task.identifier.toLowerCase()}`, 'on the task\'s own branch', lease.repo.branch);
    const logs = (await gql(`query($id: String!) { agentRun(id: $id) { logs(source: "server") { kind message } } }`, { id: lease.lease_id })).agentRun.logs;
    ok(logs.some((l) => l.kind === 'lease.grant' && l.message.includes('from project Website')), 'the run log says why');
  } catch (e) {
    fail++; console.log(`FAIL ${e.message}`);
    try { const p = browser && browser.contexts()[0]?.pages()[0]; if (p) { console.log('URL', p.url()); await p.screenshot({ path: path.join(shots, 'project-repository-fail.png') }); } } catch { /* none */ }
  } finally {
    if (browser) await browser.close();
    server.kill();
    console.log(`\n${pass} passed, ${fail} failed. Screenshots: ${shots}`);
    process.exit(fail ? 1 : 0);
  }
})();
