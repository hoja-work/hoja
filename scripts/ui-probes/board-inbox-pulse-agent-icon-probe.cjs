// Read-only: starter-card icon children and their computed colors on the agent welcome screen.
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(([t]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', 'dark'); }, [token]);
    await page.goto(url + '/'); await page.waitForTimeout(1200);
    await page.getByRole('button', { name: 'Agent', exact: true }).first().click(); await page.waitForTimeout(900);
    console.log('ICONS ' + JSON.stringify(await page.evaluate(() => [...document.querySelectorAll('.openui-agent-conversation-starter-item-long__icon')].slice(0, 2).map(e => {
      const cs = getComputedStyle(e); const kids = [...e.children].map(k => ({ tag: k.tagName, stroke: getComputedStyle(k).stroke, fill: getComputedStyle(k).fill, color: getComputedStyle(k).color, w: k.getBoundingClientRect().width }));
      return { cls: e.className.toString(), color: cs.color, bg: cs.backgroundColor, mask: cs.maskImage || cs.webkitMaskImage || '', bgImg: cs.backgroundImage.slice(0, 60), w: e.getBoundingClientRect().width, kids };
    }))));
  } finally { await browser.close(); }
})().catch(e => { console.error('PROBE_ERROR', e.message); process.exit(1); });
