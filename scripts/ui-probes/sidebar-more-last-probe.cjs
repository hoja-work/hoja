// Read-only: asserts the sidebar "Workspace More" control is the LAST nav child and that its
// panel opens upward, fully inside the viewport, in light and dark at 1440x900 and at 390x844.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/sidebar-more-last-probe.cjs
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'http://127.0.0.1:8797';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
let failures = 0;
const ok = (label, cond, detail) => {
  console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail === undefined ? '' : ' ' + JSON.stringify(detail)}`);
  if (!cond) failures += 1;
};

async function inspect(page, label) {
  const nav = await page.evaluate(() => {
    const sidebar = document.querySelector('#workspace-sidebar');
    const nav = sidebar && sidebar.querySelector('nav[aria-label="Workspace navigation"]');
    if (!nav) return { error: 'no sidebar nav' };
    const more = nav.querySelector('.nav-group-more-control');
    const trigger = more && more.querySelector('button[aria-label="Workspace More"]');
    return {
      navChildren: [...nav.children].map(el => el.className),
      lastChildClass: nav.lastElementChild ? nav.lastElementChild.className : null,
      moreIndex: more ? [...nav.children].indexOf(more) : -1,
      trigger: trigger ? {
        expanded: trigger.getAttribute('aria-expanded'),
        haspopup: trigger.getAttribute('aria-haspopup'),
        controls: trigger.getAttribute('aria-controls'),
        text: trigger.innerText.trim(),
        rect: (({ top, bottom, left, right }) => ({ top, bottom, left, right }))(trigger.getBoundingClientRect()),
      } : null,
      bottomRect: sidebar.querySelector('.sidebar-bottom') ? (({ top, bottom }) => ({ top, bottom }))(sidebar.querySelector('.sidebar-bottom').getBoundingClientRect()) : null,
    };
  });
  ok(`${label}: sidebar nav found`, !nav.error, nav.error);
  if (nav.error) return;
  ok(`${label}: More control is last nav child`, nav.moreIndex === nav.navChildren.length - 1, { moreIndex: nav.moreIndex, childCount: nav.navChildren.length, last: nav.lastChildClass });
  ok(`${label}: wrapped as nav-group nav-group-more-control`, /nav-group nav-group-more-control/.test(nav.lastChildClass || ''), nav.lastChildClass);
  ok(`${label}: trigger semantics intact`, !!nav.trigger && nav.trigger.haspopup === 'menu' && nav.trigger.controls === 'workspace-more-menu' && nav.trigger.text === 'More', nav.trigger);
  await page.locator('.sidebar button[aria-label="Workspace More"]').first().click({ timeout: 3000 });
  await page.waitForTimeout(300);
  const panel = await page.evaluate(() => {
    const p = document.getElementById('workspace-more-menu');
    if (!p) return { error: 'no #workspace-more-menu' };
    const r = p.getBoundingClientRect();
    const trigger = document.querySelector('.sidebar button[aria-label="Workspace More"]').getBoundingClientRect();
    const items = [...p.querySelectorAll('[role="menuitem"]')].map(el => el.innerText.trim());
    return {
      rect: { top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height },
      role: p.getAttribute('role'),
      items,
      triggerTop: trigger.top,
      triggerBottom: trigger.bottom,
      viewport: { w: innerWidth, h: innerHeight },
      inViewport: r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth,
      opensUpward: r.bottom <= trigger.top + 1,
      wouldOverflowDownward: trigger.bottom + 6 + r.height > innerHeight,
    };
  });
  ok(`${label}: panel present with role=menu`, !panel.error && panel.role === 'menu' && panel.items.includes('Members'), { role: panel.role, items: panel.items });
  if (panel.error) return;
  ok(`${label}: panel fully inside viewport`, panel.inViewport, { rect: panel.rect, viewport: panel.viewport });
  ok(`${label}: panel opens upward above the trigger`, panel.opensUpward, { panelBottom: panel.rect.bottom, triggerTop: panel.triggerTop });
  ok(`${label}: downward placement would have overflowed (rule justified)`, panel.wouldOverflowDownward, { triggerBottom: panel.triggerBottom, panelHeight: panel.rect.height, viewportH: panel.viewport.h });
  await page.keyboard.press('Escape');
  await page.waitForTimeout(150);
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_PATH || '/root/.cache/ms-playwright/chromium-1254/chrome-linux64/chrome' });
  try {
    for (const theme of ['light', 'dark']) {
      const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
      await page.addInitScript(([t, th]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', th); }, [token, theme]);
      await page.goto(url + '/');
      await page.waitForTimeout(1500);
      await inspect(page, `desktop-1440x900-${theme}`);
      await page.close();
      const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
      await mobile.addInitScript(([t, th]) => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', th); }, [token, theme]);
      await mobile.goto(url + '/');
      await mobile.waitForTimeout(1500);
      const toggle = mobile.locator('.topbar .mobile-nav-toggle').first();
      if (await toggle.count()) { await toggle.click({ timeout: 3000 }); await mobile.waitForTimeout(400); }
      await inspect(mobile, `mobile-390x844-${theme}`);
      await mobile.close();
    }
  } finally { await browser.close(); }
  console.log(failures === 0 ? 'RESULT: PASS' : `RESULT: FAIL (${failures} failures)`);
  process.exit(failures === 0 ? 0 : 1);
})().catch(e => { console.error(e); process.exit(1); });
