// Task view toolbar probe (HOJA-732): on My tasks, a team's Tasks page and the workspace Board,
// the layout / filter / display buttons sit on the page's title bar (not on a row of their own
// under the tabs), are 44px touch targets on phones, open their popovers on-screen, close them
// with Escape, and the layout switch still swaps board and list. Checked at 390x844 (touch) and
// 1280x800.
//
// Run from the app root after `cd frontend && npm run build`:
//   node scripts/ui-probes/task-toolbar-placement.cjs
// It seeds a throwaway database and serves frontend/dist, so it needs `.venv` and Playwright
// (PLAYWRIGHT_MODULE, or frontend/ or local-e2e/ node_modules). Exit 0 = all checks passed.
const path = require("path");
const repoRoot = path.resolve(__dirname, "../..");
function loadPlaywright() {
  for (const c of [
    process.env.PLAYWRIGHT_MODULE,
    "playwright",
    path.join(repoRoot, "frontend/node_modules/playwright"),
    path.join(repoRoot, "local-e2e/node_modules/playwright"),
  ].filter(Boolean)) {
    try {
      return require(c);
    } catch {}
  }
  throw new Error("Playwright not found; set PLAYWRIGHT_MODULE.");
}
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const { chromium } = loadPlaywright();
const repo = repoRoot,
  port = String(18930 + Math.floor(Math.random() * 60));
fs.mkdirSync(path.join(repo, "local-e2e/artifacts"), { recursive: true });
const scratch = fs.mkdtempSync(path.join(repo, "local-e2e/artifacts/toolbar-"));
const db = path.join(scratch, "toolbar.db");
spawnSync(
  repo + "/.venv/bin/python",
  [
    "-c",
    `from hoja_server.db import make_engine,migrate,seed_tenant
e=make_engine('${db}'); migrate(e); seed_tenant(e, org_name='Acme', team_key='TREBOL', team_name='Trebol', user_name='Ada', user_email='ada@example.test', token='tkn', demo_issues=True)`,
  ],
  { cwd: repo, env: { ...process.env, PYTHONPATH: repo } },
);
const s = spawn(
  repo + "/.venv/bin/hoja-serve",
  ["serve", "--db", db, "--host", "127.0.0.1", "--port", port],
  {
    cwd: repo,
    env: { ...process.env, HOJA_WEB_ROOT: repo + "/frontend/dist" },
    stdio: "ignore",
  },
);
const base = "http://127.0.0.1:" + port;
let bad = 0;
const ok = (c, m) => {
  console.log((c ? "ok   " : "FAIL ") + m);
  if (!c) bad++;
};
(async () => {
  for (let i = 0; i < 60; i++) {
    try {
      if ((await fetch(base)).ok) break;
    } catch {}
    await new Promise((r) => setTimeout(r, 250));
  }
  const b = await chromium.launch();
  for (const [vw, vh, mob] of [
    [390, 844, true],
    [1280, 800, false],
  ])
    for (const url of [
      "/workspace/my-issues/assigned",
      "/workspace/team/trebol/active",
      "/workspace/board",
    ]) {
      const ctx = await b.newContext({
        viewport: { width: vw, height: vh },
        isMobile: mob,
        hasTouch: mob,
      });
      await ctx.addInitScript(() => {
        localStorage.setItem("hoja_token", "tkn");
        localStorage.setItem("hoja_issue_layout", "Board");
      });
      const p = await ctx.newPage();
      await p.goto(base + url);
      await p.waitForTimeout(1800);
      const tag = `${vw} ${url}`;
      const inHead = await p.evaluate(
        () =>
          !!document.querySelector(
            "[data-my-issues-head] [data-my-issue-toolbar]",
          ),
      );
      ok(inHead, `${tag}: toolbar is in the title bar`);
      ok(
        await p.evaluate(
          () =>
            document.querySelectorAll("[data-my-issue-toolbar]").length === 1,
        ),
        `${tag}: exactly one toolbar`,
      );
      const sizes = await p.$$eval(
        "[data-my-issues-head] [data-my-issue-toolbar] button",
        (bs) =>
          bs.map((b) => {
            const r = b.getBoundingClientRect();
            return [
              Math.round(r.width),
              Math.round(r.height),
              Math.round(r.right),
            ];
          }),
      );
      if (mob)
        ok(
          sizes.every(([w, h]) => w >= 44 && h >= 44),
          `${tag}: toolbar buttons >=44px ${JSON.stringify(sizes)}`,
        );
      ok(
        sizes.every(([, , r]) => r <= vw),
        `${tag}: toolbar inside viewport`,
      );
      for (const name of ["Add filter", "Display options"]) {
        await p.getByRole("button", { name, exact: true }).first().click();
        await p.waitForTimeout(500);
        const pop = await p.evaluate(() => {
          const el = [
            ...document.querySelectorAll(
              "[data-my-issue-popover],[role=dialog]",
            ),
          ].find((e) => e.getBoundingClientRect().height > 0);
          if (!el) return null;
          const r = el.getBoundingClientRect();
          return [r.left, r.top, r.right, r.bottom].map(Math.round);
        });
        ok(
          pop && pop[0] >= 0 && pop[1] >= 0 && pop[2] <= vw && pop[3] <= vh,
          `${tag}: ${name} popover on-screen ${JSON.stringify(pop)}`,
        );
        await p.keyboard.press("Escape");
        await p.waitForTimeout(300);
        ok(
          await p.evaluate(
            () =>
              ![...document.querySelectorAll("[data-my-issue-popover]")].some(
                (e) => e.getBoundingClientRect().height > 0,
              ),
          ),
          `${tag}: ${name} closes with Escape`,
        );
      }
      const before = await p.evaluate(
        () => !!document.querySelector("[data-kanban-columns]"),
      );
      await p.locator("[data-layout-switch]").click();
      await p.waitForTimeout(700);
      const after = await p.evaluate(
        () => !!document.querySelector("[data-kanban-columns]"),
      );
      ok(before && !after, `${tag}: layout switch goes board -> list`);
      await ctx.close();
    }
  await b.close();
  s.kill();
  fs.rmSync(scratch, { recursive: true, force: true });
  process.exit(bad ? 1 : 0);
})();
