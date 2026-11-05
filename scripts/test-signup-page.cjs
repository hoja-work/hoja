// Tests for the /signup page's logic, without a browser.
//
// The page's decisions live in three DOM-free TypeScript modules
// (slugValidation, reasonCopy, submitSignup) which are transpiled here with the
// project's own TypeScript compiler and run in node. That is the repo's existing
// frontend test style (see scripts/test-*.cjs): no new runner, no new dependency.
//
//   node scripts/test-signup-page.cjs             # assertions
//   node scripts/test-signup-page.cjs --slug-check # stdin: JSON array of slugs
//                                                 # stdout: JSON array of reasons
//
// The --slug-check mode exists so tests/test_signup_page.py can feed the same
// cases to the real Python validate_slug and compare the answers: one corpus, two
// implementations, no second copy of the expected values.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const src = path.join(repo, 'frontend', 'src', 'signup');
const ts = require(path.join(repo, 'frontend', 'node_modules', 'typescript'));

const cache = new Map();
function loadTs(file) {
  const resolved = file.endsWith('.ts') ? file : `${file}.ts`;
  if (cache.has(resolved)) return cache.get(resolved).exports;
  const source = fs.readFileSync(resolved, 'utf8');
  const js = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: resolved,
  }).outputText;
  const module = { exports: {} };
  cache.set(resolved, module);
  const localRequire = (specifier) => loadTs(path.resolve(path.dirname(resolved), specifier));
  new Function('exports', 'require', 'module', '__filename', '__dirname', js)(
    module.exports, localRequire, module, resolved, path.dirname(resolved),
  );
  return module.exports;
}

const slug = loadTs(path.join(src, 'slugValidation'));
const copy = loadTs(path.join(src, 'reasonCopy'));
const submit = loadTs(path.join(src, 'submitSignup'));

if (process.argv.includes('--slug-check')) {
  const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
  const answers = cases.map((value) => {
    const result = slug.checkSlug(value);
    return result.ok ? null : result.reason;
  });
  process.stdout.write(JSON.stringify(answers));
  process.exit(0);
}

const checks = [];
function check(name, fn) { checks.push({ name, fn }); }

// ---------------------------------------------------------------- slug rule --
check('the rule comes from the generated server policy', () => {
  const policy = loadTs(path.join(src, 'slugPolicy.generated'));
  assert.equal(policy.SLUG_PATTERN, '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$');
  assert.equal(policy.SLUG_MAX_LENGTH, 63);
  assert.ok(policy.RESERVED_SLUGS.includes('www'), 'www is reserved');
  assert.ok(policy.RESERVED_SLUGS.length >= 27, `reserved list looks truncated: ${policy.RESERVED_SLUGS.length}`);
});

check('avoids the literal regex being retyped in the check module', () => {
  const source = fs.readFileSync(path.join(src, 'slugValidation.ts'), 'utf8');
  assert.ok(!source.includes('[a-z0-9-]{0,61}'), 'slugValidation.ts retypes the regex');
  assert.ok(source.includes("from './slugPolicy.generated'"), 'slugValidation.ts must import the generated rule');
});

check('accepts a normal label and trims it', () => {
  assert.deepEqual(slug.checkSlug('acme'), { ok: true, slug: 'acme' });
  assert.deepEqual(slug.checkSlug('  acme-studio  '), { ok: true, slug: 'acme-studio' });
  assert.deepEqual(slug.checkSlug('a'), { ok: true, slug: 'a' });
});

check('refuses empty, uppercase, reserved, punycode and hyphens', () => {
  assert.equal(slug.checkSlug('').reason, 'slug_required');
  assert.equal(slug.checkSlug('   ').reason, 'slug_required');
  assert.equal(slug.checkSlug(undefined).reason, 'slug_required');
  assert.equal(slug.checkSlug('Acme').reason, 'invalid_slug');
  assert.equal(slug.checkSlug('www').reason, 'reserved_slug');
  assert.equal(slug.checkSlug('xn--p1ai').reason, 'invalid_slug');
  assert.equal(slug.checkSlug('ac--me').reason, 'invalid_slug');
  assert.equal(slug.checkSlug('-acme').reason, 'invalid_slug');
  assert.equal(slug.checkSlug('acme-').reason, 'invalid_slug');
  assert.equal(slug.checkSlug('ac_me').reason, 'invalid_slug');
  assert.equal(slug.checkSlug('acme.work').reason, 'invalid_slug');
});

// -------------------------------------------------------------- reason copy --
check('every mapped code has a title and a message, and none is generic', () => {
  const codes = Object.keys(copy.REASON_COPY);
  assert.ok(codes.length >= 17, `expected the full list, got ${codes.length}`);
  for (const code of codes) {
    const entry = copy.REASON_COPY[code];
    assert.ok(entry.title && entry.title.trim().length > 3, `${code} has no title`);
    assert.ok(entry.message && entry.message.trim().length > 12, `${code} has no message`);
    assert.ok(!/an error occurred/i.test(entry.message), `${code} uses a generic message`);
  }
});

check('an unknown code falls back to the server message, verbatim', () => {
  const described = copy.describeReason('brand_new_code', '  The gate says no.  ');
  assert.equal(described.message, 'The gate says no.');
  assert.ok(described.title.includes('brand_new_code'));
});

check('an unknown code with no message still says something', () => {
  const described = copy.describeReason('brand_new_code', null);
  assert.ok(described.message.includes('brand_new_code'));
  assert.ok(described.message.trim().length > 20);
});

check('a known code uses the table, not the server wording', () => {
  const described = copy.describeReason('turnstile_failed', 'raw server text');
  assert.equal(described.message, copy.REASON_COPY.turnstile_failed.message);
  assert.notEqual(described.message, 'raw server text');
});

// ------------------------------------------------------------- submit handler --
function fakeFetch(reply) {
  const calls = [];
  const fetcher = async (input, init) => {
    calls.push({ input, init });
    if (reply instanceof Error) throw reply;
    return reply;
  };
  return { fetcher, calls };
}

function jsonResponse(body, status = 200) {
  return { status, json: async () => body };
}

check('posts {slug, turnstile_token} to /api/signup with credentials', async () => {
  const { fetcher, calls } = fakeFetch(jsonResponse({ ok: true, state: 'eligible', slug: 'acme', message: 'queued' }, 201));
  await submit.submitSignup({ slug: 'acme', token: 'turnstile-token-value', fetcher });
  assert.equal(calls.length, 1);
  const call = calls[0];
  assert.equal(call.input, '/api/signup');
  assert.equal(call.init.method, 'POST');
  assert.equal(call.init.credentials, 'include');
  assert.equal(call.init.headers['content-type'], 'application/json');
  const body = JSON.parse(call.init.body);
  assert.equal(body.slug, 'acme');
  assert.equal(body.turnstile_token, 'turnstile-token-value');
});

check('a queued answer reports the address and whether it was already queued', async () => {
  const { fetcher } = fakeFetch(jsonResponse({ ok: true, state: 'eligible', slug: 'acme', message: 'Your workspace request is queued.', alreadyEligible: false }, 201));
  const result = await submit.submitSignup({ slug: 'acme', token: 't', fetcher });
  assert.equal(result.kind, 'queued');
  assert.equal(result.slug, 'acme');
  assert.match(result.message, /queued/i);
  assert.equal(result.alreadyQueued, false);
});

check('a refusal surfaces the reason code and its human copy', async () => {
  const { fetcher } = fakeFetch(jsonResponse({
    ok: false, state: 'google_verified', reason: 'turnstile_failed',
    message: 'Human verification failed. Reload the page and try again.',
  }, 403));
  const result = await submit.submitSignup({ slug: 'acme', token: 't', fetcher });
  assert.equal(result.kind, 'refused');
  assert.equal(result.reason, 'turnstile_failed');
  assert.equal(result.message, copy.REASON_COPY.turnstile_failed.message);
  assert.equal(result.retryChallenge, true, 'a failed token must be replaced');
  assert.equal(result.retrySignIn, false);
});

check('every refusal the gate can send is classified, not left as a raw error', async () => {
  const codes = Object.keys(copy.REASON_COPY);
  for (const code of codes) {
    const { fetcher } = fakeFetch(jsonResponse({ ok: false, reason: code, message: `server: ${code}` }, 403));
    const result = await submit.submitSignup({ slug: 'acme', token: 't', fetcher });
    assert.equal(result.kind, 'refused', `${code} was not treated as a refusal`);
    assert.ok(result.message.trim().length > 0, `${code} produced no message`);
  }
});

check('not_authenticated asks for a new Google sign-in', async () => {
  const { fetcher } = fakeFetch(jsonResponse({ ok: false, reason: 'not_authenticated', message: 'Sign in with Google.' }, 401));
  const result = await submit.submitSignup({ slug: 'acme', token: 't', fetcher });
  assert.equal(result.retrySignIn, true);
  assert.equal(result.retryChallenge, false);
});

check('a network failure is a result, not a throw', async () => {
  const { fetcher } = fakeFetch(new Error('offline'));
  const result = await submit.submitSignup({ slug: 'acme', token: 't', fetcher });
  assert.equal(result.kind, 'unreachable');
  assert.match(result.message, /connection|reach/i);
});

check('a non-JSON answer names the status instead of saying "error"', async () => {
  const { fetcher } = fakeFetch({ status: 502, json: async () => { throw new Error('not json') } });
  const result = await submit.submitSignup({ slug: 'acme', token: 't', fetcher });
  assert.equal(result.kind, 'unreachable');
  assert.match(result.message, /502/);
});

(async () => {
  let failed = 0;
  for (const { name, fn } of checks) {
    try {
      await fn();
      console.log(`ok   ${name}`);
    } catch (error) {
      failed += 1;
      console.error(`FAIL ${name}\n     ${error.message}`);
    }
  }
  console.log(`\n${checks.length - failed}/${checks.length} signup-page checks passed`);
  process.exit(failed ? 1 : 0);
})();
