#!/usr/bin/env node
// Settings → Billing & usage (HOJA-734). Read-only screen: there is nothing to save, so this
// journey checks that the figures match the API, that money is formatted as currency (no
// six-decimal strings, "<$0.01" for sub-cent), that the ledger does not crush names on a phone,
// that month/grouping switches re-query, that a failed read offers Try again, and that a
// member is refused. A populated ledger is simulated by fulfilling usageSummary/usageEvents
// with a fixture, because a fresh stack has no ledger rows.
//
//   node scripts/settings-e2e/billing.cjs   (env: SETTINGS_E2E_URL, SETTINGS_E2E_ADMIN_TOKEN,
//                                            SETTINGS_E2E_MEMBER_TOKEN, SETTINGS_E2E_GRAPHQL)
const fs = require("node:fs");
const path = require("node:path");

function load(name) {
  for (const base of [
    process.env.SETTINGS_E2E_NODE_MODULES,
    path.join(__dirname, "../../local-e2e/node_modules"),
    path.join(__dirname, "../../frontend/node_modules"),
    process.env.SETTINGS_E2E_FALLBACK_MODULES,
  ].filter(Boolean)) {
    const p = path.join(base, name);
    if (fs.existsSync(p)) return require(p);
  }
  throw new Error(`${name} not found`);
}

const usd = (v) => {
  const n = Number(v || 0);
  if (n !== 0 && Math.abs(n) < 0.005) return "<$0.01";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(n);
};
const monthLabel = (m) => {
  const [y, mo] = m.split("-").map(Number);
  return new Date(Date.UTC(y, mo - 1, 1)).toLocaleDateString("en-US", {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
};

function fixture(month) {
  const row = (o) => ({
    agentUserId: null,
    model: null,
    month,
    inputTokens: 0,
    outputTokens: 0,
    cacheReadTokens: 0,
    credits: 0,
    runMinutes: 0,
    runs: 0,
    costUsd: "0.000000",
    computeCostUsd: "0.000000",
    limitCredits: null,
    budgetAction: null,
    ...o,
  });
  return {
    month,
    groupBy: "agent",
    credits: 1530,
    runMinutes: 95,
    costUsd: "1234.567891",
    computeCostUsd: "0.004000",
    totalUsd: "1234.571891",
    inputTokens: 1200000,
    outputTokens: 250000,
    cacheReadTokens: 80000,
    events: 42,
    priceCard: "2026-11",
    rows: [
      row({
        key: "a1",
        label: "Release-notes summariser with an unusually long agent name",
        agentUserId: "a1",
        credits: 1500,
        runMinutes: 90,
        runs: 12,
        inputTokens: 1190000,
        outputTokens: 240000,
        cacheReadTokens: 80000,
        costUsd: "1234.567891",
        computeCostUsd: "0.000000",
        limitCredits: 1600,
        budgetAction: "stop",
      }),
      row({
        key: "a2",
        label: "Tiny",
        agentUserId: "a2",
        credits: 30,
        runMinutes: 5,
        runs: 1,
        costUsd: "0.003412",
        computeCostUsd: "0.000000",
      }),
    ],
  };
}
const eventsFixture = [
  {
    id: "e1",
    agentName: "Tiny",
    model: "gpt-4o-mini",
    credits: 30,
    runMinutes: 5,
    inputTokens: 100,
    outputTokens: 20,
    cacheReadTokens: 0,
    costUsd: "0.003412",
    computeCostUsd: "0.000000",
    source: "finish",
    authMode: "managed-key",
    compute: "managed",
    recordedAt: new Date().toISOString(),
  },
];

async function run({
  baseUrl,
  adminToken,
  memberToken,
  screenshotsDir,
  graphqlUrl = process.env.SETTINGS_E2E_GRAPHQL,
}) {
  const { chromium } = load("playwright");
  const passed = [],
    failed = [];
  const check = (cond, msg) => (cond ? passed : failed).push(msg);
  const api = async (query, variables) => {
    const r = await fetch(graphqlUrl || `${baseUrl}/graphql`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${adminToken}`,
      },
      body: JSON.stringify({ query, variables }),
    });
    const b = await r.json();
    if (b.errors) throw new Error(b.errors[0].message);
    return b.data;
  };
  fs.mkdirSync(screenshotsDir, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const open = async (token, viewport = { width: 1440, height: 900 }) => {
    const ctx = await browser.newContext({ viewport });
    await ctx.addInitScript(
      (t) => localStorage.setItem("hoja_token", t),
      token,
    );
    if (graphqlUrl)
      await ctx.route("**/graphql", async (route) => {
        try {
          await route.fulfill({
            response: await route.fetch({ url: graphqlUrl }),
          });
        } catch {
          /* context closed mid-request */
        }
      });
    const page = await ctx.newPage();
    page.errorsSeen = [];
    page.on("pageerror", (e) => page.errorsSeen.push(e.message));
    return page;
  };
  const goto = async (page) => {
    for (let attempt = 0; ; attempt++) {
      await page.goto(`${baseUrl}/workspace/settings/billing`);
      try {
        await page
          .getByRole("heading", { name: "Billing & usage", level: 1 })
          .waitFor({ timeout: 45000 });
        break;
      } catch (e) {
        if (attempt) throw e; // the shared dev server can stall one load while it rebuilds
      }
    }
    await page
      .waitForFunction(
        () => !document.querySelector("[data-settings-loading]"),
        null,
        { timeout: 15000 },
      )
      .catch(() => {});
  };
  const shot = async (page, name, dark) => {
    if (dark)
      await page.evaluate(() => {
        document.documentElement.dataset.theme = "dark";
      });
    await page.waitForTimeout(150);
    await page.screenshot({
      path: path.join(screenshotsDir, `${name}${dark ? "-dark" : ""}.png`),
      fullPage: true,
    });
    if (dark)
      await page.evaluate(() => {
        delete document.documentElement.dataset.theme;
      });
  };
  const withFixture = async (page) => {
    await page.route("**/graphql", async (route) => {
      const body = route.request().postData() || "";
      const vars = JSON.parse(body).variables || {};
      if (body.includes("usageSummary"))
        return route.fulfill({
          json: {
            data: {
              usageSummary: { ...fixture(vars.month), groupBy: vars.groupBy },
            },
          },
        });
      if (body.includes("usageEvents"))
        return route.fulfill({
          json: { data: { usageEvents: eventsFixture } },
        });
      return route.fallback();
    });
  };

  try {
    // 1. Real data: the stat strip matches the API, formatted as currency.
    const page = await open(adminToken);
    await goto(page);
    const real = (
      await api(
        "query{usageSummary{month totalUsd costUsd computeCostUsd credits events rows{key}}}",
      )
    ).usageSummary;
    const totalText = await page
      .locator('[data-billing-stat^="Total for"]')
      .innerText();
    check(
      totalText.includes(usd(real.totalUsd)),
      `total shows ${usd(real.totalUsd)} (API ${real.totalUsd})`,
    );
    check(
      totalText.includes(monthLabel(real.month)),
      `total is labelled ${monthLabel(real.month)}`,
    );
    const pageText = await page.locator("[data-settings-page]").innerText();
    check(
      !/\$\d[\d,]*\.\d{3,}/.test(pageText),
      "no money figure prints more than two decimals",
    );
    const stats = await page.locator("[data-billing-stat]").count();
    check(stats === 5, `stat strip has 5 figures (${stats})`);
    if (real.rows.length === 0) {
      check(
        await page
          .getByText(`No usage in ${monthLabel(real.month)}`)
          .isVisible(),
        "empty ledger names the month and next step",
      );
      check(
        await page.getByText("No reports this month").isVisible(),
        "recent reports empty state",
      );
    } else {
      check(
        (await page.locator("[data-usage-row]").count()) >= real.rows.length,
        "ledger renders every API row",
      );
    }
    check(
      (await page
        .getByRole("link", { name: "Change budgets in Agents" })
        .getAttribute("href")) === "/workspace/agents",
      "budget link points at Agents",
    );
    await shot(page, "billing-empty-1440");

    // 2. Month navigation re-queries; Next is disabled on the current month.
    check(
      await page.getByRole("button", { name: "Next month" }).isDisabled(),
      "Next month disabled on current month",
    );
    const reqPromise = page.waitForRequest((r) =>
      (r.postData() || "").includes("usageSummary"),
    );
    await page.getByRole("button", { name: "Previous month" }).click();
    const req = await reqPromise;
    const prevMonth = JSON.parse(req.postData()).variables.month;
    check(prevMonth < real.month, `Previous month queries ${prevMonth}`);
    await page
      .getByText(monthLabel(prevMonth), { exact: true })
      .first()
      .waitFor();
    check(true, `header shows ${monthLabel(prevMonth)}`);
    await page.context().close();

    // 3. Populated ledger (fixture): currency, sub-cent, budget, grouping, long names.
    for (const [w, h] of [
      [1440, 900],
      [390, 844],
    ]) {
      const p = await open(adminToken, { width: w, height: h });
      await withFixture(p);
      await goto(p);
      await p.locator('[data-usage-row="a1"]').waitFor();
      const total = await p
        .locator('[data-billing-stat^="Total for"]')
        .innerText();
      check(total.includes("$1,234.57"), `@${w}: total formatted $1,234.57`);
      check(
        (await p.locator('[data-usage-row="a2"]').getByText("<$0.01").count()) >
          0,
        `@${w}: sub-cent cost shows <$0.01`,
      );
      check(
        (await p.locator('[data-usage-row="a1"]').getByText("1,500").count()) >
          0,
        `@${w}: budget usage 1,500 / 1,600 shown`,
      );
      check(
        (await p.getByRole("meter", { name: "Budget used" }).count()) === 1,
        `@${w}: budget meter rendered`,
      );
      const nameBox = await p
        .locator('[data-usage-row="a1"] .truncate')
        .first()
        .boundingBox();
      check(
        nameBox && nameBox.width >= 150,
        `@${w}: agent name gets ${nameBox && Math.round(nameBox.width)}px (not squeezed)`,
      );
      const sw = await p.evaluate(() => document.documentElement.scrollWidth);
      check(sw <= w, `@${w}: no horizontal overflow (${sw})`);
      // Stat strip: the total spans its row, no orphan cell.
      const boxes = await p.locator("[data-billing-stat]").evaluateAll((els) =>
        els.map((e) => {
          const r = e.getBoundingClientRect();
          return [Math.round(r.top), Math.round(r.width)];
        }),
      );
      const rows = {};
      for (const [top] of boxes) rows[top] = (rows[top] || 0) + 1;
      const counts = Object.values(rows);
      check(
        (counts.length === 1 ||
          (counts[0] === 1 && counts.slice(1).every((c) => c === 2))) &&
          boxes[0][1] > boxes[1][1],
        `@${w}: stat strip rows ${JSON.stringify(counts)} with a wider total`,
      );
      await shot(p, `billing-${w}`);
      await shot(p, `billing-${w}`, true);
      await p
        .locator("[data-billing-ledger]")
        .first()
        .screenshot({
          path: path.join(screenshotsDir, `billing-ledger-${w}.png`),
        });
      if (w === 1440) {
        const grp = p.waitForRequest((r) =>
          (r.postData() || "").includes('"groupBy":"model"'),
        );
        await p.getByRole("radio", { name: "Model" }).click();
        await grp;
        check(true, "Group by Model re-queries with groupBy=model");
        await p.getByRole("heading", { name: "Usage by model" }).waitFor();
        check(true, "ledger heading follows grouping");
      }
      await p.context().close();
    }

    // 4. A failed read shows ErrorState with Try again, which recovers.
    {
      const p = await open(adminToken);
      let fail = true;
      await p.route("**/graphql", (route) => {
        if (fail && (route.request().postData() || "").includes("usageSummary"))
          return route.fulfill({ status: 500, body: "oops" });
        return route.fallback();
      });
      await p.goto(`${baseUrl}/workspace/settings/billing`);
      const retry = p.getByRole("button", { name: "Try again" }).first();
      await retry.waitFor({ timeout: 15000 });
      check(
        await p.getByText("Couldn't load this page").first().isVisible(),
        "failed read shows ErrorState",
      );
      check(
        !(await p.locator("[data-settings-page]").innerText()).includes("oops"),
        "raw server body not shown",
      );
      await shot(p, "billing-error-1440");
      fail = false;
      await retry.click();
      await p
        .locator('[data-billing-stat^="Total for"]')
        .waitFor({ timeout: 10000 });
      check(true, "Try again recovers the page");
      check(
        !p.errorsSeen.length,
        `no uncaught page errors ${p.errorsSeen.join("|")}`,
      );
      await p.context().close();
    }

    // 5. 320px: no horizontal overflow (real data).
    {
      const p = await open(adminToken, { width: 320, height: 700 });
      await withFixture(p);
      await goto(p);
      await p.locator('[data-usage-row="a1"]').waitFor();
      const sw = await p.evaluate(() => document.documentElement.scrollWidth);
      check(sw <= 320, `@320: no horizontal overflow (${sw})`);
      await p.context().close();
    }

    // 6. Member: refused (admin-only; the ledger API also refuses).
    {
      const p = await open(memberToken);
      await p.goto(`${baseUrl}/workspace/settings/billing`);
      await p
        .getByText("Only workspace admins can open this page")
        .waitFor({ timeout: 15000 })
        .then(
          () => check(true, "member is refused"),
          () => check(false, "member is refused"),
        );
      await p.context().close();
    }
  } catch (e) {
    failed.push(`crashed: ${e.message.split("\n")[0]}`);
  } finally {
    await browser.close();
  }
  return { passed, failed };
}

module.exports = { run };

if (require.main === module) {
  run({
    baseUrl: process.env.SETTINGS_E2E_URL || "http://127.0.0.1:5834",
    adminToken: process.env.SETTINGS_E2E_ADMIN_TOKEN,
    memberToken:
      process.env.SETTINGS_E2E_MEMBER_TOKEN,
    screenshotsDir:
      process.env.SETTINGS_E2E_SHOTS ||
      process.env.SETTINGS_E2E_SHOTS || require('node:path').join(require('node:os').tmpdir(), 'settings-e2e', 'billing'),
  }).then(({ passed, failed }) => {
    for (const p of passed) console.log("  ok   " + p);
    for (const f of failed) console.log("  FAIL " + f);
    console.log(`${passed.length} passed, ${failed.length} failed`);
    process.exit(failed.length ? 1 : 0);
  });
}
