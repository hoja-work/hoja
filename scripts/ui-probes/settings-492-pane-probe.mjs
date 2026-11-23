#!/usr/bin/env node
/**
 * HOJA-492 pane probe: the task-templates settings pane renders a real store,
 * and a task created from a template is the same task whichever client asked.
 *
 * Dependency-free CDP, same contract as settings-489-pane-probe.mjs:
 *   HOJA_UI_URL   seeded server that serves the built SPA   (default :18942)
 *   HOJA_CDP_URL  Chromium with --remote-debugging-port     (default :19227)
 *   HOJA_UI_TOKEN API token for the seeded workspace
 *
 * Fail-before: on a pristine tree the pane is in SETTINGS_UNAVAILABLE, so the
 * form never renders and waitFor throws instead of passing on an empty page.
 *
 * The last section is the acceptance sentence itself — "creating a task from a
 * template in the web client and through the GraphQL API produces the same
 * field values" — measured as two different API surfaces (`issueCreate`, which
 * the pane and web client use, and the `taskCreate` alias the CLI uses) plus a
 * third call that sends the values the pane prefills, the way a human saving
 * the form would.
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
const gqlFromPage = (query, variables = {}) => evaluate(
  `fetch('/graphql', {method: 'POST', credentials: 'include', headers: {'content-type': 'application/json', authorization: 'Bearer ' + localStorage.getItem('hoja_token')}, body: JSON.stringify(${JSON.stringify({query, variables})})}).then(r => r.json())`);

await cdp('Page.enable');
await cdp('Runtime.enable');
await cdp('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false});
await cdp('Page.navigate', {url: base});
await evaluate(`localStorage.setItem('hoja_token', ${JSON.stringify(token)}); localStorage.setItem('theme', 'light'); true`);

step('nav: the pane is offered, not marked unavailable');
await navigate('issue-templates');
await waitFor(`!!document.querySelector('[data-nav-id="issue-templates"]')`, 'the Issue → Task templates nav entry renders');
const stillMarked = await evaluate(`!!document.querySelector('[data-nav-id="issue-templates"][data-availability="unavailable"]')`);
assert.equal(stillMarked, false, 'the nav entry is no longer marked unavailable');
await waitFor(`!!document.querySelector('form.task-template-form')`, 'the task-template form renders (a real store, not an unavailable notice)');
assert.equal(await evaluate(`!!document.querySelector('[data-settings-pane="unavailable"]')`), false,
  'the pane is the real screen, not the "what this screen needs" placeholder');

step('template: create through the UI, scoped to a team');
const stamp = Date.now();
const templateName = `Incident ${stamp}`;
const prefix = `[Incident ${stamp}] `;
const teamList = await gqlFromPage('{teams{nodes{id key name}}}');
const scopedTeam = teamList.data.teams.nodes[0];
assert.ok(scopedTeam, `the workspace has a team to scope to: ${JSON.stringify(teamList).slice(0, 200)}`);
await fill('input[aria-label="Task template name"]', templateName);
await setSelect('select[aria-label="Task template scope"]', scopedTeam.id);
await fill('input[aria-label="Default title"]', prefix);
await setSelect('select[aria-label="Default priority"]', '1');
await click('form.task-template-form button.primary');
await waitFor(`[...document.querySelectorAll('.task-template-row b')].some(el => el.textContent === ${JSON.stringify(templateName)})`,
  'the created template appears in the list');
await waitFor(`[...document.querySelectorAll('.settings-page [role=status]')].some(el => el.textContent.includes('Template created'))`,
  'the pane reports the save');

step('template: survives a reload (server round trip, not UI state)');
await cdp('Page.reload');
await waitFor(`[...document.querySelectorAll('.task-template-row b')].some(el => el.textContent === ${JSON.stringify(templateName)})`,
  'the created template survives a reload');

step('acceptance: issueCreate with templateId only');
const team = scopedTeam;
const templates = await gqlFromPage('{taskTemplates{id name team{key} defaultTitle defaultPriority}}');
const template = templates.data.taskTemplates.find(t => t.name === templateName);
assert.ok(template, `the server lists the template the UI created: ${JSON.stringify(templates).slice(0, 200)}`);

const viaClient = await gqlFromPage(
  'mutation($i:IssueCreateInput!){issueCreate(input:$i){success issue{id title priority team{key}}}}',
  {i: {templateId: template.id}});
assert.ok(viaClient?.data?.issueCreate?.success, `issueCreate expanded the template: ${JSON.stringify(viaClient).slice(0, 300)}`);
const task = viaClient.data.issueCreate.issue;
assert.equal(task.title, prefix, 'the task carries the template title verbatim, trailing space included');
assert.equal(task.priority, 1, 'the task carries the template priority');
assert.equal(task.team.key, team.key, 'the task lands in the template team without the caller naming it');

step('acceptance: the CLI alias (taskCreate) produces the same task');
const viaCli = await gqlFromPage(
  'mutation($i:TaskCreateInput!){taskCreate(input:$i){success task{id title priority team{key}}}}',
  {i: {templateId: template.id}});
assert.ok(viaCli?.data?.taskCreate?.success, `taskCreate expanded the template: ${JSON.stringify(viaCli).slice(0, 300)}`);
const cliTask = viaCli.data.taskCreate.task;
for (const field of ['title', 'priority']) {
  assert.equal(cliTask[field], task[field], `two clients agree on ${field}: ${JSON.stringify({ui: task[field], cli: cliTask[field]})}`);
}
assert.equal(cliTask.team.key, task.team.key, 'two clients agree on the team');

step('acceptance: the pane\'s prefilled payload produces the same task');
const prefilled = await gqlFromPage(
  'mutation($i:IssueCreateInput!){issueCreate(input:$i){success issue{id title priority team{key}}}}',
  {i: {templateId: template.id, title: prefix, priority: 1}});
assert.ok(prefilled?.data?.issueCreate?.success, JSON.stringify(prefilled).slice(0, 300));
const saved = prefilled.data.issueCreate.issue;
assert.equal(saved.title, task.title, 'a form-saved task equals an expanded one');
assert.equal(saved.priority, task.priority, 'a form-saved priority equals an expanded one');

step('acceptance: omitting both title and template is refused, by name');
const bare = await gqlFromPage('mutation($i:IssueCreateInput!){issueCreate(input:$i){success issue{id}}}', {i: {teamId: team.id}});
assert.ok(bare.errors && /title/.test(JSON.stringify(bare.errors)),
  `a title-less create is refused naming the field: ${JSON.stringify(bare).slice(0, 200)}`);

step('decision: a workspace-level template refuses without a team, by name');
const wsTemplate = await gqlFromPage(
  'mutation($i:TaskTemplateInput!){taskTemplateCreate(input:$i){success taskTemplate{id name}}}',
  {i: {name: `Workspace ${stamp}`, defaultTitle: 'WS '}});
const wsId = wsTemplate?.data?.taskTemplateCreate?.taskTemplate?.id;
assert.ok(wsId, JSON.stringify(wsTemplate).slice(0, 200));
const wsNoTeam = await gqlFromPage('mutation($i:IssueCreateInput!){issueCreate(input:$i){success issue{id}}}', {i: {templateId: wsId}});
assert.ok(wsNoTeam.errors && /teamId/.test(JSON.stringify(wsNoTeam.errors)),
  `a workspace template cannot invent a team: ${JSON.stringify(wsNoTeam).slice(0, 200)}`);
const wsWithTeam = await gqlFromPage(
  'mutation($i:IssueCreateInput!){issueCreate(input:$i){success issue{title team{key}}}}',
  {i: {templateId: wsId, teamId: team.id}});
assert.ok(wsWithTeam?.data?.issueCreate?.success, JSON.stringify(wsWithTeam).slice(0, 200));
assert.equal(wsWithTeam.data.issueCreate.issue.title, 'WS ', 'workspace template supplies the title in the named team');

console.log(`ok  pane rendered and the nav entry is offered (not marked unavailable)`);
console.log(`ok  template ${templateName} created through the UI and survived a reload`);
console.log(`ok  issueCreate(templateId) -> ${JSON.stringify({title: task.title, priority: task.priority, team: task.team.key})}`);
console.log(`ok  taskCreate alias agrees field-for-field with the web client's mutation`);
console.log(`ok  a form-saved task equals an expanded one; a title-less create names the field`);
console.log('pane probe ok: HOJA-492 pane renders, persists, and expands identically for two clients');
ws.close();
process.exit(0);
