// Read-only: Inbox/Pulse page DOM, Agent placeholder pseudo-element colors, existing agent threads.
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const sum = (fn) => fn;
(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.addInitScript(([t]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', 'dark'); }, [token]);
    await page.goto(url + '/'); await page.waitForTimeout(1200);
    const nav = async n => { await page.getByRole('button', { name: n, exact: true }).first().click(); await page.waitForTimeout(900); };
    const threads = await page.evaluate(async t => { const r = await fetch('/api/agent/threads', { headers: { authorization: 'Bearer ' + t }, credentials: 'include' }); return { status: r.status, body: (await r.text()).slice(0, 300) }; }, token);
    console.log('THREADS ' + JSON.stringify(threads));
    await nav('Inbox');
    console.log('INBOX_MAIN ' + JSON.stringify(await page.evaluate(() => { const m = document.querySelector('.main') || document.body; return { cls: m.className, text: m.innerText.slice(0, 300), buttons: [...m.querySelectorAll('button')].map(b => (b.className.toString().slice(0,40)) + ':' + (b.innerText || b.getAttribute('aria-label') || '').trim().slice(0, 25)).slice(0, 20) }; })));
    await nav('Pulse');
    console.log('PULSE_MAIN ' + JSON.stringify(await page.evaluate(() => { const m = document.querySelector('.main') || document.body; return { text: m.innerText.slice(0, 300), buttons: [...m.querySelectorAll('button')].map(b => (b.className.toString().slice(0,40)) + ':' + (b.innerText || b.getAttribute('aria-label') || '').trim().slice(0, 25)).slice(0, 20) }; })));
    await nav('Agent');
    console.log('AGENT_PLACEHOLDER ' + JSON.stringify(await page.evaluate(() => {
      const ta = document.querySelector('textarea'); if (!ta) return null;
      const ps = getComputedStyle(ta, '::placeholder');
      const cls = ta.className.toString();
      return { cls, placeholder: ta.placeholder, color: ps.color, opacity: ps.opacity, fontSize: ps.fontSize, bg: getComputedStyle(ta).backgroundColor, taColor: getComputedStyle(ta).color };
    })));
    console.log('AGENT_SEND ' + JSON.stringify(await page.evaluate(() => {
      const b = document.querySelector('button[aria-label="Send"]'); if (!b) return null;
      const svg = b.querySelector('svg'); const cs = getComputedStyle(b);
      return { disabled: b.disabled, cls: b.className.toString(), btnColor: cs.color, btnBg: cs.backgroundColor, btnOpacity: cs.opacity, svgColor: svg && getComputedStyle(svg).color, svgStroke: svg && getComputedStyle(svg).stroke, svgFill: svg && getComputedStyle(svg).fill, parentBg: getComputedStyle(b.parentElement).backgroundColor };
    })));
  } finally { await browser.close(); }
})().catch(e => { console.error('PROBE_ERROR', e.message); process.exit(1); });
