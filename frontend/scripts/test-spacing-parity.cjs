// Run after vite build (optionally pass an isolated build output directory).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../../local-e2e/node_modules/playwright');

(async () => {
  const root = path.resolve(__dirname, '..');
  const dist = path.resolve(root, process.argv[2] || 'dist');
  const html = fs.readFileSync(path.join(dist, 'index.html'), 'utf8');
  const asset = html.match(/href="([^" ]+\.css)"/)[1];
  const css = fs.readFileSync(path.join(dist, asset.replace(/^\//, '')), 'utf8');
  const vendor = fs.readFileSync(path.join(root, 'node_modules/react-grab/dist/styles.css'), 'utf8');
  const source = fs.readFileSync(path.join(root, 'src/main.tsx'), 'utf8');
  const appClass = source.match(/className="(app min-h-dvh[^"\n]+)"/)[1];
  const sidebarClass = source.match(/data-workspace-sidebar[\s\S]*?className="([^"]+)"/)[1];
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.setContent(`<style>${css}</style><div class="${appClass}"><aside class="${sidebarClass}">Navigation</aside></div>`);
    for (const inspector of [false, true]) {
      if (inspector) await page.addStyleTag({ content: `@layer vendor {${vendor}}` });
      for (const fontSize of [14, 16, 17.6]) {
        for (const collapsed of [false, true]) {
          const measured = await page.evaluate(({ fontSize, collapsed }) => {
            document.documentElement.style.fontSize = `${fontSize}px`;
            const app = document.querySelector('.app');
            app.toggleAttribute('data-sidebar-collapsed', collapsed);
            return {
              spacing: getComputedStyle(document.documentElement).getPropertyValue('--spacing').trim(),
              track: parseFloat(getComputedStyle(app).gridTemplateColumns),
              sidebar: document.querySelector('aside').getBoundingClientRect().width,
            };
          }, { fontSize, collapsed });
          const label = JSON.stringify({ inspector, fontSize, collapsed });
          assert.equal(measured.spacing, '4px', label);
          assert.equal(measured.track, collapsed ? 44 : 244, label);
          assert.equal(measured.sidebar, measured.track, label);
        }
      }
    }
    console.log('Spacing parity: expanded/collapsed sidebar matches its track at three root font sizes, with and without the dev inspector.');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
