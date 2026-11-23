#!/usr/bin/env node
/**
 * HOJA-489 pane probe: the project-statuses and project-templates settings panes
 * must render real rows and persist a change through the real server.
 *
 * Dependency-free CDP, same contract as test-settings-browser.mjs:
 *   HOJA_UI_URL   seeded server that serves the built SPA   (default :18942)
 *   HOJA_CDP_URL  Chromium with --remote-debugging-port     (default :19227)
 *   HOJA_UI_TOKEN API token for the seeded workspace
 *
 * Fail-before: run it against a pristine tree's server. There the panes are in
 * SETTINGS_UNAVAILABLE, so `.project-status-row` never appears and the probe
 * throws at the first waitFor instead of passing on an empty list.
 */
import assert from 'node:assert/strict';

const base = process.env.HOJA_UI_URL || 'http://127.0.0.1:18942';
const cdpUrl = process.env.HOJA_CDP_URL || 'http://127.0.0.1:19227';
const token = process.env.HOJA_UI_TOKEN;
if (!token) throw new Error('Set HOJA_UI_TOKEN for the disposable test workspace.');

const targets = await (await fetch(`${cdpUrl}/json/list`)).json();
const target = targets.find(t => t.type === 'page');
assert.ok(target, 'A Chromium page target is available.');
const ws = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve, reject) => {
  ws.addEventListener('open', resolve, {once: true});
  ws.addEventListener('error', reject, {once: true});
});
let sequence = 0;
const pending = new Map();
ws.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  if (!message.id) return;
  const request = pending.get(message.id);
  if (!request) return;
  pending.delete(message.id);
  if (message.error) request.reject(new Error(JSON.stringify(message.error)));
  else request.resolve(message.result);
});
function cdp(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error(`CDP timeout after 15s: ${method}`));
    }, 15000);
    pending.set(id, {
      resolve: value => { clearTimeout(timer); resolve(value); },
      reject: error => { clearTimeout(timer); reject(error); },
    });
    ws.send(JSON.stringify({id, method, params}));
  });
}
const step = label => console.log(`-- ${label}`);
async function evaluate(expression) {
  const response = await cdp('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true, userGesture: true});
  if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
  return response.result.value;
}
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
setTimeout(() => { console.error('watchdog: probe exceeded 240s'); process.exit(3); }, 240000).unref();
async function waitFor(expression, label = expression) {
  for (let i = 0; i < 150; i++) {
    if (await evaluate(expression)) return;
    await wait(200);
  }
  throw new Error(`Timed out: ${label}`);
}
async function navigate(section) {
  await cdp('Page.navigate', {url: `${base}/workspace/settings/${section}`});
  await waitFor(`document.querySelectorAll('.settings-sidebar').length === 1`, `${section}: shell`);
  await wait(400);
}
async function click(selector) {
  const point = await evaluate(`(() => {const el = document.querySelector(${JSON.stringify(selector)});if (!el) return null;el.scrollIntoView({block:'center',inline:'center'});const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  assert.ok(point, `Click target: ${selector}`);
  await cdp('Input.dispatchMouseEvent', {type: 'mouseMoved', ...point});
  await cdp('Input.dispatchMouseEvent', {type: 'mousePressed', ...point, button: 'left', clickCount: 1});
  await cdp('Input.dispatchMouseEvent', {type: 'mouseReleased', ...point, button: 'left', clickCount: 1});
  await wait(120);
}
async function fill(selector, text) {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus();true`);
  await cdp('Input.insertText', {text});
}
async function setSelect(selector, value) {
  await evaluate(`(() => {const el = document.querySelector(${JSON.stringify(selector)});const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;setter.call(el, ${JSON.stringify(value)});el.dispatchEvent(new Event('change', {bubbles: true}));return true;})()`);
}

await cdp('Page.enable');
await cdp('Runtime.enable');
await cdp('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false});
await cdp('Page.navigate', {url: base});
await evaluate(`localStorage.setItem('hoja_token', ${JSON.stringify(token)}); localStorage.setItem('theme', 'light'); true`);

// ---- statuses: render the configured set -----------------------------------
step('statuses: navigate');
await navigate('project-statuses');
await waitFor(`document.querySelectorAll('.project-status-row').length >= 6`,
  'the seeded project statuses render as rows');
const defaults = ['backlog', 'planned', 'started', 'paused', 'completed', 'canceled'];
const initial = await evaluate(`[...document.querySelectorAll('.project-status-row input[aria-label$="name"]')].map(el => el.value)`);
for (const name of defaults) assert.ok(initial.includes(name), `seeded status "${name}" is present (got ${JSON.stringify(initial)})`);

// ---- statuses: create through the UI, then prove it survived a reload -----
const statusName = `QA review ${Date.now()}`;
await fill('input[aria-label="Project status name"]', statusName);
await setSelect('select[aria-label="Project status category"]', 'started');
step('statuses: create');
await click('form.label-create button.primary');
const createDebug = await evaluate(`({
  input: document.querySelector('input[aria-label="Project status name"]')?.value,
  alert: document.querySelector('.settings-page [role=alert]')?.textContent,
  rows: [...document.querySelectorAll('.project-status-row input[aria-label$="name"]')].map(el => el.value),
})`);
await waitFor(`[...document.querySelectorAll('.project-status-row input[aria-label$="name"]')].some(el => el.value === ${JSON.stringify(statusName)})`,
  `the new status appears in the list (state: ${JSON.stringify(createDebug)})`);
await waitFor(`[...document.querySelectorAll('.settings-page [role=status]')].some(el => el.textContent.includes('Created'))`,
  'the pane reports the save');

step('statuses: reload');
await cdp('Page.reload');
await waitFor(`[...document.querySelectorAll('.project-status-row input[aria-label$="name"]')].some(el => el.value === ${JSON.stringify(statusName)})`,
  'the created status survives a reload (server round trip)');

// ---- statuses: reorder through the UI, prove the order persisted ----------
const indexBefore = await evaluate(`[...document.querySelectorAll('.project-status-row input[aria-label$="name"]')].findIndex(el => el.value === ${JSON.stringify(statusName)})`);
assert.ok(indexBefore >= 6, `the new status starts after the defaults (got index ${indexBefore})`);
step('statuses: reorder click');
await click(`button[aria-label="Move ${statusName} up"]`);
await waitFor(`[...document.querySelectorAll('.settings-page [role=status]')].some(el => el.textContent.includes('Order saved'))`,
  'the reorder is acknowledged');
await cdp('Page.reload');
await waitFor(`document.querySelectorAll('.project-status-row').length > 0`, 'rows after reorder reload');
const indexAfter = await evaluate(`[...document.querySelectorAll('.project-status-row input[aria-label$="name"]')].findIndex(el => el.value === ${JSON.stringify(statusName)})`);
assert.equal(indexAfter, indexBefore - 1, `reorder persisted (index ${indexBefore} -> ${indexAfter})`);

// ---- templates: render, create through the UI, prove it survived ----------
step('templates: navigate');
await navigate('project-templates');
await waitFor(`!!document.querySelector('form.project-template-form')`, 'the template form renders');
const templateName = `Probe template ${Date.now()}`;
await fill('input[aria-label="Template name"]', templateName);
await fill('textarea[aria-label="Template description"]', 'Created by the HOJA-489 pane probe.');
step('templates: submit');
await click('form.project-template-form button.primary');
await waitFor(`[...document.querySelectorAll('.project-template-row b')].some(el => el.textContent === ${JSON.stringify(templateName)})`,
  'the created template appears in the list');
await cdp('Page.reload');
await waitFor(`[...document.querySelectorAll('.project-template-row b')].some(el => el.textContent === ${JSON.stringify(templateName)})`,
  'the created template survives a reload');

// ---- the persistent state is the server's, not the UI's -------------------
const server = await evaluate(`fetch('/graphql', {method: 'POST', credentials: 'include', headers: {'content-type': 'application/json', authorization: 'Bearer ' + localStorage.getItem('hoja_token')}, body: JSON.stringify({query: '{projectStatuses{name position} projectTemplates{name}}'})}).then(r => r.json())`);
assert.ok(server?.data?.projectStatuses, `server answers projectStatuses: ${JSON.stringify(server).slice(0, 200)}`);
assert.ok(server.data.projectStatuses.some(s => s.name === statusName), 'the server, not the UI cache, holds the created status');
assert.ok(server.data.projectTemplates.some(t => t.name === templateName), 'the server, not the UI cache, holds the created template');

console.log(`ok  statuses rendered: seeded defaults ${JSON.stringify(defaults)}`);
console.log(`ok  created ${statusName} through the UI and it survived a reload`);
console.log(`ok  reorder persisted: index ${indexBefore} -> ${indexAfter}`);
console.log(`ok  template ${templateName} created through the UI and survived a reload`);
console.log('pane probe ok: HOJA-489 panes render and persist');
ws.close();
process.exit(0);
