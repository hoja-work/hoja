// Run from repo root. Measures the My issues Kanban board: width fill, card text, and status-row alignment.
// Usage: PROBE_TOKEN=... [PROBE_URL=https://hoja.clover4.app] node scripts/ui-probes/kanban-measure.cjs 1280 1920 2560
const { chromium } = require('../../local-e2e/node_modules/playwright');
const url = process.env.PROBE_URL || 'https://hoja.clover4.app';
const token = process.env.PROBE_TOKEN;
if (!token) { console.error('set PROBE_TOKEN'); process.exit(2); }
const widths = process.argv.slice(2).map(Number).filter(Boolean);
if (!widths.length) widths.push(1280, 1920, 2560);
(async () => {
  const browser = await chromium.launch({ headless: true });
  const results = [];
  try {
    for (const width of widths) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      await page.addInitScript(t => localStorage.setItem('hoja_token', t), token);
      await page.goto(url + '/workspace/my-issues/assigned', { waitUntil: 'domcontentloaded', timeout: 30000 });
      await page.evaluate(() => localStorage.setItem('hoja_issue_layout', 'Board'));
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.waitForSelector('.kanban-card', { timeout: 20000 }).catch(() => {});
      const m = await page.evaluate(() => {
        const r = el => el ? el.getBoundingClientRect() : null;
        const round = o => o && { l: Math.round(o.left), r: Math.round(o.right), t: Math.round(o.top), b: Math.round(o.bottom), w: Math.round(o.width), h: Math.round(o.height) };
        const main = document.querySelector('.main');
        const board = document.querySelector('.kanban');
        const columns = [...document.querySelectorAll('.kanban-column')];
        const card = document.querySelector('.kanban-card');
        const out = {
          viewport: innerWidth,
          main: round(r(main)), mainMaxWidth: main && getComputedStyle(main).maxWidth,
          board: round(r(board)),
          columnCount: columns.length,
          columnWidths: columns.slice(0, 5).map(c => Math.round(c.getBoundingClientRect().width)),
          rightGapToViewport: board ? Math.round(innerWidth - board.getBoundingClientRect().right) : null,
          docOverflowX: document.documentElement.scrollWidth - innerWidth,
        };
        if (card) {
          const small = card.querySelector('.kanban-card-open small');
          const text = small ? small.innerText.replace(/\s+/g, ' ').trim() : '';
          out.cardHeaderText = text;
          const wrap = card.closest('.kanban-card-wrap') || card;
          out.cardWidth = Math.round(wrap.getBoundingClientRect().width);
          out.cardToColumnRatio = +(wrap.getBoundingClientRect().width / card.closest('.kanban-column').getBoundingClientRect().width).toFixed(2);
          out.teamKeyBadgeCount = document.querySelectorAll('.team-key-badge').length;
          const props = [...card.querySelectorAll('.kanban-card-props > *')];
          out.propRows = [...new Set(props.map(p => Math.round(p.getBoundingClientRect().top)))].length;
          out.propCentersY = props.map(p => {
            const b = p.getBoundingClientRect(); return { cls: p.className.toString().slice(0, 40), cy: Math.round(b.top + b.height / 2), h: Math.round(b.height) };
          });
          const trigger = card.querySelector('.kanban-card-props .status-picker-trigger');
          const chips = props.filter(p => p !== trigger.closest('.status-picker'));
          if (trigger && chips.length) {
            const tb = trigger.getBoundingClientRect();
            const cb = chips[0].getBoundingClientRect();
            out.statusVsChipCenterOffsetPx = Math.round((tb.top + tb.height / 2) - (cb.top + cb.height / 2));
          }
        }
        return out;
      });
      results.push(m);
      await page.close();
    }
  } finally { await browser.close(); }
  console.log(JSON.stringify(results, null, 1));
})().catch(e => { console.error(e.message); process.exit(1); });
