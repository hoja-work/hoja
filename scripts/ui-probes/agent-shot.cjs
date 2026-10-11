const { chromium } = require('../../local-e2e/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  for (const [w, h, name] of [[1440, 900, 'desktop'], [390, 844, 'mobile']]) {
    const ctx = await browser.newContext({ viewport: { width: w, height: h } });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('http://127.0.0.1:8797/');
    await page.evaluate(() => localStorage.setItem('hoja_token', 'cliniar_probe_token_12345'));
    await page.goto('http://127.0.0.1:8797/workspace/agent');
    await page.waitForSelector('.openui-agent-container, .agent-chat-page', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `local-e2e/artifacts/agent-${name}.png` });
    const info = await page.evaluate(() => ({
      composerClasses: [...new Set([...document.querySelectorAll('[class*="composer"]')].map(e => e.className.baseVal ?? e.className))].slice(0, 12),
      placeholder: document.querySelector('textarea')?.placeholder,
      logoBroken: [...document.querySelectorAll('img')].filter(i => !i.complete || i.naturalWidth === 0).map(i => i.getAttribute('src')),
      scrollW: document.documentElement.scrollWidth,
    }));
    console.log(name, JSON.stringify(info));
    console.log(name, 'pageerrors:', errors.slice(0, 3));
    await ctx.close();
  }
  await browser.close();
})().catch(e => { console.error(e.message); process.exit(1); });
