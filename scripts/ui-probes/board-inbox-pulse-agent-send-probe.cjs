// Read-only: type into the agent composer, measure the enabled send button and placeholder-free state.
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
    await page.locator('textarea').first().click(); await page.keyboard.type('Find unassigned bugs'); await page.waitForTimeout(400);
    console.log('TYPED ' + JSON.stringify(await page.evaluate(() => {
      const b = document.querySelector('button[aria-label="Send"]'); if (!b) return null; const cs = getComputedStyle(b); const svg = b.querySelector('svg');
      return { disabled: b.disabled, cls: b.className.toString().slice(0, 120), btnColor: cs.color, btnBg: cs.backgroundColor, btnOpacity: cs.opacity, svgColor: svg && getComputedStyle(svg).color, svgStroke: svg && getComputedStyle(svg).stroke };
    })));
    await page.locator('textarea').first().fill(''); await page.waitForTimeout(200);
  } finally { await browser.close(); }
})().catch(e => { console.error('PROBE_ERROR', e.message); process.exit(1); });
