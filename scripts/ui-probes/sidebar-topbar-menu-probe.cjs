// Read-only: opens workspace menu via .sidebar-top .workspace-trigger; reports nav-item computed colors and low-contrast paths.
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(([t]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', 'dark'); }, [token]);
    await page.goto(url + '/');
    await page.waitForTimeout(1500);
    const navs = await page.evaluate(() => [...document.querySelectorAll('.sidebar .nav-item')].slice(0, 8).map(e => ({ text: e.innerText.trim().slice(0, 20), color: getComputedStyle(e).color, bg: getComputedStyle(e).backgroundColor, cls: e.className })));
    console.log('NAV_ITEMS ' + JSON.stringify(navs));
    const headers = await page.evaluate(() => [...document.querySelectorAll('.sidebar .nav-group > p, .sidebar .nav-heading')].map(e => ({ text: e.innerText.trim().slice(0, 20), color: getComputedStyle(e).color, bg: getComputedStyle(e).backgroundColor, cls: e.className })));
    console.log('GROUP_HEADERS ' + JSON.stringify(headers));
    const trig = page.locator('.sidebar-top .workspace-trigger').first();
    console.log('trigger-count=' + await trig.count());
    await trig.click({ timeout: 3000 }).catch(e => console.log('trigger-click-failed ' + e.message.split('\n')[0]));
    await page.waitForTimeout(500);
    const menu = await page.evaluate(() => { const m = document.querySelector('.sidebar-top .workspace-menu'); if (!m) return 'no .workspace-menu'; return [...m.querySelectorAll('*')].filter(e => [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())).map(e => ({ text: [...e.childNodes].filter(n => n.nodeType === 3).map(n => n.textContent.trim()).join(' ').slice(0, 24), color: getComputedStyle(e).color, bg: getComputedStyle(e).backgroundColor, cls: e.className.toString().slice(0, 40) })); });
    console.log('WORKSPACE_MENU ' + JSON.stringify(menu));
  } finally { await browser.close(); }
})().catch(e => { console.error(e.message); process.exit(1); });
