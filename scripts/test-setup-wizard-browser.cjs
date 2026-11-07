// Real-browser journeys for the setup wizard.
//
// Boots the real ASGI app on a disposable SQLite database and the real Vite dev
// server (with its proxy), then drives Chromium. Nothing is mocked except the one
// response we deliberately lose, to prove the wizard does not create twice.
//
//   node scripts/test-setup-wizard-browser.cjs
//   SETUP_APP_ROOT=/path/to/app node scripts/test-setup-wizard-browser.cjs
//
// The Cloud edition runs the same file with SETUP_APP_ROOT pointing at its app/.
const {spawn, spawnSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = process.env.SETUP_APP_ROOT
  ? path.resolve(process.env.SETUP_APP_ROOT)
  : path.resolve(__dirname, '..');
const frontend = path.join(repo, 'frontend');
const localPython = path.join(repo, '.venv', 'bin', 'python');
const python = process.env.HOJA_PYTHON
  || (fs.existsSync(localPython) ? localPython : 'python3');
function loadPlaywright() {
  const candidates = [
    process.env.PLAYWRIGHT_MODULE,
    'playwright',
    path.join(repo, 'local-e2e', 'node_modules', 'playwright'),
  ].filter(Boolean);
  for (const candidate of candidates) {
    try { return require(candidate) } catch { /* try the next location */ }
  }
  throw new Error('Playwright not found. Set PLAYWRIGHT_MODULE to its package directory.');
}
const playwright = loadPlaywright();

const net = require('node:net');
const children = [];
const temporary = [];

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const {port} = server.address();
      server.close(() => resolve(port));
    });
  });
}

function track(child, name) {
  children.push(child);
  child.stderr.on('data', (chunk) => {
    if (process.env.SETUP_DEBUG) process.stderr.write(`[${name}] ${chunk}`);
  });
  child.on('exit', (code) => {
    if (process.env.SETUP_DEBUG) process.stderr.write(`[${name}] exited ${code}\n`);
  });
  return child;
}

async function waitFor(url, what) {
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error(`${what} did not start within 30s`);
}

async function startStack(label) {
  const db = path.join(os.tmpdir(), `hoja-setup-${label}-${process.pid}.db`);
  temporary.push(db);
  const backendPort = await freePort();
  const appPort = await freePort();
  const backendUrl = `http://127.0.0.1:${backendPort}`;
  const appUrl = `http://127.0.0.1:${appPort}`;
  const migrated = spawnSync(python, ['-m', 'hoja_server.cli', 'migrate', '--db', db], {cwd: repo, encoding: 'utf8'});
  if (migrated.status !== 0) throw new Error(`migrate failed: ${migrated.stderr || migrated.stdout}`);
  const backend = track(spawn(python, ['-m', 'hoja_server.cli', 'serve', '--db', db, '--port', String(backendPort), '--host', '127.0.0.1', '--log-level', 'warning'], {
    cwd: repo,
    stdio: ['ignore', 'ignore', 'pipe'],
  }), 'backend');
  await waitFor(`${backendUrl}/ready`, 'backend');

  const viteBin = path.join(frontend, 'node_modules', 'vite', 'bin', 'vite.js');
  const vite = track(spawn(process.execPath, [viteBin, '--host', '127.0.0.1'], {
    cwd: frontend,
    env: {...process.env, HOJA_DEV_BACKEND: backendUrl, HOJA_DEV_PORT: String(appPort)},
    stdio: ['ignore', 'ignore', 'pipe'],
  }), 'vite');
  await waitFor(appUrl, 'vite');
  return {backend, vite, db, backendUrl, appUrl};
}

async function stop(child) {
  if (!child || child.killed) return;
  child.kill('SIGTERM');
  await new Promise((resolve) => {
    const timer = setTimeout(() => { child.kill('SIGKILL'); resolve() }, 4000);
    child.on('exit', () => { clearTimeout(timer); resolve() });
  });
}

async function loseNextMutation(page, marker) {
  let lost = false;
  const handler = async (route) => {
    const request = route.request();
    const body = (() => { try { return JSON.parse(request.postData() || '{}') } catch { return {} } })();
    if (!lost && String(body.query || '').includes(marker)) {
      lost = true;
      await route.fetch();       // the server commits
      await route.abort('failed'); // the page never sees it
      return;
    }
    await route.continue();
  };
  await page.route('**/graphql', handler);
  return () => page.unroute('**/graphql', handler);
}

const results = [];
async function step(name, fn) {
  try {
    await fn();
    results.push({name, ok: true});
    console.log(`ok - ${name}`);
  } catch (error) {
    results.push({name, ok: false, error});
    console.error(`not ok - ${name}`);
    console.error(`  ${error.message}`);
  }
}

async function main() {
  const stackA = await startStack('a');
  const browser = await playwright.chromium.launch();
  try {
    const context = await browser.newContext({viewport: {width: 1440, height: 900}});
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', (error) => pageErrors.push(String(error)));

    const heading = () => page.locator('.setup-form-panel h1');
    let inviteLink = '';
    const waitHeading = async (text) => {
      await heading().filter({hasText: text}).waitFor({timeout: 15000});
      assert.match(await heading().innerText(), new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    };

    await step('a fresh instance opens the wizard, not the sign-in page', async () => {
      await page.goto(stackA.appUrl);
      await waitHeading('Let’s make this yours.');
      assert.match((await page.locator('.setup-progress-label').innerText()).toLowerCase(), /^step 1 of 6$/);
      assert.equal(await page.locator('#setup-workspace-name').count(), 0);
    });

    await step('the account step validates before any request and moves focus', async () => {
      await page.fill('#setup-name', 'Alex Morgan');
      await page.fill('#setup-email', 'alex@example.com');
      await page.fill('#setup-password', 'short');
      await page.getByRole('button', {name: 'Continue'}).click();
      await page.locator('#setup-password-error').waitFor();
      assert.equal(await page.inputValue('#setup-password'), 'short', 'the field is kept');

      await page.fill('#setup-password', 'a-sufficiently-long-password');
      await page.getByRole('button', {name: 'Continue'}).click();
      await waitHeading('Give your workspace a home.');
      assert.equal(await page.evaluate(() => document.activeElement?.tagName), 'H1', 'focus moves to the step heading');
    });

    await step('a derived address follows the name until the address is edited', async () => {
      await page.fill('#setup-workspace-name', 'Acme Studio');
      assert.equal(await page.inputValue('#setup-workspace-address'), 'acme-studio');
      await page.fill('#setup-workspace-address', 'acme-eu');
      await page.fill('#setup-workspace-name', 'Acme Studio EU');
      assert.equal(await page.inputValue('#setup-workspace-address'), 'acme-eu', 'manual address survives a rename');
      assert.match(await page.locator('.setup-preview-address').innerText(), /acme-eu/);
    });

    await step('creating the workspace is one write and shows real ids afterwards', async () => {
      await page.getByRole('button', {name: 'Continue'}).click();
      await waitHeading('Create your workspace and first team.');
      await page.getByRole('button', {name: 'Create workspace'}).click();
      await waitHeading('Start with a project?');
      // Revisit the finished steps: they are read-only summaries, not editable forms.
      await page.getByRole('button', {name: 'Back'}).click();
      assert.ok((await heading().innerText()).includes('Add your first team'));
      assert.equal(await page.locator('#setup-team-name').count(), 0);
      await page.getByRole('button', {name: 'Back'}).click();
      assert.equal(await page.locator('#setup-workspace-name').count(), 0);
      await page.getByRole('button', {name: 'Continue'}).click();
      await page.getByRole('button', {name: 'Continue'}).click();
      await waitHeading('Start with a project?');
    });

    await step('a lost project response is reconciled by name, not re-created', async () => {
      await page.fill('#setup-project-name', 'Launch');
      const restore = await loseNextMutation(page, 'projectCreate');
      await page.getByRole('button', {name: 'Create project'}).click();
      await page.locator('.setup-error').waitFor();
      assert.match(await page.locator('.setup-error').innerText(), /did not finish/i);
      await restore();
      await page.getByRole('button', {name: 'Create project'}).click();
      await waitHeading('Bring your teammates in.');
      // Going back shows the server-confirmed project instead of an editable form.
      await page.getByRole('button', {name: 'Back'}).click();
      await page.locator('.setup-created').filter({hasText: 'Launch'}).waitFor();
      assert.equal(await page.locator('#setup-project-name').count(), 0);
      await page.getByRole('button', {name: 'Continue'}).click();
      await waitHeading('Bring your teammates in.');
      await page.evaluate(async () => {
        const response = await fetch('/graphql', {method: 'POST', credentials: 'include', headers: {'content-type': 'application/json'}, body: JSON.stringify({query: '{ projects(first:50){nodes{name}} }'})});
        window.__projects = (await response.json()).data.projects.nodes;
      });
      assert.deepEqual(await page.evaluate(() => window.__projects), [{name: 'Launch'}], 'back navigation did not create a second project');
    });

    await step('a member survives a failed invite response and is not duplicated on retry', async () => {
      await page.fill('#setup-invite-name', 'Sam Rivera');
      await page.fill('#setup-invite-email', 'sam@example.com');
      const restore = await loseNextMutation(page, 'membershipInvitationCreate');
      await page.getByRole('button', {name: 'Add invitation'}).click();
      await page.locator('.setup-error').waitFor();
      await restore();
      await page.getByRole('button', {name: 'Add invitation'}).click();
      await page.locator('.setup-result-head').filter({hasText: 'Invite link ready'}).waitFor();
      inviteLink = await page.locator('input[aria-label="Invite link"]').inputValue();
      assert.match(inviteLink, /#invite=/);
      assert.match(await page.locator('.setup-hint').first().innerText(), /share this single-use link/i);
    });

    await step('reloading mid-flow resumes the same step instead of starting over', async () => {
      await page.reload();
      await waitHeading('Bring your teammates in.');
      await page.locator('.setup-created').filter({hasText: 'pending'}).waitFor();
      assert.equal(await page.locator('#setup-workspace-name').count(), 0);
    });

    await step('reduced motion removes the step animation', async () => {
      await page.emulateMedia({reducedMotion: 'reduce'});
      await page.getByRole('button', {name: 'Back'}).click();
      await waitHeading('Start with a project?');
      assert.equal(await page.locator('.setup-step').evaluate((node) => getComputedStyle(node).animationName), 'none');
      await page.emulateMedia({reducedMotion: 'no-preference'});
    });

    await step('the step transition is short and does not shift layout', async () => {
      const measured = await page.evaluate(async () => {
        const shell = document.querySelector('.setup-shell');
        const before = shell.getBoundingClientRect().top;
        let shift = 0;
        const observer = new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) if (!entry.hadRecentInput) shift += entry.value;
        });
        observer.observe({type: 'layout-shift', buffered: true});
        const primary = [...document.querySelectorAll('.setup-step button')]
          .filter((button) => button.textContent?.includes('Continue')).pop();
        primary?.click();
        await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const fresh = document.querySelector('.setup-step');
        const duration = fresh ? getComputedStyle(fresh).animationDuration : '0s';
        await new Promise((resolve) => setTimeout(resolve, 300));
        observer.disconnect();
        const after = shell.getBoundingClientRect().top;
        return {shift, duration, topDelta: Math.abs(after - before)};
      });
      assert.ok(parseFloat(measured.duration) <= 0.2, `animation too long: ${measured.duration}`);
      assert.ok(measured.shift < 0.02, `layout shifted: ${measured.shift}`);
      assert.ok(measured.topDelta < 4, `panel jumped: ${measured.topDelta}px`);
    });

    await step('dark theme and a narrow viewport keep the layout intact', async () => {
      await page.evaluate(() => { document.documentElement.dataset.theme = 'dark' });
      const dark = await page.evaluate(() => getComputedStyle(document.querySelector('.setup-page')).backgroundColor);
      assert.equal(dark, 'rgb(23, 23, 25)');
      const darkShot = path.join('/tmp', 'setup-dark.png');
      await page.screenshot({path: darkShot, fullPage: true});
      fs.accessSync(darkShot);
      await page.evaluate(() => { document.documentElement.dataset.theme = 'light' });

      await page.setViewportSize({width: 390, height: 844});
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      assert.ok(overflow <= 1, `horizontal overflow: ${overflow}px`);
      await page.screenshot({path: path.join('/tmp', 'setup-mobile.png'), fullPage: true});

      await page.setViewportSize({width: 1280, height: 600});
      await page.locator('.setup-form-panel .primary').first().scrollIntoViewIfNeeded();
      const box = await page.locator('.setup-form-panel .primary').first().boundingBox();
      assert.ok(box && box.y >= 0 && box.y + box.height <= 600, 'the primary action fits a short viewport');
      await page.setViewportSize({width: 1440, height: 900});
    });

    await step('the ready step summarises only what exists, then opens the workspace', async () => {
      for (let attempt = 0; attempt < 5; attempt += 1) {
        const title = await heading().innerText();
        if (title.includes('all set')) break;
        if (title.includes('teammates')) {
          await page.getByRole('button', {name: 'Skip this step'}).click();
        } else {
          await page.locator('.setup-step button.primary').last().click();
        }
        await page.waitForTimeout(250);
      }
      await waitHeading('You’re all set.');
      const summary = await page.locator('.setup-summary li').allInnerTexts();
      assert.ok(summary.some((row) => row.includes('acme-eu')));
      assert.ok(summary.some((row) => row.includes('Launch')));
      assert.ok(summary.some((row) => row.includes('sent') || row.includes('pending')));
      assert.ok(!summary.some((row) => row.includes('Connected')), 'no connector was invented');
      await page.getByRole('button', {name: 'Open my workspace'}).click();
      await page.waitForURL(/\/workspace\/my-issues\/assigned/, {timeout: 15000});
      const draft = await page.evaluate(() => localStorage.getItem('hoja_setup_draft_v1:oss') || localStorage.getItem('hoja_setup_draft_v1:cloud'));
      assert.equal(draft, null, 'the finished draft is cleared');
    });

    await step('the persisted resources and authorization match what the summary claimed', async () => {
      const data = await page.evaluate(async () => {
        const query = async (q) => (await (await fetch('/graphql', {method: 'POST', credentials: 'include', headers: {'content-type': 'application/json'}, body: JSON.stringify({query: q})})).json()).data;
        return {
          viewer: await query('{ viewer { email admin } }'),
          teams: await query('{ teams(first:50){nodes{id key name}} }'),
          projects: await query('{ projects(first:50){nodes{id name}} }'),
          members: await query('{ organizationMembers(first:50){nodes{id email}} }'),
          pending: await query('{ pendingMembershipInvitations{userId email} }'),
        };
      });
      assert.equal(data.viewer.viewer.email, 'alex@example.com');
      assert.equal(data.viewer.viewer.admin, true);
      assert.deepEqual(data.teams.teams.nodes.map((team) => team.key), ['ENG']);
      assert.deepEqual(data.projects.projects.nodes.map((project) => project.name), ['Launch']);
      assert.equal(data.members.organizationMembers.nodes.filter((m) => m.email === 'sam@example.com').length, 1);
      assert.equal(data.pending.pendingMembershipInvitations.length, 1);
    });

    await step('an invited teammate can accept the single-use link', async () => {
      const invitee = await browser.newContext({viewport: {width: 1000, height: 800}});
      const page2 = await invitee.newPage();
      await page2.goto(inviteLink);
      await page2.getByRole('heading', {name: 'Join your team.'}).waitFor();
      await page2.fill('input[type=email]', 'sam@example.com');
      await page2.fill('input[type=password]', 'sam-password-long-enough');
      await page2.getByRole('button', {name: 'Accept invitation →'}).click();
      try {
        await page2.locator('.onboarding-card h1').filter({hasText: /You’re in\.|Your invite is ready\./}).waitFor({timeout: 8000});
      } catch (error) {
        console.error('  invite page body:', (await page2.locator('body').innerText()).slice(0, 500));
        throw error;
      }
      await page2.getByRole('button', {name: /Open my workspace/}).click();
      // Accepting lands in the workspace; the route is normalised in place, so
      // assert the authenticated app rather than one exact URL.
      await page2.locator('.app').waitFor({timeout: 15000});
      assert.equal(await page2.locator('.setup-page').count(), 0, 'the invitee is not pushed into owner setup');
      assert.match(await page2.locator('body').innerText(), /My issues/);
      const identity = await page2.evaluate(async () => {
        const response = await fetch('/graphql', {method: 'POST', credentials: 'include', headers: {'content-type': 'application/json'}, body: JSON.stringify({query: '{ viewer { email } }'})});
        return (await response.json()).data.viewer;
      });
      assert.equal(identity.email, 'sam@example.com');
      await invitee.close();
    });

    await step('a returning member signs in without being pushed into setup', async () => {
      await page.locator('.logout-button').click();
      await page.locator('.login-card').waitFor();
      await page.fill('#email', 'alex@example.com');
      await page.fill('#password', 'a-sufficiently-long-password');
      await page.locator('.login-card button.primary').click();
      await page.waitForURL(/\/workspace\/my-issues\/assigned/, {timeout: 15000});
      assert.equal(await page.locator('.setup-form-panel h1').count(), 0);
    });

    await step('another workspace reuses the steps but not the account step', async () => {
      await page.getByRole('button', {name: 'Switch workspace'}).click();
      await page.getByRole('menuitem', {name: 'Create workspace'}).click();
      await page.getByLabel('Workspace name').fill('Second Studio');
      await page.getByLabel('Workspace key').fill('second-studio');
      await page.getByRole('button', {name: 'Create workspace'}).click();
      await waitHeading('Add your first team.');
      assert.equal(await page.locator('#setup-name').count(), 0, 'the account step is not asked again');
      assert.equal(await page.locator('#setup-workspace-name').count(), 0, 'the workspace already exists');
      await page.getByRole('button', {name: 'Create team'}).click();
      await waitHeading('Start with a project?');
      await page.getByRole('button', {name: 'Skip this step'}).click();
      await waitHeading('Bring your teammates in.');
      await page.getByRole('button', {name: 'Skip this step'}).click();
      await waitHeading('You’re all set.');
      await page.getByRole('button', {name: 'Open my workspace'}).click();
      await page.waitForURL(/\/workspace\/my-issues\/assigned/, {timeout: 15000});
      const second = await page.evaluate(async () => {
        const response = await fetch('/graphql', {method: 'POST', credentials: 'include', headers: {'content-type': 'application/json'}, body: JSON.stringify({query: '{ workspace { urlKey } teams(first:10){nodes{key}} }'})});
        return (await response.json()).data;
      });
      assert.equal(second.workspace.urlKey, 'second-studio');
      assert.deepEqual(second.teams.nodes.map((team) => team.key), ['ENG']);
    });

    await step('the wizard throws no uncaught error on the happy path', async () => {
      // Network 401s from pre-session probes are expected resource logs, not
      // JavaScript failures; an uncaught exception is.
      assert.deepEqual(pageErrors, []);
    });

    await context.close();

    // ---- lost response: the server committed, the browser did not hear back ----
    await stop(stackA.backend);
    await stop(stackA.vite);

    const stackB = await startStack('b');
    try {
      const contextB = await browser.newContext({viewport: {width: 1100, height: 800}});
      const pageB = await contextB.newPage();
      let dropped = 0;
      await pageB.route('**/onboarding/bootstrap', async (route) => {
        if (dropped > 0) return route.continue();
        dropped += 1;
        await route.fetch();      // the real server commits
        await route.abort('failed'); // the browser never sees the response
      });
      await pageB.goto(stackB.appUrl);
      await pageB.locator('.setup-form-panel h1').filter({hasText: 'Let’s make this yours.'}).waitFor();
      await pageB.fill('#setup-name', 'Robin Lee');
      await pageB.fill('#setup-email', 'robin@example.com');
      await pageB.fill('#setup-password', 'another-long-password');
      await pageB.getByRole('button', {name: 'Continue'}).click();
      await pageB.fill('#setup-workspace-name', 'Beta Works');
      await pageB.getByRole('button', {name: 'Continue'}).click();
      await pageB.getByRole('button', {name: 'Create workspace'}).click();

      await step('a lost create response is reported, not retried, and the account still works', async () => {
        await pageB.locator('.setup-error').waitFor({timeout: 15000});
        assert.match(await pageB.locator('.setup-error').innerText(), /already set up/i);
        await pageB.getByRole('button', {name: 'Reload Hoja'}).waitFor();
        const status = await (await pageB.request.get(`${stackB.backendUrl}/onboarding/status`)).json();
        assert.equal(status.initialized, true);
        const login = await pageB.request.post(`${stackB.backendUrl}/auth/login`, {data: {email: 'robin@example.com', password: 'another-long-password'}});
        assert.equal(login.status(), 200, 'the workspace and account exist exactly once');
      });

      await step('reloading after the lost response does not create a second workspace', async () => {
        await pageB.getByRole('button', {name: 'Reload Hoja'}).click();
        await pageB.waitForTimeout(1500);
        const status = await (await pageB.request.get(`${stackB.backendUrl}/onboarding/status`)).json();
        assert.equal(status.initialized, true);
        const second = await pageB.request.post(`${stackB.backendUrl}/onboarding/bootstrap`, {data: {
          name: 'Robin Lee', email: 'robin@example.com', password: 'another-long-password',
          workspaceName: 'Beta Works', workspaceKey: 'beta-works', teamName: 'Engineering', teamKey: 'ENG',
        }});
        assert.equal(second.status(), 409);
      });

      await contextB.close();
    } finally {
      await stop(stackB.backend);
      await stop(stackB.vite);
    }
  } finally {
    await browser.close();
    for (const child of children) await stop(child);
    for (const file of temporary) {
      for (const suffix of ['', '-wal', '-shm']) {
        try { fs.unlinkSync(file + suffix) } catch { /* already gone */ }
      }
    }
  }

  const failed = results.filter((result) => !result.ok);
  console.log(`\n${results.length - failed.length}/${results.length} browser checks passed (${repo})`);
  if (failed.length) process.exit(1);
}

main().catch((error) => {
  console.error(error);
  for (const child of children) child.kill('SIGKILL');
  process.exit(1);
});
