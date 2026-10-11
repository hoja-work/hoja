// Run from repo root: node scripts/test-brand-route.cjs
// Verifies the brand mark route returns SVG content (not the SPA shell) from a built web root.
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const repoScratch = path.join(repo, 'local-e2e/artifacts');
fs.mkdirSync(repoScratch, { recursive: true });
const python = path.join(repo, '.venv/bin/python');
const dir = fs.mkdtempSync(path.join(repoScratch, 'brand-route-'));
const web = path.join(dir, 'dist');
fs.mkdirSync(path.join(web, 'assets'), { recursive: true });
fs.writeFileSync(path.join(web, 'index.html'), '<!doctype html><title>SPA</title>');
fs.writeFileSync(path.join(web, 'hoja-clover.8371400e.svg'), '<svg xmlns="http://www.w3.org/2000/svg"></svg>');
const db = path.join(dir, 'brand.db');
const port = 18700 + Math.floor(Math.random() * 200);
const migrate = spawnSync(python, ['-c', `from cliniar_server.db import make_engine,migrate; e=make_engine(${JSON.stringify(db)}); migrate(e); e.dispose()`], { cwd: repo, env: { ...process.env, PYTHONPATH: repo }, encoding: 'utf8' });
assert.equal(migrate.status, 0, migrate.stderr);
const server = spawn(path.join(repo, '.venv/bin/cliniar-serve'), ['serve', '--db', db, '--host', '127.0.0.1', '--port', String(port)], { cwd: repo, env: { ...process.env, CLINIAR_WEB_ROOT: web }, stdio: 'ignore' });

(async () => {
  try {
    let up = false;
    for (let i = 0; i < 60 && !up; i++) {
      try { up = (await fetch(`http://127.0.0.1:${port}/health`)).ok; } catch {}
      if (!up) await new Promise(r => setTimeout(r, 250));
    }
    assert.ok(up, 'server did not start');
    const logo = await fetch(`http://127.0.0.1:${port}/hoja-clover.8371400e.svg`);
    assert.match(logo.headers.get('content-type') || '', /image\/svg\+xml/, 'brand mark must be served as SVG');
    assert.match(await logo.text(), /<svg/, 'brand mark body must be SVG');
    const spa = await fetch(`http://127.0.0.1:${port}/some/unknown/path`);
    assert.match(await spa.text(), /SPA/, 'unknown paths must still fall back to index.html');
    console.log('Brand route: /hoja-clover.8371400e.svg serves SVG; SPA fallback preserved');
  } finally {
    server.kill();
    fs.rmSync(dir, { recursive: true, force: true });
  }
})().catch(error => { console.error(error.message); process.exit(1); });
