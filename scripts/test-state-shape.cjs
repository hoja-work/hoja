// State-shape invariants (HOJA-746).
//
// An interactive surface must keep the same shape in every state. Concretely:
// the box a control paints when hovered (its background/border) or focused (its
// outline or ring) must not be cut by an ancestor that clips on that axis
// (overflow hidden/clip). A cut surface shows up as a hover pill that is rounded
// on one side and square on the other, or a focus ring with a missing edge.
//
// The probe drives the real app: every sidebar destination, every Settings
// section, a task and a project, the workspace menu, in light and dark, at
// desktop, 125% zoom and phone sizes. It moves the real mouse WITHOUT scrolling
// (Playwright's hover() scrolls clipped containers into view, which hides this
// exact bug), and focuses with preventScroll after a keyboard event so
// :focus-visible applies.
//
// Usage:
//   node scripts/test-state-shape.cjs                 # boots a seeded server
//   PROBE_URL=http://127.0.0.1:8797 PROBE_TOKEN=... node scripts/test-state-shape.cjs
//   STATE_SHAPE_OUT=dir  writes findings.json and one cropped PNG per finding.
const { chromium } = require('../local-e2e/node_modules/playwright');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const out = process.env.STATE_SHAPE_OUT || '';
const wait = ms => new Promise(r => setTimeout(r, ms));

const CONFIGS = [
  { name: 'desktop-light', width: 1440, height: 900, dsf: 1, scheme: 'light' },
  { name: 'desktop-dark', width: 1280, height: 720, dsf: 1, scheme: 'dark' },
  { name: 'zoom125-light', width: 1152, height: 720, dsf: 1.25, scheme: 'light' },
  { name: 'phone-light', width: 390, height: 844, dsf: 3, scheme: 'light', phone: true },
];
const TOL = 0.5;

async function boot() {
  if (process.env.PROBE_URL) {
    assert.ok(process.env.PROBE_TOKEN, 'PROBE_URL needs PROBE_TOKEN');
    return { base: process.env.PROBE_URL.replace(/\/$/, ''), token: process.env.PROBE_TOKEN, stop: () => {} };
  }
  const web = path.join(repo, 'frontend/dist');
  assert.ok(fs.existsSync(path.join(web, 'index.html')), 'build the frontend first: cd frontend && npm run build');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hoja-state-shape-'));
  const db = path.join(dir, 'state-shape.db');
  const token = 'hoja_state_shape_token';
  const py = path.join(repo, '.venv/bin/python');
  const seed = spawnSync(py, ['-c', `from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine(${JSON.stringify(db)}); migrate(e)
seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token=${JSON.stringify(token)}, demo_issues=True)
e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
  assert.equal(seed.status, 0, seed.stderr);
  const port = 18900 + Math.floor(Math.random() * 90);
  const server = spawn(path.join(repo, '.venv/bin/hoja-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], {
    cwd: repo, env: { ...process.env, HOJA_WEB_ROOT: web }, stdio: 'ignore',
  });
  const base = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 120; i++) { try { if ((await fetch(`${base}/health`)).ok) break; } catch {} await wait(250); }
  return { base, token, stop: () => { server.kill(); fs.rmSync(dir, { recursive: true, force: true }); } };
}

// Runs in the page. Returns every visible interactive element whose border box,
// or border box grown by its potential focus ring, crosses an ancestor that
// clips on that axis. Elements entirely outside a clip box are skipped: those
// are scrolled or deliberately hidden, not cut.
function collectCandidates(slop) {
  const SEL = 'button,a[href],[role=button],[role=tab],[role=menuitem],[role=option],[role=switch],[role=checkbox],summary,input:not([type=hidden]),select,textarea,[tabindex="0"]';
  // Ancestors that clip `el` per CSS: overflow only clips descendants whose
  // containing-block chain passes through it (fixed and absolute boxes escape
  // non-positioned ancestors). Once an axis passes a scroll container, outer
  // clippers on that axis only cut scrolled content, which is not a shape bug.
  const clips = el => {
    const res = [];
    let pos = getComputedStyle(el).position;
    let scrolledX = false, scrolledY = false;
    for (let a = el.parentElement; a && a !== document.documentElement; a = a.parentElement) {
      const s = getComputedStyle(a);
      const makesCB = s.transform !== 'none' || s.filter !== 'none' || s.perspective !== 'none' || /paint|layout|strict|content/.test(s.contain) || s.containerType !== 'normal';
      const inChain = pos === 'fixed' ? makesCB : pos === 'absolute' ? (s.position !== 'static' || makesCB) : true;
      if (inChain) {
        const x = !scrolledX && (s.overflowX === 'hidden' || s.overflowX === 'clip');
        const y = !scrolledY && (s.overflowY === 'hidden' || s.overflowY === 'clip');
        if (x || y) {
          const r = a.getBoundingClientRect();
          res.push({ el: a, x, y, l: r.left + a.clientLeft, t: r.top + a.clientTop, r: r.left + a.clientLeft + a.clientWidth, b: r.top + a.clientTop + a.clientHeight });
        }
        // A scroll container cuts paint that lies outside its scrollable overflow area:
        // outlines and shadows do not extend scrollHeight/scrollWidth, so a focus ring on
        // the first or last row can never be scrolled into view.
        const sx = !scrolledX && (s.overflowX === 'auto' || s.overflowX === 'scroll');
        const sy = !scrolledY && (s.overflowY === 'auto' || s.overflowY === 'scroll');
        if (sx || sy) {
          const r = a.getBoundingClientRect();
          const l = r.left + a.clientLeft - a.scrollLeft, t = r.top + a.clientTop - a.scrollTop;
          res.push({ el: a, x: sx, y: sy, scroller: true, l, t, r: l + a.scrollWidth, b: t + a.scrollHeight });
        }
        if (s.overflowX === 'auto' || s.overflowX === 'scroll') scrolledX = true;
        if (s.overflowY === 'auto' || s.overflowY === 'scroll') scrolledY = true;
        // overflow:hidden with a real scroll offset is a scroller too (scrolled by script).
        if ((s.overflowY === 'hidden') && a.scrollHeight > a.clientHeight + 1 && a.scrollTop > 0) scrolledY = true;
        pos = s.position === 'absolute' || s.position === 'fixed' ? s.position : 'static';
      }
    }
    return res;
  };
  const label = el => (el.getAttribute('aria-label') || el.textContent || el.getAttribute('placeholder') || el.tagName).replace(/\s+/g, ' ').trim().slice(0, 48);
  const describe = el => {
    const parts = [];
    for (let e = el; e && parts.length < 3 && e !== document.body; e = e.parentElement) {
      const data = [...e.attributes].map(a => a.name).filter(n => n.startsWith('data-') && !n.startsWith('data-state')).slice(0, 2).map(n => `[${n}]`).join('');
      const aria = e.getAttribute('aria-label') ? `[aria-label="${e.getAttribute('aria-label').slice(0, 30)}"]` : '';
      parts.unshift(e.tagName.toLowerCase() + (data || aria));
    }
    return parts.join(' > ');
  };
  const W = innerWidth, H = innerHeight;
  const found = [];
  let i = 0;
  for (const el of document.querySelectorAll(SEL)) {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    if (r.width < 1 || r.height < 1 || s.visibility === 'hidden' || s.display === 'none' || Number(s.opacity) === 0) continue;
    if (r.right <= 0 || r.bottom <= 0 || r.left >= W || r.top >= H) continue;
    if (el.closest('[inert],[aria-hidden="true"]')) continue;
    const cuts = [];
    for (const c of clips(el)) {
      // Fully outside the clip box: hidden on purpose (collapsed rail, scrolled away).
      if (r.right <= c.l || r.left >= c.r || r.bottom <= c.t || r.top >= c.b) { cuts.length = 0; break; }
      const over = {
        left: c.x ? c.l - r.left : null, right: c.x ? r.right - c.r : null,
        top: c.y ? c.t - r.top : null, bottom: c.y ? r.bottom - c.b : null,
      };
      if (Object.values(over).some(v => v !== null && v > -slop)) cuts.push({ clipper: describe(c.el), ...Object.fromEntries(Object.entries(over).map(([k, v]) => [k, v === null ? null : +v.toFixed(2)])), hScroll: c.el.scrollWidth - c.el.clientWidth });
    }
    if (!cuts.length) continue;
    const id = `ss-${i++}`;
    el.setAttribute('data-state-shape-probe', id);
    found.push({ id, label: label(el), path: describe(el), cuts });
  }
  return found;
}

// Runs in the page for one element in its current state: what does it paint
// and how far beyond its border box does that paint reach?
function paintedExtent(id) {
  const el = document.querySelector(`[data-state-shape-probe="${id}"]`);
  if (!el) return null;
  const s = getComputedStyle(el);
  const transparent = c => !c || c === 'transparent' || /rgba\([^)]*,\s*0\)$/.test(c);
  const bg = !transparent(s.backgroundColor) || s.backgroundImage !== 'none';
  const border = ['Top', 'Right', 'Bottom', 'Left'].some(k => parseFloat(s[`border${k}Width`]) > 0 && s[`border${k}Style`] !== 'none' && !transparent(s[`border${k}Color`]));
  let ring = 0;
  if (s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0) ring = Math.max(ring, parseFloat(s.outlineWidth) + parseFloat(s.outlineOffset || 0));
  for (const m of (s.boxShadow === 'none' ? '' : s.boxShadow).matchAll(/(?:rgba?\([^)]*\)|#\w+|\w+)?\s*(-?[\d.]+)px\s+(-?[\d.]+)px\s+([\d.]+)px\s+([\d.]+)px(?!\s*inset)/g)) {
    if (!/inset/.test(m[0]) && Number(m[1]) === 0 && Number(m[2]) === 0) ring = Math.max(ring, Number(m[4]));
  }
  const r = el.getBoundingClientRect();
  return { bg, border, ring, radius: [s.borderTopLeftRadius, s.borderTopRightRadius, s.borderBottomRightRadius, s.borderBottomLeftRadius], rect: { l: r.left, t: r.top, r: r.right, b: r.bottom } };
}

function cutBy(cand, rect, grow) {
  // Re-measure against the clip boxes recorded at collection time, grown by `grow`.
  const sides = [];
  for (const c of cand.cuts) for (const side of ['left', 'right', 'top', 'bottom']) if (c[side] !== null && c[side] + grow > TOL) sides.push({ side, px: +(c[side] + grow).toFixed(2), clipper: c.clipper, hScroll: c.hScroll });
  return sides;
}

async function routes(page, base) {
  await page.goto(`${base}/`);
  await page.waitForSelector('[data-workspace-sidebar]');
  await wait(600);
  const n = await page.locator('[data-workspace-sidebar] [data-nav-item]').count();
  const paths = new Set(['/']);
  for (let i = 0; i < n; i++) {
    const item = page.locator('[data-workspace-sidebar] [data-nav-item]').nth(i);
    if (!(await item.isVisible())) continue;
    await item.click({ timeout: 3000 }).catch(() => {});
    await wait(300);
    paths.add(new URL(page.url()).pathname);
  }
  for (const id of ['account', 'preferences', 'notifications', 'members', 'teams', 'labels', 'api']) paths.add(`/workspace/settings/${id}`);
  const issue = await page.evaluate(async () => {
    const r = await fetch('/graphql', { method: 'POST', headers: { 'content-type': 'application/json', authorization: `Bearer ${localStorage.getItem('hoja_token')}` }, body: JSON.stringify({ query: 'query{issues(first:1){nodes{identifier}} projects(first:1){nodes{id}}}' }) });
    return (await r.json()).data;
  }).catch(() => null);
  if (issue?.issues?.nodes?.[0]) paths.add(`/workspace/issue/${issue.issues.nodes[0].identifier}`);
  if (issue?.projects?.nodes?.[0]) paths.add(`/workspace/project/${issue.projects.nodes[0].id}`);
  return [...paths];
}

(async () => {
  const { base, token, stop } = await boot();
  const browser = await chromium.launch({ headless: true });
  const findings = [];
  let probed = 0;
  const shot = new Set();
  try {
    const seedCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await seedCtx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
    const ROUTES = await routes(await seedCtx.newPage(), base);
    await seedCtx.close();
    if (out) fs.mkdirSync(out, { recursive: true });

    for (const cfg of CONFIGS) {
      const ctx = await browser.newContext({ viewport: { width: cfg.width, height: cfg.height }, deviceScaleFactor: cfg.dsf, colorScheme: cfg.scheme, reducedMotion: 'reduce' });
      await ctx.addInitScript(t => localStorage.setItem('hoja_token', t), token);
      const page = await ctx.newPage();
      const surfaces = [...ROUTES.map(r => ({ route: r })), { route: '/', open: '[data-workspace-trigger]' }];
      for (const surface of surfaces) {
        await page.goto(base + surface.route);
        await page.waitForSelector('[data-workspace-sidebar]', { timeout: 8000 }).catch(() => {});
        await wait(700);
        if (cfg.phone) {
          const t = page.locator('button.mobile-nav-toggle[aria-expanded="false"]');
          if (await t.count()) { await t.click(); await wait(250); }
        }
        if (surface.open) { await page.locator(surface.open).first().click({ timeout: 3000 }).catch(() => {}); await wait(300); }
        const where = surface.route + (surface.open ? ` (menu ${surface.open})` : '');
        const cands = await page.evaluate(collectCandidates, 8);
        for (const cand of cands) {
          probed++;
          const sel = `[data-state-shape-probe="${cand.id}"]`;
          // Hover: real pointer, no scroll.
          const box = await page.evaluate(s => { const r = document.querySelector(s)?.getBoundingClientRect(); return r && { x: r.left, y: r.top, w: r.width, h: r.height }; }, sel);
          if (!box) continue;
          const vx = Math.min(Math.max(box.x + box.w / 2, 1), cfg.width - 1), vy = Math.min(Math.max(box.y + box.h / 2, 1), cfg.height - 1);
          await page.mouse.move(vx, vy);
          await wait(40);
          const hovered = await page.evaluate(paintedExtent, cand.id);
          const states = [];
          if (hovered && (hovered.bg || hovered.border)) {
            const sides = cutBy(cand, hovered.rect, 0);
            if (sides.length) states.push({ state: 'hover', sides, radius: hovered.radius });
          }
          // Focus-visible: keyboard modality, then focus without scrolling.
          await page.mouse.move(0, 0);
          await page.keyboard.press('Shift');
          const focused = await page.evaluate(s => { const el = document.querySelector(s); el?.focus({ preventScroll: true }); return document.activeElement === el && el.matches(':focus-visible'); }, sel);
          if (focused) {
            const f = await page.evaluate(paintedExtent, cand.id);
            if (f && f.ring > 0) {
              const sides = cutBy(cand, f.rect, f.ring).filter(x => !(states[0]?.sides || []).some(y => y.side === x.side && y.px >= x.px));
              if (sides.length) states.push({ state: 'focus-visible', sides, ring: f.ring });
            }
            await page.evaluate(() => document.activeElement?.blur());
          }
          if (!states.length) continue;
          const finding = { config: cfg.name, surface: where, label: cand.label, path: cand.path, states };
          const shotKey = `${cfg.name}|${states[0].state}|${states[0].sides[0].clipper}`;
          if (out && !shot.has(shotKey)) {
            shot.add(shotKey);
            const file = `${shot.size.toString().padStart(2, '0')}-${cfg.name}-${states[0].state}-${cand.label.replace(/\W+/g, '_').slice(0, 24)}.png`;
            if (states[0].state === 'hover') { await page.mouse.move(vx, vy); await wait(40); }
            else { await page.keyboard.press('Shift'); await page.evaluate(s => document.querySelector(s)?.focus({ preventScroll: true }), sel); }
            const pad = 16, x0 = Math.max(0, box.x - pad), y0 = Math.max(0, box.y - pad);
            await page.screenshot({ path: path.join(out, file), clip: { x: x0, y: y0, width: Math.min(box.w + pad * 2, cfg.width - x0), height: Math.min(box.h + pad * 2, cfg.height - y0) } }).catch(() => {});
            await page.evaluate(() => document.activeElement?.blur());
            finding.screenshot = file;
          }
          findings.push(finding);
        }
        // Clear any menu this surface opened before the next goto.
        await page.keyboard.press('Escape').catch(() => {});
      }
      await ctx.close();
    }
  } finally {
    await browser.close();
    stop();
  }
  if (out) fs.writeFileSync(path.join(out, 'findings.json'), JSON.stringify({ probed, findings }, null, 2));
  // Group by mechanism (state + clipping ancestor + cut sides) so one cause reads as one line.
  const groups = new Map();
  for (const f of findings) for (const st of f.states) {
    const clipper = st.sides[0].clipper;
    const key = `${st.state} | ${clipper} | ${[...new Set(st.sides.map(x => x.side))].join('/')}`;
    const g = groups.get(key) || { labels: new Set(), where: new Set(), max: 0, hScroll: 0, shot: f.screenshot };
    g.labels.add(f.label); g.where.add(`${f.config} ${f.surface}`);
    g.max = Math.max(g.max, ...st.sides.map(x => x.px)); g.hScroll = Math.max(g.hScroll, ...st.sides.map(x => x.hScroll || 0));
    groups.set(key, g);
  }
  for (const [key, g] of [...groups].sort((a, b) => b[1].labels.size - a[1].labels.size)) {
    console.log(`✗ ${key} (up to ${g.max}px${g.hScroll > 0 ? `; clipper scrolls ${g.hScroll}px sideways` : ''})\n    ${g.labels.size} controls: ${[...g.labels].slice(0, 5).map(l => JSON.stringify(l)).join(', ')}${g.labels.size > 5 ? ', …' : ''}\n    in ${g.where.size}: ${[...g.where].slice(0, 3).join(', ')}${g.where.size > 3 ? ', …' : ''}${g.shot ? `\n    e.g. ${g.shot}` : ''}`);
  }
  console.log(`${findings.length ? 'FAIL' : 'ok'} state-shape: ${probed} near-edge controls checked, ${groups.size} distinct mechanisms`);
  process.exit(findings.length ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
