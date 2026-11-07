// Logic tests for the setup wizard, without a browser.
//
// The wizard's decisions live in DOM-free modules (flow, validation, draft,
// preview), transpiled here with the project's own TypeScript. That keeps the
// suite dependency-free like the other scripts/test-*.cjs files, and lets the
// same assertions run for both editions by pointing SETUP_ROOT at either tree:
//
//   node scripts/test-setup-wizard.cjs
//   SETUP_ROOT=../Hoja-Cloud/app/frontend/src/setup node scripts/test-setup-wizard.cjs
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const src = process.env.SETUP_ROOT
  ? path.resolve(process.env.SETUP_ROOT)
  : path.join(repo, 'frontend', 'src', 'setup');
const ts = require(path.join(repo, 'frontend', 'node_modules', 'typescript'));

const cache = new Map();
function loadTs(file) {
  const resolved = file.endsWith('.ts') ? file : `${file}.ts`;
  if (cache.has(resolved)) return cache.get(resolved).exports;
  const source = fs.readFileSync(resolved, 'utf8');
  const js = ts.transpileModule(source, {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022},
    fileName: resolved,
  }).outputText;
  const module = {exports: {}};
  cache.set(resolved, module);
  const localRequire = (specifier) => loadTs(path.resolve(path.dirname(resolved), specifier));
  new Function('exports', 'require', 'module', '__filename', '__dirname', js)(
    module.exports, localRequire, module, resolved, path.dirname(resolved),
  );
  return module.exports;
}

const flow = loadTs(path.join(src, 'flow'));
const validation = loadTs(path.join(src, 'validation'));
const draftModule = loadTs(path.join(src, 'draft'));
const previewModule = loadTs(path.join(src, 'preview'));

const checks = [];
function check(name, fn) { checks.push({name, fn}); }

// ------------------------------------------------------------------- flow ----
check('a first run walks account, workspace, team, then optional steps', () => {
  const steps = flow.buildSteps({
    mode: 'first-run', googleSignIn: false, ownerBound: false,
    workspaceCreated: false, teamExists: false, googleWorkspaceAvailable: true,
  });
  assert.deepEqual(steps, ['account', 'workspace', 'team', 'project', 'invitations', 'integrations', 'ready']);
});

check('an existing workspace skips account and workspace', () => {
  const steps = flow.buildSteps({
    mode: 'existing', googleSignIn: false, ownerBound: false,
    workspaceCreated: true, teamExists: true, googleWorkspaceAvailable: false,
  });
  assert.deepEqual(steps, ['project', 'invitations', 'ready']);
});

check('before a team exists the team step comes first, then project and invitations', () => {
  const steps = flow.buildSteps({
    mode: 'first-run', googleSignIn: false, ownerBound: false,
    workspaceCreated: true, teamExists: false, googleWorkspaceAvailable: false,
  });
  assert.deepEqual(steps, ['account', 'workspace', 'team', 'project', 'invitations', 'ready']);
  const created = flow.buildSteps({
    mode: 'first-run', googleSignIn: false, ownerBound: false,
    workspaceCreated: true, teamExists: true, googleWorkspaceAvailable: false,
  });
  assert.deepEqual(created, ['account', 'workspace', 'team', 'project', 'invitations', 'ready']);
  assert.equal(created.length, steps.length, 'the step count must not shrink after the write');
});

check('the integrations step disappears when the deployment cannot run it', () => {
  const withStep = flow.buildSteps({
    mode: 'first-run', googleSignIn: false, ownerBound: false,
    workspaceCreated: false, teamExists: false, googleWorkspaceAvailable: true,
  });
  const without = flow.buildSteps({
    mode: 'first-run', googleSignIn: false, ownerBound: false,
    workspaceCreated: false, teamExists: false, googleWorkspaceAvailable: false,
  });
  assert.ok(withStep.includes('integrations'));
  assert.ok(!without.includes('integrations'));
  assert.equal(without[without.length - 1], 'ready');
});

check('only optional steps can be skipped', () => {
  assert.equal(flow.canSkip('account'), false);
  assert.equal(flow.canSkip('workspace'), false);
  assert.equal(flow.canSkip('team'), false);
  assert.equal(flow.canSkip('ready'), false);
  assert.equal(flow.canSkip('project'), true);
  assert.equal(flow.canSkip('invitations'), true);
  assert.equal(flow.canSkip('integrations'), true);
});

check('back and forward stay inside the derived list', () => {
  const steps = ['account', 'workspace', 'team', 'ready'];
  assert.equal(flow.nextStep(steps, 'account'), 'workspace');
  assert.equal(flow.nextStep(steps, 'ready'), null);
  assert.equal(flow.previousStep(steps, 'account'), null);
  assert.equal(flow.previousStep(steps, 'team'), 'workspace');
});

check('a step already passed can be revisited, a later one cannot', () => {
  const steps = ['account', 'workspace', 'team', 'project', 'ready'];
  assert.equal(flow.canVisit(steps, 'project', 'workspace'), true);
  assert.equal(flow.canVisit(steps, 'workspace', 'project'), false);
  assert.equal(flow.canVisit(steps, 'project', 'not-a-step'), false);
});

check('progress counts the steps that actually exist', () => {
  const steps = ['account', 'team', 'ready'];
  assert.deepEqual(flow.progress(steps, 'team'), {index: 1, total: 3, label: 'Step 2 of 3'});
});

// ------------------------------------------------------------- validation ----
check('workspace names and addresses are checked before any request', () => {
  assert.equal(validation.checkWorkspaceName(''), 'Give your workspace a name.');
  assert.equal(validation.checkWorkspaceName('Acme'), '');
  assert.equal(validation.checkAddress(''), 'Choose an address for your workspace.');
  assert.equal(validation.checkAddress('acme-studio'), '');
  assert.notEqual(validation.checkAddress('Acme'), '');
  assert.notEqual(validation.checkAddress('-acme'), '');
  assert.notEqual(validation.checkAddress('acme_work'), '');
  assert.notEqual(validation.checkAddress('a'.repeat(64)), '');
});

check('team keys are limited to what becomes an issue prefix', () => {
  assert.equal(validation.checkTeamKey('ENG'), '');
  assert.equal(validation.checkTeamKey('ENG2'), '');
  assert.notEqual(validation.checkTeamKey(''), '');
  assert.notEqual(validation.checkTeamKey('TOOLONGKEY'), '');
  assert.equal(validation.normaliseTeamKey('eng-2'), 'ENG2');
});

check('deriving an address never leaves a trailing hyphen', () => {
  assert.equal(validation.deriveAddress('Acme Studio!'), 'acme-studio');
  assert.equal(validation.deriveAddress('  Acme   Studio  '), 'acme-studio');
  assert.equal(validation.deriveAddress('Acme (EU)'), 'acme-eu');
  assert.equal(validation.deriveAddress('***'), '');
});

check('a manually edited address survives a later rename', () => {
  const typed = validation.addressOnNameChange('Acme', '', false);
  assert.equal(typed, 'acme');
  const edited = 'acme-eu';
  assert.equal(validation.addressOnNameChange('Acme Europe', edited, true), 'acme-eu');
  assert.equal(validation.addressOnNameChange('Acme Europe', edited, false), 'acme-europe');
});

// ------------------------------------------------------------------ draft ----
function memoryStorage(seed = {}) {
  const values = new Map(Object.entries(seed));
  return {
    getItem: (key) => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => { values.set(key, value) },
    removeItem: (key) => { values.delete(key) },
    dump: () => Object.fromEntries(values),
  };
}

check('a draft round-trips the non-secret progress', () => {
  const storage = memoryStorage();
  const scope = {edition: 'oss'};
  const draft = draftModule.freshDraft('first-run');
  draft.workspaceName = 'Acme';
  draft.workspaceAddress = 'acme';
  draft.teamName = 'Engineering';
  draft.teamKey = 'ENG';
  draft.projectName = 'Launch';
  draft.invites = [{name: 'Sam', email: 'sam@example.com'}];
  draft.step = 'invitations';
  draft.completed = ['account', 'workspace', 'team'];
  draftModule.saveDraft(storage, scope, draft);
  const loaded = draftModule.loadDraft(storage, scope);
  assert.equal(loaded.workspaceName, 'Acme');
  assert.equal(loaded.step, 'invitations');
  assert.deepEqual(loaded.completed, ['account', 'workspace', 'team']);
  assert.deepEqual(loaded.invites, [{name: 'Sam', email: 'sam@example.com'}]);
});

check('secrets placed on a draft object are never written', () => {
  const storage = memoryStorage();
  const draft = draftModule.freshDraft('first-run');
  draft.password = 'correct horse battery staple';
  draft.apiKey = 'lin_api_secret';
  draft.code = 'invite-secret';
  draftModule.saveDraft(storage, {edition: 'oss'}, draft);
  const raw = Object.values(storage.dump()).join('');
  assert.ok(!raw.includes('correct horse'), 'password leaked into storage');
  assert.ok(!raw.includes('lin_api_secret'), 'api key leaked into storage');
  assert.ok(!raw.includes('invite-secret'), 'invite code leaked into storage');
  assert.ok(raw.includes('"version":1'));
});

check('storage is treated as untrusted input', () => {
  const scope = {edition: 'oss'};
  assert.equal(draftModule.loadDraft(memoryStorage({[`${draftModule.DRAFT_KEY}:oss`]: 'not json'}), scope), null);
  assert.equal(draftModule.loadDraft(memoryStorage({[`${draftModule.DRAFT_KEY}:oss`]: '{"version":99}'}), scope), null);
  assert.equal(draftModule.loadDraft(memoryStorage({[`${draftModule.DRAFT_KEY}:oss`]: '[1,2,3]'}), scope), null);
  const hostile = JSON.stringify({version: 1, mode: 'first-run', step: 'not-a-step', invites: 'nope', completed: ['bogus']});
  const loaded = draftModule.loadDraft(memoryStorage({[`${draftModule.DRAFT_KEY}:oss`]: hostile}), scope);
  assert.equal(loaded.step, 'account');
  assert.deepEqual(loaded.invites, []);
  assert.deepEqual(loaded.completed, []);
});

check('a draft for another workspace is discarded', () => {
  const storage = memoryStorage();
  const draft = draftModule.freshDraft('existing');
  draft.workspaceId = 'org-a';
  draftModule.saveDraft(storage, {edition: 'oss'}, draft);
  assert.ok(draftModule.loadDraft(storage, {edition: 'oss', workspaceId: 'org-a'}));
  assert.equal(draftModule.loadDraft(storage, {edition: 'oss', workspaceId: 'org-b'}), null);
});

check('the editions keep separate drafts', () => {
  const storage = memoryStorage();
  const draft = draftModule.freshDraft('first-run');
  draft.workspaceName = 'Oss';
  draftModule.saveDraft(storage, {edition: 'oss'}, draft);
  assert.equal(draftModule.loadDraft(storage, {edition: 'cloud'}), null);
});

check('clearing removes the draft', () => {
  const storage = memoryStorage();
  draftModule.saveDraft(storage, {edition: 'oss'}, draftModule.freshDraft('first-run'));
  draftModule.clearDraft(storage, {edition: 'oss'});
  assert.equal(draftModule.loadDraft(storage, {edition: 'oss'}), null);
});

// ---------------------------------------------------------------- preview ----
check('the preview confirms only what the server confirmed', () => {
  const draft = draftModule.freshDraft('first-run');
  draft.workspaceName = 'Acme';
  draft.workspaceAddress = 'acme';
  draft.teamName = 'Engineering';
  draft.teamKey = 'ENG';
  const typed = previewModule.buildPreview({draft, googleWorkspaceAvailable: true});
  const workspace = typed.rows.find((row) => row.label === 'Workspace');
  assert.equal(workspace.state, 'typed');
  assert.equal(typed.issueExample, 'ENG-1');

  draft.workspaceId = 'org-1';
  draft.teamId = 'team-1';
  const confirmed = previewModule.buildPreview({draft, googleWorkspaceAvailable: true});
  assert.equal(confirmed.rows.find((row) => row.label === 'Workspace').state, 'confirmed');
  assert.equal(confirmed.rows.find((row) => row.label === 'Team').state, 'confirmed');
});

check('nothing is shown as created before an id exists', () => {
  const draft = draftModule.freshDraft('first-run');
  const preview = previewModule.buildPreview({draft, googleWorkspaceAvailable: false});
  assert.ok(preview.rows.every((row) => row.state !== 'confirmed'));
  assert.equal(preview.rows.find((row) => row.label === 'Address').state, 'empty');
  assert.equal(preview.rows.find((row) => row.label === 'Google Workspace').state, 'unavailable');
});

check('the hosted domain is appended only when one is configured', () => {
  const draft = draftModule.freshDraft('first-run');
  draft.workspaceAddress = 'acme';
  const hosted = previewModule.buildPreview({draft, addressDomain: '.hoja.work', googleWorkspaceAvailable: false});
  const selfHosted = previewModule.buildPreview({draft, addressDomain: '', googleWorkspaceAvailable: false});
  assert.equal(hosted.rows.find((row) => row.label === 'Address').value, 'acme.hoja.work');
  assert.equal(selfHosted.rows.find((row) => row.label === 'Address').value, 'acme');
});

// ------------------------------------------------------------------- run ----
let failed = 0;
for (const {name, fn} of checks) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (error) {
    failed += 1;
    console.error(`not ok - ${name}`);
    console.error(`  ${error.message}`);
  }
}
console.log(`\n${checks.length - failed}/${checks.length} passed (${path.relative(repo, src) || src})`);
process.exit(failed ? 1 : 0);
