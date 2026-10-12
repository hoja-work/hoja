// Read-only counts and placeholder/send-button measurements for BOARD, INBOX, PULSE, AGENT.
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const rgb = s => s;
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(([t]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', 'dark'); }, [token]);
    await page.goto(url + '/'); await page.waitForTimeout(1200);
    const nav = async n => { await page.getByRole('button', { name: n, exact: true }).first().click(); await page.waitForTimeout(900); };
    await nav('Board');
    console.log('BOARD counts', JSON.stringify(await page.evaluate(() => ({ lanes: document.querySelectorAll('.kanban-column').length, cards: document.querySelectorAll('.kanban-card').length, empty: document.querySelectorAll('.kanban-empty').length, tabs: [...document.querySelectorAll('.board-page-tabs button')].map(b => b.innerText + (b.className ? '(' + b.className + ')' : '')) }))));
    const trig = page.locator('.status-picker-trigger').first();
    await trig.click(); await page.waitForTimeout(500);
    console.log('STATUS_MENU open=' + await page.locator('.status-menu').count() + ' options=' + await page.locator('.status-menu [role=option]').count());
    if (await page.locator('.status-menu [role=option]').count()) {
      const opt = await page.evaluate(() => { const o = document.querySelector('.status-menu [role=option]'); const cs = getComputedStyle(o); const m = document.querySelector('.status-menu'); const cm = getComputedStyle(m); return { optColor: cs.color, optBg: cs.backgroundColor, menuBg: cm.backgroundColor, menuColor: cm.color, sel: o.className }; });
      console.log('STATUS_MENU_STYLE ' + JSON.stringify(opt));
    }
    await page.keyboard.press('Escape');
    await nav('Inbox');
    console.log('INBOX counts', JSON.stringify(await page.evaluate(() => ({ rows: document.querySelectorAll('.inbox-row, .inbox-item, [class*=inbox] li').length, empty: [...document.querySelectorAll('p,div,span')].filter(e => /no |nothing|empty|all caught|inbox zero/i.test(e.innerText || '') && e.children.length === 0).map(e => e.className + '|' + e.innerText.slice(0, 60)).slice(0, 3), tabs: [...document.querySelectorAll('[role=tab], .inbox-tabs button, [class*=inbox] button')].map(b => (b.className || '') + ':' + b.innerText.trim().slice(0, 25)).slice(0, 12) }))));
    await nav('Pulse');
    console.log('PULSE counts', JSON.stringify(await page.evaluate(() => ({ posts: document.querySelectorAll('[class*=pulse-post], [class*=pulse-card], [class*=pulse] article').length, tabs: [...document.querySelectorAll('[class*=pulse] button, [role=tab]')].map(b => (b.className || '') + ':' + b.innerText.trim().slice(0, 25)).slice(0, 12), empty: [...document.querySelectorAll('[class*=pulse] p, [class*=pulse] div')].filter(e => e.children.length === 0 && /no |nothing|empty/i.test(e.innerText || '')).map(e => e.innerText.slice(0, 60)).slice(0, 3) }))));
    await nav('Agent');
    const agent = await page.evaluate(() => {
      const ta = document.querySelector('textarea, [contenteditable="true"]');
      if (!ta) return { composer: 'none' };
      const cs = getComputedStyle(ta);
      const ph = ta.getAttribute('placeholder') || ta.getAttribute('data-placeholder');
      const btns = [...document.querySelectorAll('.openui-agent-desktop-welcome-composer__action-bar button, [class*=composer] button')].map(b => ({ cls: (b.className || '').toString().slice(0, 70), disabled: b.disabled, aria: b.getAttribute('aria-label'), color: getComputedStyle(b).color, bg: getComputedStyle(b).backgroundColor, opacity: getComputedStyle(b).opacity }));
      return { composerTag: ta.tagName, placeholder: ph, color: cs.color, bg: cs.backgroundColor, btns };
    });
    console.log('AGENT ' + JSON.stringify(agent));
    const ph = await page.evaluate(() => {
      const ta = document.querySelector('textarea, [contenteditable="true"]');
      if (!ta) return null; const id = 'probe-ph'; ta.setAttribute('data-probe', id);
      const st = document.createElement('style'); st.id = 'probe-ph-style'; st.textContent = '[data-probe="' + id + '"]::placeholder{color:inherit}'; document.head.appendChild(st);
      return null;
    });
    console.log('AGENT_CHIPS ' + JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('.agent-issue-chip, [class*=starter], [class*=suggestion]')].map(e => ({ cls: e.className.toString().slice(0, 60), text: e.innerText.slice(0, 40), color: getComputedStyle(e).color, bg: getComputedStyle(e).backgroundColor })).slice(0, 8))));
  } finally { await browser.close(); }
})().catch(e => { console.error('PROBE_ERROR', e.message); process.exit(1); });
