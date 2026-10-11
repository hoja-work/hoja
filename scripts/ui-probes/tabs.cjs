const { chromium } = require('../../local-e2e/node_modules/playwright');
(async () => {
  const b = await chromium.launch({ headless: true });
  const p = await b.newPage({ viewport: { width: 320, height: 800 } });
  await p.addInitScript(() => localStorage.setItem('hoja_token', 'cliniar_probe_token_12345'));
  await p.goto('http://127.0.0.1:8797/');
  await p.waitForTimeout(1200);
  const r = await p.evaluate(() => {
    const row = document.querySelector('.my-issue-tabs');
    const chain = []; let e = row;
    for (let i = 0; i < 5 && e; i++, e = e.parentElement) { const s = getComputedStyle(e), b = e.getBoundingClientRect(); chain.push({ cls: (e.className?.toString?.() || e.tagName).slice(0, 60), w: Math.round(b.width), right: Math.round(b.right), overflowX: s.overflowX, display: s.display, gridCols: s.gridTemplateColumns.slice(0, 60) }); }
    return { chain, rowScroll: row.scrollWidth, rowClient: row.clientWidth, docScroll: document.documentElement.scrollWidth };
  });
  console.log(JSON.stringify(r, null, 1));
  await b.close();
})();
