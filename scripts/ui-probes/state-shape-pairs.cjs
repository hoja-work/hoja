// Before/after crops for the HOJA-746 state-shape fixes. Run against two servers
// (e.g. main and the branch) seeded with the same tenant.
// Usage: PROBE_TOKEN=... node scripts/ui-probes/state-shape-pairs.cjs <outdir> before=http://… after=http://…
const { chromium } = require('../../local-e2e/node_modules/playwright');
const fs = require('node:fs');
const path = require('node:path');
const [outDir, ...targets] = process.argv.slice(2);
const token = process.env.PROBE_TOKEN;
const wait = ms => new Promise(r => setTimeout(r, ms));
const CASES = [
  { name: 'workspace-header-hover', route: '/', vp: [1280, 800], sel: '[data-nav-group-toggle]', state: 'hover', pad: [12, 8, 20, 8] },
  { name: 'sidebar-row-focus-inbox', route: '/', vp: [1280, 800], sel: '[data-workspace-sidebar] [data-nav-item]', state: 'focus', pad: [12, 10, 20, 10] },
  { name: 'team-subrow-focus', route: '/', vp: [1280, 800], sel: 'button:has-text("Cycles")', state: 'focus', pad: [40, 10, 20, 10] },
  { name: 'settings-row-focus', route: '/workspace/settings/preferences', vp: [1280, 800], sel: '[data-settings-sidebar] [data-nav-item]', state: 'focus', pad: [12, 10, 20, 10] },
  { name: 'my-issues-tab-focus', route: '/workspace/my-issues/assigned', vp: [1280, 800], sel: '[data-my-issue-tabs] button', state: 'focus', pad: [16, 12, 60, 12] },
  { name: 'leaderboard-row-focus', route: '/workspace/leaderboard', vp: [1280, 800], sel: '[data-leaderboard-people] button', state: 'focus', pad: [8, 8, 8, 8] },
  { name: 'phone-group-header-hover', route: '/', vp: [390, 844], sel: '[aria-label="Collapse Todo"]', state: 'hover', pad: [16, 16, 320, 24] },
  { name: 'phone-group-header-focus', route: '/', vp: [390, 844], sel: '[aria-label="Collapse Todo"]', state: 'focus', pad: [16, 16, 320, 24] },
  { name: 'phone-inbox-filter-focus', route: '/workspace/inbox', vp: [390, 844], sel: '[aria-label="Show unread only"]', state: 'focus', pad: [16, 12, 16, 12] },
  { name: 'phone-nav-trigger-focus', route: '/workspace/reviews', vp: [390, 844], sel: '[data-mobile-nav-toggle]', state: 'focus', pad: [8, 12, 60, 12] },
  { name: 'sidebar-full', route: '/', vp: [1280, 800], full: [0, 0, 250, 800] },
];
(async () => {
  const browser = await chromium.launch();
  fs.mkdirSync(outDir, { recursive: true });
  for (const t of targets) {
    const [label, base] = t.split('=');
    for (const c of CASES) {
      const ctx = await browser.newContext({ viewport: { width: c.vp[0], height: c.vp[1] }, deviceScaleFactor: 3, reducedMotion: 'reduce' });
      await ctx.addInitScript(tk => localStorage.setItem('hoja_token', tk), token);
      const page = await ctx.newPage();
      await page.goto(base + c.route);
      await wait(1200);
      let clip;
      if (c.full) clip = { x: c.full[0], y: c.full[1], width: c.full[2], height: c.full[3] };
      else {
        const el = page.locator(c.sel).first();
        const b = await el.evaluate(e => { const r = e.getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; }).catch(() => null);
        if (!b) { console.log(`skip ${label} ${c.name}: not found`); await ctx.close(); continue; }
        if (c.state === 'hover') await page.mouse.move(b.x + b.w / 2, b.y + b.h / 2);
        else { await page.keyboard.press('Shift'); await el.evaluate(e => e.focus({ preventScroll: true })); }
        await wait(80);
        const [l, tp, r, bt] = c.pad;
        const x = Math.max(0, b.x - l), y = Math.max(0, b.y - tp);
        clip = { x, y, width: Math.min(b.w + l + r, c.vp[0] - x), height: Math.min(b.h + tp + bt, c.vp[1] - y) };
      }
      await page.screenshot({ path: path.join(outDir, `${c.name}.${label}.png`), clip });
      await ctx.close();
    }
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
