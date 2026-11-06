// Dependency-free browser verification through native Chrome DevTools Protocol.
// Requires a running Chromium --remote-debugging-port and a seeded Hoja server.
// Never imports Playwright. Mutations run only when HOJA_UI_ALLOW_MUTATIONS=1.
import assert from 'node:assert/strict';
import fs from 'node:fs';

const base = process.env.HOJA_UI_URL || 'http://127.0.0.1:18942';
const cdpUrl = process.env.HOJA_CDP_URL || 'http://127.0.0.1:19227';
const token = process.env.HOJA_UI_TOKEN;
if (!token) throw new Error('Set HOJA_UI_TOKEN for the disposable test workspace.');
const artifacts = process.env.HOJA_SETTINGS_ARTIFACTS || 'artifacts/settings-verified';
const allowMutations = process.env.HOJA_UI_ALLOW_MUTATIONS === '1';
fs.mkdirSync(artifacts, {recursive: true});

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
const exceptions = [];
ws.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  if (message.id) {
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id);
    if (message.error) request.reject(new Error(JSON.stringify(message.error)));
    else request.resolve(message.result);
  } else if (message.method === 'Runtime.exceptionThrown') exceptions.push(message.params.exceptionDetails);
});
function cdp(method, params = {}) {
  const id = ++sequence;
  return new Promise((resolve, reject) => {
    pending.set(id, {resolve, reject});
    ws.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const response = await cdp('Runtime.evaluate', {expression, returnByValue: true, awaitPromise: true, userGesture: true});
  if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
  return response.result.value;
}
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitFor(expression) {
  for (let i = 0; i < 300; i++) {
    if (await evaluate(expression)) return;
    await wait(400);
  }
  throw new Error(`Timed out: ${expression}`);
}
async function viewport(width, height = 900) {
  await cdp('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: width <= 700});
}
async function navigate(section) {
  await cdp('Page.navigate', {url: `${base}/workspace/settings/${section}`});
  await waitFor(`location.pathname === ${JSON.stringify(`/workspace/settings/${section}`)} && document.querySelectorAll('.settings-sidebar').length === 1`);
  await waitFor(`document.querySelector('.settings-workspace-name')?.textContent.trim().length > 0 && !document.querySelector('.settings-workspace-name')?.textContent.trim().startsWith('Settings')`);
  await wait(250);
}
async function shot(name) {
  const image = await cdp('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
  fs.writeFileSync(`${artifacts}/${name}.png`, Buffer.from(image.data, 'base64'));
}
async function click(selector) {
  const point = await evaluate(`(() => {const el = document.querySelector(${JSON.stringify(selector)});if (!el) return null;el.scrollIntoView({block:'center',inline:'center'});const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()`);
  assert.ok(point, `Click target: ${selector}`);
  await cdp('Input.dispatchMouseEvent', {type: 'mouseMoved', ...point});
  await cdp('Input.dispatchMouseEvent', {type: 'mousePressed', ...point, button: 'left', clickCount: 1});
  await cdp('Input.dispatchMouseEvent', {type: 'mouseReleased', ...point, button: 'left', clickCount: 1});
  await wait(120);
}
async function key(key, code = key) {
  const virtual = {Tab: 9, Enter: 13, Escape: 27, Space: 32, ArrowDown: 40, ArrowUp: 38, Home: 36, End: 35}[key];
  const domKey = key === 'Space' ? ' ' : key;
  const text = key === 'Enter' ? '\r' : key === 'Space' ? ' ' : undefined;
  await cdp('Input.dispatchKeyEvent', {type: 'keyDown', key: domKey, code, text, windowsVirtualKeyCode: virtual, nativeVirtualKeyCode: virtual});
  await cdp('Input.dispatchKeyEvent', {type: 'keyUp', key: domKey, code, windowsVirtualKeyCode: virtual, nativeVirtualKeyCode: virtual});
  await wait(100);
}
async function fill(selector, text) {
  await evaluate(`document.querySelector(${JSON.stringify(selector)}).focus();true`);
  await cdp('Input.insertText', {text});
}

const CONTRAST_SCAN = `(() => {
  const parse = input => {const m=input.match(/rgba?\\(([^)]+)\\)/);if(!m)return null;const p=m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number);return {r:p[0],g:p[1],b:p[2],a:p.length>3?p[3]:1};};
  const over = (fg,bg) => ({r:fg.r*fg.a+bg.r*(1-fg.a),g:fg.g*fg.a+bg.g*(1-fg.a),b:fg.b*fg.a+bg.b*(1-fg.a),a:1});
  const lum = c => {const f=v=>{v/=255;return v<=.03928?v/12.92:Math.pow((v+.055)/1.055,2.4)};return .2126*f(c.r)+.7152*f(c.g)+.0722*f(c.b)};
  const background = el => {const layers=[];for(let e=el;e;e=e.parentElement){const c=parse(getComputedStyle(e).backgroundColor);if(c&&c.a>0){layers.push(c);if(c.a>=1)break}}let out={r:255,g:255,b:255,a:1};for(const c of layers.reverse())out=over(c,out);return out;};
  const failures=[];
  for(const el of document.querySelectorAll('.settings-page *, .settings-sidebar *')){
    if(el.closest(':disabled') || el.closest('[aria-hidden=true]'))continue;
    const text=[...el.childNodes].filter(n=>n.nodeType===3).map(n=>n.textContent.trim()).join(' ').trim();
    if(!text)continue;
    const r=el.getBoundingClientRect(),cs=getComputedStyle(el);if(!r.width||!r.height||cs.display==='none'||cs.visibility==='hidden'||cs.opacity==='0')continue;
    const fg=parse(cs.color);if(!fg)continue;const bg=background(el),f=over(fg,bg),a=lum(f),b=lum(bg),ratio=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    if(ratio<4.5)failures.push({text:text.slice(0,45),tag:el.tagName,class:typeof el.className==='string'?el.className:'',ratio:Math.round(ratio*100)/100,color:cs.color});
  }
  return failures;
})()`;

await cdp('Page.enable');
await cdp('Runtime.enable');
await viewport(1440, 1000);
await cdp('Page.navigate', {url: base});
await evaluate(`localStorage.setItem('hoja_token',${JSON.stringify(token)});localStorage.setItem('theme','light');true`);
const sections = ['preferences','profile','notifications','api-keys','agent-conversations','connections','issue-labels','workspace','teams','members','model-providers'];
const report = {routes: [], contrast: [], geometry: [], interactions: {}, mutations: 'skipped'};

for (const theme of ['light','dark']) {
  await navigate('preferences');
  await click(theme === 'light' ? '.setting-control button:first-child' : '.setting-control button:nth-child(2)');
  await waitFor(`document.documentElement.dataset.theme === ${JSON.stringify(theme)}`);
  await waitFor(`[...document.querySelectorAll('.settings-page [role=status]')].some(el=>el.textContent.includes('Saved'))`);
  for (const section of sections) {
    await navigate(section);
    await evaluate(`document.getAnimations().forEach(animation=>animation.finish());true`);
    await evaluate(`new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))`);
    const result = await evaluate(`(() => {
      const page=document.querySelector('.settings-page');
      const controls=[...document.querySelectorAll('.settings-page button,.settings-page input,.settings-page select,.settings-page a')].filter(el=>{const r=el.getBoundingClientRect(),cs=getComputedStyle(el);return r.width&&r.height&&cs.display!=='none'&&cs.visibility!=='hidden'}).map(el=>{const r=el.getBoundingClientRect();return {name:el.getAttribute('aria-label')||el.textContent.trim().slice(0,30),left:r.left,right:r.right};});
      return {heading:document.querySelector('.settings-page h1')?.textContent,theme:document.documentElement.dataset.theme,scrollWidth:document.documentElement.scrollWidth,viewport:innerWidth,controls,contrast:${CONTRAST_SCAN}};
    })()`);
    assert.ok(result.heading, `${section}: visible page heading`);
    assert.equal(result.theme, theme);
    assert.ok(result.scrollWidth <= result.viewport + 1, `${section}/${theme}: ${result.scrollWidth}px document in ${result.viewport}px viewport`);
    assert.ok(result.controls.every(c => c.left >= -1 && c.right <= result.viewport + 1), `${section}/${theme}: control escapes viewport ${JSON.stringify(result.controls.filter(c => c.left < -1 || c.right > result.viewport + 1))}`);
    assert.deepEqual(result.contrast, [], `${section}/${theme}: text contrast ${JSON.stringify(result.contrast)}`);
    report.routes.push({section, theme, heading: result.heading});
    report.contrast.push({section, theme, failures: result.contrast.length});
    report.geometry.push({section, theme, width: result.scrollWidth, viewport: result.viewport, controls: result.controls.length});
  }
  await shot(`desktop-${theme}-model-providers`);
}

await navigate('preferences');
await click('.setting-control button:first-child');
await waitFor(`document.documentElement.dataset.theme === 'light'`);
await click('.setting-control button:nth-child(2)');
await waitFor(`document.documentElement.dataset.theme === 'dark'`);
await waitFor(`[...document.querySelectorAll('.settings-page [role=status]')].some(el=>el.textContent.includes('Saved'))`);
assert.equal(await evaluate(`localStorage.getItem('theme')`), 'dark');
await evaluate(`document.querySelector('.setting-control button:first-child').focus();true`);
await key('Enter');
await waitFor(`document.documentElement.dataset.theme === 'light'`);
report.interactions.keyboardTheme = true;

// Context-rich select: native keyboard exploration, Escape cancellation,
// typeahead, contextual copy, persistence, and real server-backed restoration.
const fontSelect = '[role=combobox][aria-label="Font size"]';
await click(fontSelect);
assert.match(await evaluate(`document.querySelector('[role=listbox][aria-label="Font size"]').textContent`), /The balanced Hoja reading size/);
assert.equal(await evaluate(`document.querySelector('[role=option][data-value="100"]').getAttribute('aria-selected')`), 'true');
await key('ArrowDown');
assert.equal(await evaluate(`document.querySelector('[role=option][data-active=true]').dataset.value`), '110');
await key('Enter');
await waitFor(`document.documentElement.style.getPropertyValue('--font-scale') === '1.1'`);
await waitFor(`[...document.querySelectorAll('.settings-page [role=status]')].some(el=>el.textContent.includes('Saved'))`);
await key('ArrowDown');
await key('Home');
assert.equal(await evaluate(`document.querySelector('[role=option][data-active=true]').dataset.value`), '90');
await key('Escape');
assert.equal(await evaluate(`document.querySelector('[role=combobox][aria-label="Font size"] .settings-select-value b').textContent`), 'Large');
await key('c');
await key('Enter');
await waitFor(`document.documentElement.style.getPropertyValue('--font-scale') === '0.9'`);
await key('d');
await key('Enter');
await waitFor(`document.documentElement.style.getPropertyValue('--font-scale') === '1'`);
await click(fontSelect);
await shot('desktop-font-dropdown');
await key('Escape');
report.interactions.dropdownKeyboardAndTypeahead = true;

await evaluate(`document.querySelector('.settings-search input').focus();true`);
await cdp('Input.insertText', {text: 'model'});
await waitFor(`document.querySelectorAll('.settings-sidebar .nav-item').length === 1`);
assert.equal(await evaluate(`document.querySelector('.settings-sidebar .nav-item span').textContent`), 'Model providers');
await click('.settings-search > button');
assert.ok(await evaluate(`document.querySelectorAll('.settings-sidebar .nav-item').length > 5`));
await fill('.settings-search input', 'no-such-setting');
await waitFor(`document.querySelector('.settings-search-empty') !== null`);
await click('.settings-search-empty button');
report.interactions.searchAndEmpty = true;

await navigate('notifications');
const beforeSwitch = await evaluate(`document.querySelector('.setting-switch').checked`);
await evaluate(`document.querySelector('.setting-switch').focus();true`);
await key('Space', 'Space');
await waitFor(`document.querySelector('.setting-switch').checked === ${!beforeSwitch}`);
await waitFor(`[...document.querySelectorAll('.settings-page [role=status]')].some(el=>el.textContent.includes('saved'))`);
await key('Space', 'Space');
await waitFor(`document.querySelector('.setting-switch').checked === ${beforeSwitch}`);
report.interactions.nativeSwitchKeyboard = true;

await navigate('api-keys');
await click('[role=combobox][aria-label="API key access"]');
assert.match(await evaluate(`document.querySelector('[role=listbox][aria-label="API key access"]').textContent`), /View data without making changes/);
await key('ArrowDown');
await key('Enter');
assert.equal(await evaluate(`document.querySelector('[aria-label="API key access"] .settings-select-value b').textContent`), 'Read only');
await key('Home');
await key('Enter');
assert.equal(await evaluate(`document.querySelector('[aria-label="API key access"] .settings-select-value b').textContent`), 'Read and write');
await click('[role=combobox][aria-label="API key expiration"]');
assert.match(await evaluate(`document.querySelector('[role=listbox][aria-label="API key expiration"]').textContent`), /Valid until/);
assert.match(await evaluate(`document.querySelector('[role=listbox][aria-label="API key expiration"]').textContent`), /Stays valid until you revoke it/);
const desktopMenu = await evaluate(`document.querySelector('[role=listbox][aria-label="API key expiration"]').getBoundingClientRect().toJSON()`);
assert.ok(desktopMenu.left >= 0 && desktopMenu.right <= 1440 && desktopMenu.top >= 0 && desktopMenu.bottom <= 1000, 'Desktop menu is placed inside the viewport.');
await shot('desktop-api-expiration-dropdown');
await key('Escape');
report.interactions.dropdownCredentialContext = true;
await click('.api-key-row > button.outline');
assert.equal(await evaluate(`document.querySelectorAll('.api-key-confirm').length`), 1);
await click('.api-key-confirm .outline');
assert.equal(await evaluate(`document.querySelectorAll('.api-key-confirm').length`), 0);
report.interactions.revokeCancel = true;

await navigate('teams');
assert.equal(await evaluate(`document.querySelector('[aria-label="Add a member to Engineering"]').getAttribute('aria-disabled')`), 'true');
await click('[aria-label="Add a member to Engineering"]');
assert.equal(await evaluate(`document.querySelectorAll('[role=listbox]').length`), 0, 'Exhausted team membership does not open an empty menu.');
await click('.team-card-actions .outline.danger');
assert.equal(await evaluate(`document.querySelector('.team-card-delete button.danger').disabled`), true);
await fill('.team-card-delete-confirm input', 'ENG');
assert.equal(await evaluate(`document.querySelector('.team-card-delete button.danger').disabled`), false);
await click('.team-card-delete .outline');
report.interactions.teamDeleteGate = true;

await navigate('members');
await click('[role=combobox][aria-label^="Role for "]');
assert.match(await evaluate(`document.querySelector('.settings-select-panel[role=listbox]').textContent`), /Manage the workspace, members, and teams/);
const roleBefore = await evaluate(`document.querySelector('[role=combobox][aria-label^="Role for "] .settings-select-value b').textContent`);
await key('Escape');
assert.equal(await evaluate(`document.querySelector('[role=combobox][aria-label^="Role for "] .settings-select-value b').textContent`), roleBefore, 'Escape does not change a workspace role.');
report.interactions.dropdownRoleContext = true;

if (allowMutations) {
  const verificationLabel = `UI verification ${Date.now()}`;
  await navigate('api-keys');
  await fill('input[aria-label="API key name"]', verificationLabel);
  await click('.api-key-create > button');
  await waitFor(`document.querySelector('.api-key-secret code')?.textContent.length > 20`);
  const leak = await evaluate(`Object.values(localStorage).some(value=>value.includes(document.querySelector('.api-key-secret code').textContent))`);
  assert.equal(leak, false, 'The one-time secret is not persisted in localStorage.');
  await click('.api-key-secret .auth-switch');
  const label = JSON.stringify(verificationLabel);
  const created = await evaluate(`[...document.querySelectorAll('.api-key-row')].findIndex(row=>row.textContent.includes(${label}))`);
  assert.ok(created >= 0, 'Created key appears in inventory.');
  await evaluate(`[...document.querySelectorAll('.api-key-row')].find(row=>row.textContent.includes(${label})&&row.querySelector('button'))?.querySelector('button').click();true`);
  await waitFor(`document.querySelector('.api-key-confirm') !== null`);
  await click('.api-key-confirm button.danger');
  await waitFor(`[...document.querySelectorAll('.api-key-row')].some(row=>row.textContent.includes(${label})&&row.textContent.includes('Revoked'))`);
  report.mutations = 'create/reveal/dismiss/revoke passed; raw secret not logged or persisted';
}

await viewport(390, 844);
for (const section of sections) {
  await navigate(section);
  const geometry = await evaluate(`(() => {const page=document.querySelector('.settings-page').getBoundingClientRect();return {width:innerWidth,scrollWidth:document.documentElement.scrollWidth,left:page.left,right:page.right}})()`);
  assert.ok(geometry.scrollWidth <= geometry.width + 1, `mobile ${section}: page overflow`);
  assert.ok(geometry.left >= 0 && geometry.right <= 390, `mobile ${section}: settings page escapes viewport`);
}
await navigate('preferences');
await click(fontSelect);
const mobileMenu = await evaluate(`document.querySelector('[role=listbox][aria-label="Font size"]').getBoundingClientRect().toJSON()`);
assert.ok(mobileMenu.left >= 0 && mobileMenu.right <= 390 && mobileMenu.top >= 0 && mobileMenu.bottom <= 844, 'Mobile menu is placed inside the viewport.');
await key('End');
assert.equal(await evaluate(`document.querySelector('[role=option][data-active=true]').dataset.value`), '110');
await key('Escape');
report.interactions.dropdownMobilePlacement = true;
await click('.mobile-nav-toggle');
assert.equal(await evaluate(`document.querySelector('.settings-sidebar').getAttribute('role')`), 'dialog');
assert.equal(await evaluate(`document.activeElement.classList.contains('settings-drawer-close')`), true);
await key('Escape');
assert.equal(await evaluate(`document.querySelector('.settings-sidebar').classList.contains('open')`), false);
assert.equal(await evaluate(`document.activeElement.classList.contains('mobile-nav-toggle')`), true);
await shot('mobile-preferences');
await cdp('Emulation.setEmulatedMedia', {features: [{name: 'prefers-reduced-motion', value: 'reduce'}]});
const duration = await evaluate(`getComputedStyle(document.querySelector('.settings-sidebar')).transitionDuration`);
assert.equal(duration, '0s');
await cdp('Emulation.setEmulatedMedia', {features: []});
report.interactions.mobileDrawerKeyboard = true;
report.interactions.reducedMotion = duration;

await navigate('api-keys');
await shot('mobile-light-api-keys');
await navigate('preferences');
await click('.setting-control button:nth-child(2)');
await waitFor(`document.documentElement.dataset.theme === 'dark'`);
await navigate('api-keys');
await shot('mobile-dark-api-keys');
assert.deepEqual(exceptions, [], 'No browser runtime exceptions.');
report.runtimeExceptions = exceptions.length;
console.log(JSON.stringify(report, null, 2));
ws.close();
