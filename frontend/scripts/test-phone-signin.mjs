#!/usr/bin/env node
/**
 * The "Sign in on your phone" state machine (HOJA-733), driven with a fake `fetch` and fake
 * timers. The wire contract is docs/mobile-signin.md section 1; src/PhoneSignIn.tsx renders it.
 *
 *   node --experimental-strip-types scripts/test-phone-signin.mjs
 */
import { PhoneSignInController, formatCode, messageFor } from '../src/phoneSignIn.ts';

/** Timers that only fire when the test says so. */
function fakeTimers() {
  let next = 1;
  const pending = new Map();
  return {
    pending,
    setTimeout: (fn, ms) => {
      const h = next++;
      pending.set(h, { fn, ms });
      return h;
    },
    clearTimeout: (h) => pending.delete(h),
    /** Fire every timer scheduled with exactly `ms`. */
    fire(ms) {
      const due = [...pending].filter(([, t]) => t.ms === ms);
      for (const [h, t] of due) {
        pending.delete(h);
        t.fn();
      }
      return due.length;
    },
  };
}

/** A scripted server: each route returns the next queued response, or a default. */
function fakeServer() {
  const calls = [];
  const routes = new Map();
  const reply = (status, body) => ({ ok: status >= 200 && status < 300, status, json: async () => body });
  let seq = 0;
  const handoff = (over = {}) => {
    seq++;
    return { id: `h${seq}`, secret: `s${seq}`, code: 'BCDFGHJK', qrUrl: `https://acme.hoja.work/m/pair#h${seq}.s${seq}`, expiresAt: '2026-11-23T12:05:00Z', rotateAfter: 30, ...over };
  };
  const server = {
    calls,
    status: 'pending',
    device: null,
    on(key, ...responses) {
      routes.set(key, responses);
    },
    fetch: async (url, init = {}) => {
      const method = init.method || 'GET';
      const body = init.body ? JSON.parse(init.body) : undefined;
      calls.push({ method, url, body, credentials: init.credentials });
      const key = `${method} ${url}`;
      const queued = routes.get(key) || routes.get(`${method} ${url.replace(/h\d+/, ':id')}`);
      if (queued?.length) {
        const [status, data] = queued.length > 1 ? queued.shift() : queued[0];
        return reply(status, typeof data === 'function' ? data(body) : data);
      }
      if (method === 'POST' && url === '/auth/handoff') return reply(201, handoff());
      if (method === 'GET') return reply(200, { id: url.split('/').pop(), status: server.status, expiresAt: '', device: server.device });
      if (url.endsWith('/decision')) return reply(200, { status: { approve: 'approved', deny: 'denied', cancel: 'cancelled' }[body.decision] });
      return reply(404, { error: 'not found', reason: 'unknown' });
    },
  };
  return server;
}

const flush = () => new Promise((r) => setTimeout(r, 0));

function setup() {
  const timers = fakeTimers();
  const server = fakeServer();
  const c = new PhoneSignInController({ fetch: server.fetch, timers, pollMs: 1500, successCloseMs: 2000 });
  const seen = [];
  c.subscribe(() => seen.push(c.state.kind));
  return { c, timers, server, seen };
}

const phone = { name: 'Pixel 8', platform: 'android', ip: '203.0.113.9', userAgent: 'Mozilla/5.0 (Linux; Android 15) Hoja/1.0.2' };

let failures = 0;
async function test(name, fn) {
  try {
    await fn();
    console.log(`  ✓ ${name}`);
  } catch (e) {
    failures++;
    console.error(`  ✗ ${name}\n      ${e.message}`);
  }
}
function eq(got, want, what) {
  const g = JSON.stringify(got);
  const w = JSON.stringify(want);
  if (g !== w) throw new Error(`${what}: got ${g}, want ${w}`);
}

await test('create renders the QR url and the code', async () => {
  const { c, server } = setup();
  await c.start();
  eq(server.calls[0], { method: 'POST', url: '/auth/handoff', body: {}, credentials: 'same-origin' }, 'create request');
  eq(c.state.kind, 'pending', 'state');
  eq(c.state.qrUrl, 'https://acme.hoja.work/m/pair#h1.s1', 'qrUrl');
  eq(formatCode(c.state.code), 'BCDF-GHJK', 'formatted code');
  eq(c.state.rotateAfter, 30, 'rotateAfter');
});

await test('polls GET /auth/handoff/<id> every 1.5 s while pending', async () => {
  const { c, timers, server } = setup();
  await c.start();
  eq(timers.fire(1500), 1, 'poll timer');
  await flush();
  eq(server.calls.at(-1), { method: 'GET', url: '/auth/handoff/h1', body: undefined, credentials: 'same-origin' }, 'poll request');
  eq([...timers.pending.values()].filter((t) => t.ms === 1500).length, 1, 'next poll scheduled');
});

await test('rotation after rotateAfter seconds sends replaces and shows the new code', async () => {
  const { c, timers, server } = setup();
  await c.start();
  eq(timers.fire(30000), 1, 'rotate timer');
  await flush();
  eq(server.calls.find((x) => x.body?.replaces), { method: 'POST', url: '/auth/handoff', body: { replaces: 'h1' }, credentials: 'same-origin' }, 'rotate request');
  eq(server.calls.filter((x) => x.url.endsWith('/decision')).length, 0, 'nothing cancelled when the old code was still pending');
  eq(c.state.kind, 'pending', 'still pending');
  eq(c.state.id, 'h2', 'new handoff id');
  eq(c.state.qrUrl, 'https://acme.hoja.work/m/pair#h2.s2', 'new qrUrl');
  eq([...timers.pending.values()].filter((t) => t.ms === 30000).length, 1, 'next rotation scheduled');
});

await test('a code claimed just before rotation is kept and the new one is cancelled', async () => {
  const { c, timers, server } = setup();
  await c.start();
  server.on('GET /auth/handoff/h1', [200, { id: 'h1', status: 'claimed', device: phone }]);
  timers.fire(30000);
  await flush();
  await flush();
  eq(c.state.kind, 'claimed', 'state');
  eq(c.state.id, 'h1', 'the claimed code stays');
  eq(server.calls.at(-1), { method: 'POST', url: '/auth/handoff/h2/decision', body: { decision: 'cancel' }, credentials: 'same-origin' }, 'new code cancelled');
});

await test('a poll answer for the replaced code is ignored', async () => {
  const { c, timers, server } = setup();
  await c.start();
  let release;
  server.on('GET /auth/handoff/h1', [200, () => ({ id: 'h1', status: 'cancelled' })]);
  const slow = server.fetch;
  server.fetch = async (url, init) => {
    if (url === '/auth/handoff/h1') await new Promise((r) => (release = r));
    return slow(url, init);
  };
  c['fetchImpl'] = server.fetch;
  timers.fire(1500); // poll of h1 is now in flight
  timers.fire(30000); // rotate to h2
  await flush();
  release();
  await flush();
  eq(c.state.kind, 'pending', 'state after stale "cancelled"');
  eq(c.state.id, 'h2', 'current id');
});

await test('claimed shows the approve card and Approve posts the decision', async () => {
  const { c, timers, server } = setup();
  await c.start();
  server.status = 'claimed';
  server.device = phone;
  timers.fire(1500);
  await flush();
  eq(c.state.kind, 'claimed', 'state');
  eq(c.state.device.name, 'Pixel 8', 'device name');
  eq([...timers.pending.values()].filter((t) => t.ms === 30000).length, 0, 'no rotation once claimed');
  await c.approve();
  eq(server.calls.at(-1), { method: 'POST', url: '/auth/handoff/h1/decision', body: { decision: 'approve' }, credentials: 'same-origin' }, 'decision request');
  eq(c.state.kind, 'approved', 'waiting for the phone');
  server.status = 'consumed';
  timers.fire(1500);
  await flush();
  eq(c.state.kind, 'consumed', 'signed in');
  eq(c.state.device.name, 'Pixel 8', 'success names the device');
  timers.fire(2000);
  eq(c.state.kind, 'closed', 'auto-closes');
});

await test('Deny posts deny and closes', async () => {
  const { c, timers, server } = setup();
  await c.start();
  server.status = 'claimed';
  server.device = phone;
  timers.fire(1500);
  await flush();
  await c.deny();
  eq(server.calls.at(-1).body, { decision: 'deny' }, 'decision');
  eq(c.state.kind, 'closed', 'state');
  eq(timers.pending.size, 0, 'timers stopped');
});

await test('approve refused by policy keeps the card and explains why', async () => {
  const { c, timers, server } = setup();
  await c.start();
  server.status = 'claimed';
  server.device = phone;
  timers.fire(1500);
  await flush();
  server.on('POST /auth/handoff/:id/decision', [403, { error: 'nope', reason: 'ip_not_allowed' }]);
  await c.approve();
  eq(c.state.kind, 'claimed', 'state');
  eq(c.state.busy, false, 'buttons enabled again');
  eq(c.state.notice, messageFor('ip_not_allowed'), 'notice');
});

await test('burned shows the warning and stops polling', async () => {
  const { c, timers, server } = setup();
  await c.start();
  server.status = 'burned';
  timers.fire(1500);
  await flush();
  eq(c.state.kind, 'burned', 'state');
  eq(timers.pending.size, 0, 'timers stopped');
  await c.retry();
  eq(c.state.kind, 'pending', 'Show a new code');
  eq(server.calls.at(-1).body, {}, 'a fresh code, not a replacement');
});

await test('expired shows Code expired', async () => {
  const { c, timers, server } = setup();
  await c.start();
  server.status = 'expired';
  timers.fire(1500);
  await flush();
  eq(c.state.kind, 'expired', 'state');
});

await test('close while pending sends cancel', async () => {
  const { c, server, timers } = setup();
  await c.start();
  c.close();
  await flush();
  eq(server.calls.at(-1), { method: 'POST', url: '/auth/handoff/h1/decision', body: { decision: 'cancel' }, credentials: 'same-origin' }, 'cancel request');
  eq(timers.pending.size, 0, 'timers stopped');
});

await test('close while claimed sends cancel', async () => {
  const { c, timers, server } = setup();
  await c.start();
  server.status = 'claimed';
  timers.fire(1500);
  await flush();
  c.close();
  await flush();
  eq(server.calls.at(-1).body, { decision: 'cancel' }, 'cancel');
});

await test('close after sign-in sends nothing', async () => {
  const { c, timers, server } = setup();
  await c.start();
  server.status = 'consumed';
  timers.fire(1500);
  await flush();
  const before = server.calls.length;
  c.close();
  await flush();
  eq(server.calls.length, before, 'no request');
});

await test('close while the code is still being created cancels it once it exists', async () => {
  const { c, server } = setup();
  const started = c.start();
  c.close();
  await started;
  await flush();
  eq(server.calls.map((x) => `${x.method} ${x.url} ${JSON.stringify(x.body)}`), ['POST /auth/handoff {}', 'POST /auth/handoff/h1/decision {"decision":"cancel"}'], 'requests');
});

for (const reason of ['rate_limited', 'browser_session_required']) {
  await test(`create refused with ${reason} shows a clear message`, async () => {
    const { c, server } = setup();
    server.on('POST /auth/handoff', [reason === 'rate_limited' ? 429 : 403, { error: 'x', reason }]);
    await c.start();
    eq(c.state.kind, 'error', 'state');
    eq(c.state.message, messageFor(reason), 'message');
  });
}

await test('a network failure while creating is reported, not thrown', async () => {
  const timers = fakeTimers();
  const c = new PhoneSignInController({ fetch: async () => { throw new TypeError('Failed to fetch'); }, timers });
  await c.start();
  eq(c.state.kind, 'error', 'state');
  eq(c.state.reason, 'network', 'reason');
});

if (failures) {
  console.error(`\n${failures} failing`);
  process.exit(1);
}
console.log('\nphone sign-in: all passing');
