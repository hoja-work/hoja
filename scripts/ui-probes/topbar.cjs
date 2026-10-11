const { chromium } = require('../../local-e2e/node_modules/playwright');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
(async () => {
  const b = await chromium.launch({ headless: true });
  const p = await b.newPage({ viewport: { width: 320, height: 800 } });
  await p.addInitScript(() => localStorage.setItem('hoja_token', 'cliniar_probe_token_12345'));
  await p.goto('http://127.0.0.1:8797/');
  await p.waitForTimeout(1200);
  const r = await p.evaluate(() => [...document.querySelectorAll('button,a,input,[role=button]')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).map(e => ({ tag: e.tagName, cls: e.className?.toString?.().slice(0,80), aria: e.getAttribute('aria-label'), text: e.textContent.trim().slice(0,40), parent: e.parentElement?.className?.toString?.().slice(0,80), right: Math.round(e.getBoundingClientRect().right) })));
  console.log(JSON.stringify(r, null, 1));
  await b.close();
})();
