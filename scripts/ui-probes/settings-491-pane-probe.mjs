#!/usr/bin/env node
/**
 * HOJA-491 pane probe: the SLAs pane manages policies the server enforces, and a
 * task created under a policy reports its clock and breach state through GraphQL.
 *
 * Fail-before: on a pristine tree the pane is in SETTINGS_UNAVAILABLE, so the
 * policy form never renders and the probe asserts that instead of passing.
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
await new Promise((resolve, reject) => { ws.addEventListener('open', resolve, {once: true}); ws.addEventListener('error', reject, {once: true}); });
let sequence = 0; const pending = new Map();
ws.addEventListener('message', event => { const m = JSON.parse(event.data); if (!m.id) return; const p = pending.get(m.id); if (!p) return; pending.delete(m.id); m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result); });
const cdp = (method, params = {}) => new Promise((resolve, reject) => { const id = ++sequence; const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 15000); pending.set(id, {resolve: v => { clearTimeout(timer); resolve(v); }, reject: e => { clearTimeout(timer); reject(e); }}); ws.send(JSON.stringify({id, method, params})); });
const step = l => console.log(`-- ${l}`);
async function evaluate(expression) { const r = await cdp('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true, userGesture: true}); if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails)); return r.result.value; }
const wait = ms => new Promise(r => setTimeout(r, ms));
setTimeout(() => { console.error('watchdog: probe exceeded 240s'); process.exit(3); }, 240000).unref();
async function waitFor(expression, label) { for (let i = 0; i < 150; i++) { if (await evaluate(expression)) return; await wait(200); } throw new Error(`Timed out: ${label || expression}`); }
async function navigate(section) { await cdp('Page.navigate', {url: `${base}/workspace/settings/${section}`}); await waitFor(`document.querySelectorAll('.settings-sidebar').length === 1`, `${section}: shell`); await wait(400); }
async function fill(selector, text) { await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus();true`); await cdp('Input.insertText', {text}); }
async function click(selector) { const point = await evaluate(`(() => {const el = document.querySelector(${JSON.stringify(selector)});if (!el) return null;el.scrollIntoView({block:'center'});const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`); assert.ok(point, `Click target: ${selector}`); await cdp('Input.dispatchMouseEvent', {type: 'mousePressed', ...point, button: 'left', clickCount: 1}); await cdp('Input.dispatchMouseEvent', {type: 'mouseReleased', ...point, button: 'left', clickCount: 1}); await wait(150); }
async function setSelect(selector, value) { await evaluate(`(() => {const el = document.querySelector(${JSON.stringify(selector)});const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set;setter.call(el, ${JSON.stringify(value)});el.dispatchEvent(new Event('change', {bubbles: true}));return true;})()`); }
const gql = (query, variables = {}) => evaluate(`fetch('/graphql', {method: 'POST', credentials: 'include', headers: {'content-type': 'application/json', authorization: 'Bearer ' + localStorage.getItem('hoja_token')}, body: JSON.stringify(${JSON.stringify({query, variables})})}).then(r => r.json())`);

await cdp('Page.enable'); await cdp('Runtime.enable');
await cdp('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false});
await cdp('Page.navigate', {url: base});
await evaluate(`localStorage.setItem('hoja_token', ${JSON.stringify(token)}); localStorage.setItem('theme', 'light'); true`);

step('nav: the pane is offered, not marked unavailable');
await navigate('slas');
await waitFor(`!!document.querySelector('[data-nav-id="slas"]')`, 'the Issues → SLAs nav entry renders');
assert.equal(await evaluate(`!!document.querySelector('[data-nav-id="slas"][data-availability="unavailable"]')`), false, 'the nav entry is no longer marked unavailable');
await waitFor(`!!document.querySelector('form.sla-policy-form')`, 'the SLA policy form renders');
assert.equal(await evaluate(`!!document.querySelector('[data-settings-pane="unavailable"]')`), false, 'the pane is the real screen');

step('policy: create through the UI');
const teams = await gql('{teams{nodes{id key name}}}');
const team = teams.data.teams.nodes[0];
const name = `Standard ${Date.now()}`;
await fill('input[aria-label="SLA policy name"]', name);
await setSelect('select[aria-label="SLA policy scope"]', team.id);
await fill('input[aria-label="First response minutes"]', '60');
await fill('input[aria-label="Resolution minutes"]', '5');
await click('form.sla-policy-form button.primary');
await waitFor(`[...document.querySelectorAll('.sla-policy-row b')].some(el => el.textContent === ${JSON.stringify(name)})`, 'the created policy appears');
await waitFor(`[...document.querySelectorAll('.settings-page [role=status]')].some(el => el.textContent.includes('Policy created'))`, 'the pane reports the save');
await cdp('Page.reload');
await waitFor(`[...document.querySelectorAll('.sla-policy-row b')].some(el => el.textContent === ${JSON.stringify(name)})`, 'the policy survives a reload');

step('acceptance: a task under the policy reports its clock through GraphQL');
const made = await gql('mutation($i:IssueCreateInput!){issueCreate(input:$i){success issue{id identifier sla{state overdueMinutes resolutionDueAt firstResponseDueAt firstRespondedAt policy{name}}}}}', {i: {teamId: team.id, title: `SLA probe ${Date.now()}`}});
assert.ok(made?.data?.issueCreate?.success, JSON.stringify(made).slice(0, 300));
const clock = made.data.issueCreate.issue.sla;
assert.ok(clock, 'the task reports a clock');
assert.equal(clock.policy.name, name, 'the clock names the policy the pane created');
assert.equal(clock.state, 'ok', 'a fresh task is not overdue');
assert.ok(clock.resolutionDueAt && clock.firstResponseDueAt, 'both deadlines are reported');
assert.equal(clock.firstRespondedAt, null, 'nobody has responded yet');

step('acceptance: the state changes on the documented events');
const states = await gql(`{teams{nodes{states{nodes{id type name}}}}}`);
const list = states.data.teams.nodes[0].states.nodes;
const started = list.find(s => s.type === 'started'), completed = list.find(s => s.type === 'completed');
const moved = await gql('mutation($id:String!,$i:IssueUpdateInput!){issueUpdate(id:$id,input:$i){success}}', {id: made.data.issueCreate.issue.id, i: {stateId: started.id}});
assert.ok(moved?.data?.issueUpdate?.success, JSON.stringify(moved).slice(0, 200));
const done = await gql('mutation($id:String!,$i:IssueUpdateInput!){issueUpdate(id:$id,input:$i){success}}', {id: made.data.issueCreate.issue.id, i: {stateId: completed.id}});
assert.ok(done?.data?.issueUpdate?.success, JSON.stringify(done).slice(0, 200));
const after = await gql('query($ref:String!){issue(id:$ref){sla{state resolvedAt}}}', {ref: made.data.issueCreate.issue.id});
assert.ok(after.data.issue.sla.resolvedAt, 'a completed task stops its clock');

console.log(`ok  pane rendered and the nav entry is offered`);
console.log(`ok  policy ${name} created through the UI and survived a reload`);
console.log(`ok  task clock: state=${clock.state} resolutionDue=${clock.resolutionDueAt} policy=${clock.policy.name}`);
console.log(`ok  clock stops on completion (resolvedAt set)`);
console.log('pane probe ok: HOJA-491 pane renders, persists, and the server reports the clock');
ws.close(); process.exit(0);
