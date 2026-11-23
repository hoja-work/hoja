#!/usr/bin/env node
/**
 * HOJA-490 pane probe: the permissions and security settings panes must render
 * the real server state and persist a change that the server then enforces.
 *
 * Dependency-free CDP, same contract as test-settings-browser.mjs:
 *   HOJA_UI_URL   seeded server that serves the built SPA   (default :18942)
 *   HOJA_CDP_URL  Chromium with --remote-debugging-port     (default :19227)
 *   HOJA_UI_TOKEN API token for the seeded workspace
 *
 * Fail-before: run it against a pristine tree's server. There both panes are in
 * SETTINGS_UNAVAILABLE, so `.permission-row` never appears and the probe throws
 * at the first waitFor instead of passing on an empty list.
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

// ---- permissions: every capability renders from the server -----------------
step('permissions: navigate');
await navigate('permissions');
await waitFor(`document.querySelectorAll('.permission-row').length === 5`,
  'the five capabilities render as rows');
const capabilities = await evaluate(`[...document.querySelectorAll('.permission-row .permission-code')].map(el => el.textContent)`);
for (const cap of ['team.create','member.invite','task.delete','label.manage','extension.manage']) {
  assert.ok(capabilities.includes(cap), `capability ${cap} is listed (got ${JSON.stringify(capabilities)})`);
}
const before = await evaluate(`document.querySelector('input[aria-label="Members may create teams"]').checked`);
assert.equal(before, false, 'the default is admins-only (HOJA-490 seeds no grants)');

// ---- permissions: toggle, then prove the server holds it -------------------
step('permissions: toggle team.create');
await click('input[aria-label="Members may create teams"]');
await waitFor(`[...document.querySelectorAll('.settings-page [role=status]')].some(el => el.textContent.includes('Create teams'))`,
  'the pane reports the save');
await cdp('Page.reload');
await waitFor(`document.querySelectorAll('.permission-row').length === 5`, 'rows after reload');
const after = await evaluate(`document.querySelector('input[aria-label="Members may create teams"]').checked`);
assert.equal(after, true, 'the grant survives a reload (server round trip)');
const grants = await evaluate(`fetch('/graphql', {method: 'POST', headers: {'content-type': 'application/json', authorization: 'Bearer ' + localStorage.getItem('hoja_token')}, body: JSON.stringify({query: '{workspacePermissions{capability memberAllowed}}'})}).then(r => r.json())`);
const teamGrant = (grants?.data?.workspacePermissions || []).find(r => r.capability === 'team.create');
assert.equal(teamGrant?.memberAllowed, true, `the server, not the UI cache, holds the grant: ${JSON.stringify(grants).slice(0, 200)}`);

// put it back so the probe is repeatable
step('permissions: restore the default');
await click('input[aria-label="Members may create teams"]');
await waitFor(`document.querySelector('input[aria-label="Members may create teams"]').checked === false`,
  'the grant is revoked again');

// ---- security: the policy renders, saves, and is read back from the server -
step('security: navigate');
await navigate('security');
await waitFor(`!!document.querySelector('input[aria-label="Idle timeout minutes"]')`,
  'the security form renders');
await fill('input[aria-label="Idle timeout minutes"]', '45');
await fill('textarea[aria-label="IP allowlist"]', '10.0.0.0/8');
step('security: save');
await click('form.security-form button.primary');
await waitFor(`[...document.querySelectorAll('.settings-page [role=status]')].some(el => el.textContent.includes('Security policy saved'))`,
  'the pane reports the save');
await cdp('Page.reload');
await waitFor(`!!document.querySelector('input[aria-label="Idle timeout minutes"]')`, 'form after reload');
const idle = await evaluate(`document.querySelector('input[aria-label="Idle timeout minutes"]').value`);
assert.equal(idle, '45', 'the idle timeout survives a reload');
const policy = await evaluate(`fetch('/graphql', {method: 'POST', headers: {'content-type': 'application/json', authorization: 'Bearer ' + localStorage.getItem('hoja_token')}, body: JSON.stringify({query: '{workspaceSecurityPolicy{idleTimeoutMinutes ipAllowlist}}'})}).then(r => r.json())`);
assert.equal(policy?.data?.workspaceSecurityPolicy?.idleTimeoutMinutes, 45, `server holds the timeout: ${JSON.stringify(policy).slice(0, 200)}`);
assert.deepEqual(policy.data.workspaceSecurityPolicy.ipAllowlist, ['10.0.0.0/8'], 'server holds the allowlist');

// clear the policy so a re-run starts from the default, and prove clearing works
step('security: clear');
await evaluate(`document.querySelector('input[aria-label="Idle timeout minutes"]').focus(); document.execCommand?.('selectAll'); true`);
await evaluate(`(() => {const el = document.querySelector('input[aria-label="Idle timeout minutes"]');const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;setter.call(el, '');el.dispatchEvent(new Event('input', {bubbles: true}));return true;})()`);
await evaluate(`(() => {const el = document.querySelector('textarea[aria-label="IP allowlist"]');const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;setter.call(el, '');el.dispatchEvent(new Event('input', {bubbles: true}));return true;})()`);
await click('form.security-form button.primary');
await waitFor(`[...document.querySelectorAll('.settings-page [role=status]')].some(el => el.textContent.includes('Security policy saved'))`,
  'the cleared policy is acknowledged');
const cleared = await evaluate(`fetch('/graphql', {method: 'POST', headers: {'content-type': 'application/json', authorization: 'Bearer ' + localStorage.getItem('hoja_token')}, body: JSON.stringify({query: '{workspaceSecurityPolicy{idleTimeoutMinutes ipAllowlist}}'})}).then(r => r.json())`);
assert.equal(cleared?.data?.workspaceSecurityPolicy?.idleTimeoutMinutes, null, `clearing persists: ${JSON.stringify(cleared).slice(0, 200)}`);

console.log('ok  five capabilities rendered from the server, default admins-only');
console.log('ok  team.create grant toggled on, survived a reload, read back from the server, revoked');
console.log('ok  idle timeout 45 and IP allowlist 10.0.0.0/8 saved, survived a reload, read back from the server');
console.log('ok  clearing the policy persists');
console.log('pane probe ok: HOJA-490 panes render and persist');
ws.close();
process.exit(0);
