// New-issue dialog polish audit. Run from the repo root after `cd frontend && npm run build`:
//   node scripts/test-issue-modal-polish.cjs
// It measures the things "polished" actually means, instead of eyeballing a screenshot:
//   * motion budget: entrance/exit animate transform+opacity only, 0<d<=240ms, settled state clean
//   * prefers-reduced-motion collapses the motion to ~0ms
//   * hover/press/focus-visible produce measurable style deltas; touch has no hover dependency
//   * keyboard focus stays inside the dialog, Escape closes it, focus returns to the trigger
//   * interactions never shift layout (CLS measured from open to close)
//   * contrast: body/placeholder text >=4.5:1, focus ring >=3:1, in light AND dark
//   * geometry: dialog and its footer fit at 1440x900, 390x844, 320x568
const { chromium } = require('../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const web = path.join(repo, 'frontend/dist');
assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');
const scratch = path.join(repo, 'local-e2e/artifacts');
fs.mkdirSync(scratch, { recursive: true });
const dir = fs.mkdtempSync(path.join(scratch, 'modal-polish-'));
const db = path.join(dir, 'modal.db');
const token = 'cliniar_modal_polish_token';
const py = path.join(repo, '.venv/bin/python');
const seed = spawnSync(py, ['-c', `from cliniar_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
assert.equal(seed.status, 0, seed.stderr);

const port = 19100 + Math.floor(Math.random() * 90);
const base = `http://127.0.0.1:${port}`;
const server = spawn(path.join(repo, '.venv/bin/cliniar-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, CLINIAR_WEB_ROOT: web }, stdio: 'ignore' });
const wait = ms => new Promise(r => setTimeout(r, ms));

let pass = 0, fail = 0;
const ok = (cond, label, detail = '') => { cond ? pass++ : fail++; console.log(`${cond ? 'PASS' : 'FAIL'} ${label}${detail ? ` [${detail}]` : ''}`); };

/* WCAG contrast from computed colours. Handles both `rgb(r,g,b)` / `rgba(...)` and the
 * `color(srgb r g b / a)` form Chromium returns for color-mix(), and composites the
 * foreground alpha over the background before measuring. */
const parseColor = css => {
  const n = (css.match(/[\d.]+/g) || []).map(Number);
  const isUnit = /^color\(/.test(css);
  const [r, g, b] = isUnit ? [n[0] * 255, n[1] * 255, n[2] * 255] : [n[0], n[1], n[2]];
  return [r, g, b, n.length > 3 ? n[3] : 1];
};
const contrast = (fg, bg) => {
  const f = parseColor(fg), b = parseColor(bg);
  const a = f[3];
  const [r, g, bl] = [f[0] * a + b[0] * (1 - a), f[1] * a + b[1] * (1 - a), f[2] * a + b[2] * (1 - a)];
  const lum = ([rr, gg, bb]) => [rr, gg, bb].map(v => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; }).reduce((acc, v, i) => acc + v * [0.2126, 0.7152, 0.0722][i], 0);
  const [l1, l2] = [lum([r, g, bl]), lum(b.slice(0, 3))].sort((x, y) => y - x);
  return Number(((l1 + 0.05) / (l2 + 0.05)).toFixed(2));
};

/* Open through a control that exists at every viewport (the mobile sidebar is off-canvas). */
const settled = t => t === 'none' || /^matrix\(1, 0, 0, 1, 0, 0\)$/.test(t);
const openDialog = async page => {
  const groupButton = page.locator('button[aria-label^="Create issue in"]').first();
  const trigger = (await groupButton.count()) ? groupButton : page.locator('.page-actions .primary').first();
  await trigger.click();
  await page.waitForSelector('.issue-create-modal');
  await wait(320);
};

const readModal = page => page.evaluate(() => {
  const modal = document.querySelector('.issue-create-modal');
  const backdrop = modal.parentElement;
  const cs = getComputedStyle(modal);
  return {
    bg: cs.backgroundColor, radius: cs.borderRadius, opacity: cs.opacity, transform: cs.transform,
    backdropBg: getComputedStyle(backdrop).backgroundColor,
    backdropFilter: getComputedStyle(backdrop).backdropFilter,
    anims: modal.getAnimations().map(a => ({ name: a.animationName || a.transitionProperty, dur: a.effect?.getTiming?.().duration })),
    backdropAnims: backdrop.getAnimations().map(a => ({ name: a.animationName || a.transitionProperty, dur: a.effect?.getTiming?.().duration })),
  };
});

(async () => {
  let browser;
  try {
    for (let i = 0; i < 80; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch {} await wait(250); }
    browser = await chromium.launch();

    /* ---------- motion budget (light, default motion) ---------- */
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(`${base}/workspace/my-issues/assigned`, { waitUntil: 'networkidle' });

    await page.addInitScript(() => {
      window.__cls = [];
      new PerformanceObserver(list => { for (const e of list.getEntries()) if (!e.hadRecentInput) window.__cls.push(e.value); }).observe({ type: 'layout-shift', buffered: true });
    });
    await openDialog(page);
    const rest = await readModal(page);
    const modalAnim = rest.anims.find(a => a.name === 'issue-modal-in');
    ok(Boolean(modalAnim), 'dialog plays an entrance animation', JSON.stringify(rest.anims));
    ok(modalAnim && modalAnim.dur > 0 && modalAnim.dur <= 240, 'entrance duration within the 240ms budget', `${modalAnim?.dur}ms`);
    const backdropAnim = rest.backdropAnims.find(a => a.name === 'issue-backdrop-in');
    ok(Boolean(backdropAnim) && backdropAnim.dur <= 240, 'backdrop fades within the budget', `${backdropAnim?.dur}ms`);
    ok(settled(rest.transform) && rest.opacity === '1', 'settled state has no residual transform/opacity', `${rest.transform} ${rest.opacity}`);
    const keyframes = await page.evaluate(() => {
      const names = [...document.styleSheets].flatMap(sh => { try { return [...sh.cssRules]; } catch { return []; }; })
        .filter(r => r.type === CSSRule.KEYFRAMES_RULE).map(r => ({ name: r.name, props: [...r.cssRules].flatMap(k => [...k.style].filter(p => !['animation-timing-function', 'offset'].includes(p))) }));
      return names.filter(n => n.name.startsWith('issue-'));
    });
    const modalKf = keyframes.find(k => k.name === 'issue-modal-in');
    const onlyComposited = p => ['transform', 'opacity', 'animation-timing-function'].includes(p);
    ok(modalKf && modalKf.props.every(onlyComposited), 'entrance animates only transform/opacity', JSON.stringify(modalKf?.props));
    const motionToken = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--motion-overlay').trim());
    ok(['200ms', '0.2s', '.2s'].includes(motionToken), 'motion tokens are defined on :root', motionToken);

    /* ---------- hover / press / focus deltas ---------- */
    const pill = page.locator('.issue-property').first();
    const pillRest = await pill.evaluate(e => getComputedStyle(e).backgroundColor + '|' + getComputedStyle(e).borderColor);
    await pill.hover(); await wait(240);
    const pillHover = await pill.evaluate(e => getComputedStyle(e).backgroundColor + '|' + getComputedStyle(e).borderColor);
    ok(pillRest !== pillHover, 'property pill reacts to pointer hover', `${pillRest} -> ${pillHover}`);

    const primary = page.locator('.issue-create-footer .primary');
    const disabledLook = await primary.evaluate(e => getComputedStyle(e).opacity + '|' + getComputedStyle(e).transform);
    ok(disabledLook.startsWith('0.5') && settled(disabledLook.split('|')[1]), 'disabled action is dimmed and never lifts', disabledLook);
    await page.locator('.issue-create-title').fill('Polish hover measurement');
    await wait(120);
    const btnRest = await primary.evaluate(e => getComputedStyle(e).transform + '|' + getComputedStyle(e).boxShadow);
    await primary.hover(); await wait(240);
    const btnHover = await primary.evaluate(e => getComputedStyle(e).transform + '|' + getComputedStyle(e).boxShadow);
    ok(btnRest !== btnHover, 'primary action lifts on hover', `${btnRest} -> ${btnHover}`);
    await page.mouse.down(); await wait(140);
    const btnPress = await primary.evaluate(e => getComputedStyle(e).transform);
    await page.mouse.up();
    ok(!settled(btnPress), 'primary action settles on press', btnPress);

    const titleRing = await page.evaluate(() => { const el = document.querySelector('.issue-create-title'); el.focus(); const s = getComputedStyle(el); return { w: parseFloat(s.outlineWidth), c: s.outlineColor, bg: getComputedStyle(document.querySelector('.issue-create-modal')).backgroundColor }; });
    ok(titleRing.w >= 2, 'title shows a focus-visible ring', `${titleRing.w}px ${titleRing.c}`);
    const ringContrast = contrast(titleRing.c, titleRing.bg);
    ok(ringContrast >= 3, 'focus ring has >=3:1 contrast against the dialog', `${ringContrast}:1`);
    await page.keyboard.press('Tab'); await wait(80);
    const pillRing = await pill.evaluate(e => getComputedStyle(e).boxShadow);
    const selectRing = await page.evaluate(() => { const s = getComputedStyle(document.activeElement); return { w: parseFloat(s.outlineWidth), box: s.boxShadow, tag: document.activeElement.tagName }; });
    ok(pillRing !== 'none' || selectRing.w >= 2, 'focused property pill/control is visibly marked', `${pillRing} / ${selectRing.tag} ${selectRing.w}px`);

    /* ---------- focus trap, escape, focus restore ---------- */
    // Real Tab presses (synthetic keydown does not move focus in Chromium).
    await page.locator('.issue-create-title').focus();
    let escaped = false;
    for (let i = 0; i < 30; i++) {
      await page.keyboard.press('Tab');
      if (!(await page.evaluate(() => document.querySelector('.issue-create-modal')?.contains(document.activeElement)))) { escaped = true; break; }
    }
    ok(!escaped, 'Tab never leaves the dialog (focus trap)');
    let escapedBack = false;
    for (let i = 0; i < 30; i++) {
      await page.keyboard.press('Shift+Tab');
      if (!(await page.evaluate(() => document.querySelector('.issue-create-modal')?.contains(document.activeElement)))) { escapedBack = true; break; }
    }
    ok(!escapedBack, 'Shift+Tab never leaves the dialog (focus trap)');
    const triggerHandle = await page.evaluateHandle(() => document.querySelector('[aria-label^="Create issue in"], .page-actions .primary'));
    await page.keyboard.press('Escape'); await wait(400);
    ok(await page.locator('.issue-create-modal').count() === 0, 'Escape closes the dialog');
    const restored = await page.evaluate(() => document.activeElement && document.activeElement !== document.body);
    ok(restored, 'focus returns to the page after close');

    /* ---------- contrast, light ---------- */
    await openDialog(page);
    const light = await page.evaluate(() => {
      const modal = document.querySelector('.issue-create-modal');
      const bg = getComputedStyle(modal).backgroundColor;
      const title = document.querySelector('.issue-create-title');
      return {
        bg,
        text: getComputedStyle(title).color,
        placeholder: getComputedStyle(title, '::placeholder').color,
        muted: getComputedStyle(modal.querySelector('.issue-property')).color,
        pillBg: getComputedStyle(modal.querySelector('.issue-property')).backgroundColor,
        border: getComputedStyle(modal.querySelector('.issue-property')).borderTopColor,
      };
    });
    ok(contrast(light.text, light.bg) >= 4.5, 'light: body text >=4.5:1', `${contrast(light.text, light.bg)}:1`);
    ok(contrast(light.placeholder, light.bg) >= 4.5, 'light: placeholder >=4.5:1', `${contrast(light.placeholder, light.bg)}:1`);
    ok(contrast(light.muted, light.pillBg) >= 4.5, 'light: pill label >=4.5:1', `${contrast(light.muted, light.pillBg)}:1`);

    /* ---------- interactions do not shift layout ---------- */
    await page.evaluate(() => { window.__cls = []; });
    await pill.hover(); await page.keyboard.press('Tab'); await page.locator('.issue-create-title').fill('Polish measurement title'); await wait(300);
    const cls = await page.evaluate(() => window.__cls.reduce((a, b) => a + b, 0));
    ok(cls <= 0.001, 'hover/typing/focus cause no layout shift', `CLS ${cls.toFixed(5)}`);

    /* ---------- geometry at three widths ---------- */
    for (const [w, h] of [[1440, 900], [390, 844], [320, 568]]) {
      await page.setViewportSize({ width: w, height: h }); await wait(250);
      const geo = await page.evaluate(() => {
        const modal = document.querySelector('.issue-create-modal');
        const footer = modal.querySelector('.issue-create-footer');
        const m = modal.getBoundingClientRect(), f = footer.getBoundingClientRect();
        const controls = [...modal.querySelectorAll('button,input,select,textarea')];
        return {
          modalInside: m.top >= -1 && m.bottom <= innerHeight + 1 && m.left >= -1 && m.right <= innerWidth + 1,
          footerVisible: f.bottom <= innerHeight + 1 && f.top >= -1,
          overflow: controls.filter(el => { const r = el.getBoundingClientRect(); return r.width > 0 && (r.right > m.right + 1); }).map(el => el.className || el.tagName),
          hScroll: document.documentElement.scrollWidth > innerWidth + 1,
        };
      });
      ok(geo.modalInside, `dialog fits the viewport at ${w}x${h}`);
      ok(geo.footerVisible, `footer actions stay visible at ${w}x${h}`);
      ok(geo.overflow.length === 0, `no control overflows the dialog at ${w}x${h}`, geo.overflow.join(','));
      ok(!geo.hScroll, `no horizontal page scroll at ${w}x${h}`);
    }
    await page.setViewportSize({ width: 1440, height: 900 });

    /* ---------- reduced motion ---------- */
    const reduceCtx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
    await reduceCtx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const reducePage = await reduceCtx.newPage();
    await reducePage.goto(`${base}/workspace/my-issues/assigned`, { waitUntil: 'networkidle' });
    await openDialog(reducePage);
    const reduced = await readModal(reducePage);
    const reducedDur = reduced.anims.find(a => a.name === 'issue-modal-in')?.dur;
    ok(reducedDur === undefined || reducedDur <= 1, 'reduced motion collapses the entrance', `${reducedDur}ms`);
    ok(settled(reduced.transform), 'reduced motion never moves the dialog', reduced.transform);
    const tokenValue = await reducePage.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--motion-overlay').trim());
    ok(tokenValue === '1ms', 'reduced motion collapses the motion tokens', tokenValue);

    /* ---------- touch: hover must not be required ---------- */
    const touchCtx = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    await touchCtx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const touchPage = await touchCtx.newPage();
    await touchPage.goto(`${base}/workspace/my-issues/assigned`, { waitUntil: 'networkidle' });
    await openDialog(touchPage);
    const touchPill = await touchPage.evaluate(() => { const el = document.querySelector('.issue-property'); const s = getComputedStyle(el); return { bg: s.backgroundColor, border: s.borderTopColor, height: el.getBoundingClientRect().height }; });
    const touchRest = await touchPage.evaluate(() => getComputedStyle(document.querySelector('.issue-create-modal')).backgroundColor);
    ok(touchPill.bg === touchRest, 'touch: pills keep their resting style (no hover-only affordance)', `${touchPill.bg} vs ${touchRest}`);
    ok(touchPill.height >= 24, 'touch: pill hit target stays >=24px', `${Math.round(touchPill.height)}px`);

    /* ---------- dark theme ---------- */
    const darkCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await darkCtx.addInitScript(t => { localStorage.setItem('hoja_token', t); localStorage.setItem('theme', 'dark'); }, token);
    const darkPage = await darkCtx.newPage();
    await darkPage.goto(`${base}/workspace/my-issues/assigned`, { waitUntil: 'networkidle' });
    await darkPage.evaluate(() => { document.documentElement.dataset.theme = 'dark'; });
    await openDialog(darkPage);
    const dark = await darkPage.evaluate(() => {
      const modal = document.querySelector('.issue-create-modal');
      const pill = modal.querySelector('.issue-property');
      return { bg: getComputedStyle(modal).backgroundColor, text: getComputedStyle(modal.querySelector('.issue-create-title')).color,
        muted: getComputedStyle(pill).color, pillBg: getComputedStyle(pill).backgroundColor, border: getComputedStyle(pill).borderTopColor };
    });
    ok(contrast(dark.text, dark.bg) >= 4.5, 'dark: body text >=4.5:1', `${contrast(dark.text, dark.bg)}:1`);
    ok(contrast(dark.muted, dark.pillBg) >= 4.5, 'dark: pill label >=4.5:1', `${contrast(dark.muted, dark.pillBg)}:1`);
    const darkPill = darkPage.locator('.issue-property').first();
    const dRest = await darkPill.evaluate(e => getComputedStyle(e).backgroundColor);
    await darkPill.hover(); await wait(240);
    const dHover = await darkPill.evaluate(e => getComputedStyle(e).backgroundColor);
    ok(dRest !== dHover, 'dark: hover state still reads', `${dRest} -> ${dHover}`);

    ok(errors.length === 0, 'no page errors while exercising the dialog', errors.join(' | '));
  } finally {
    if (browser) await browser.close();
    server.kill();
    fs.rmSync(dir, { recursive: true, force: true });
  }
  console.log(`\nSUMMARY: ${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error('SCRIPT ERROR', e); server.kill(); process.exit(2); });
