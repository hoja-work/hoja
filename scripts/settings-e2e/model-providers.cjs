#!/usr/bin/env node
// Settings → Model providers (HOJA-734): add → toggle → edit (incl. replace key) → delete, each
// read back from the API after a reload; a failed toggle rolls back with a visible error; a
// delete answered `false` is an error, not a success; managed (default-*) providers show
// "Managed" and offer no edit/delete; a member is refused.
//
// Creating a provider needs HOJA_SECRET_KEY on the server. When the server lacks it, the journey
// asserts the plain-language error and seeds a provider row straight into SQLite instead
// (SETTINGS_E2E_DB, default: the HOJA-734 stack db), so every other step still runs.
// The managed row is always seeded that way (managed providers are provisioned, not created).
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

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

// The disposable stack's SQLite file, for seeding rows the UI cannot create without
// HOJA_SECRET_KEY. Unset → those steps are skipped and reported.
const DEFAULT_DB = process.env.SETTINGS_E2E_DB;

function seed(db, row) {
  const py = `
import sqlite3, sys, json, datetime
r = json.loads(sys.argv[2]); c = sqlite3.connect(sys.argv[1])
org = c.execute("select organization_id from user where admin = 1 order by created_at limit 1").fetchone()[0]
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
c.execute("insert into model_provider (id, organization_id, name, provider, model_id, base_url, context_window, encrypted_api_key, enabled, created_at, updated_at) values (?,?,?,?,?,?,?,?,?,?,?)",
  (r["id"], org, r["name"], r["provider"], r["modelId"], r.get("baseUrl"), r.get("contextWindow"), "", 1, now, now))
c.commit()`;
  execFileSync("python3", ["-c", py, db, JSON.stringify(row)]);
}

async function run({
  baseUrl,
  adminToken,
  memberToken,
  screenshotsDir,
  graphqlUrl = process.env.SETTINGS_E2E_GRAPHQL,
  db = process.env.SETTINGS_E2E_DB || DEFAULT_DB,
}) {
  const { chromium } = load("playwright");
  const passed = [],
    failed = [];
  const check = (cond, msg) => (cond ? passed : failed).push(msg);
  const api = async (query, variables, token = adminToken) => {
    const r = await fetch(graphqlUrl || `${baseUrl}/graphql`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ query, variables }),
    });
    const b = await r.json();
    if (b.errors) throw new Error(b.errors[0].message);
    return b.data;
  };
  const list = async () =>
    (
      await api(
        "query{modelProviders{id name modelId baseUrl enabled hasApiKey managed updatedAt}}",
      )
    ).modelProviders;
  fs.mkdirSync(screenshotsDir, { recursive: true });
  const stamp = Date.now().toString(36);
  const name = `E2E model ${stamp}`;
  const managedId = `default-e2e-${stamp}`;
  const managedName = `Hoja managed ${stamp}`;
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
      await page.goto(`${baseUrl}/workspace/settings/model-providers`);
      try {
        await page
          .getByRole("heading", { name: "Model providers", level: 1 })
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
  const shot = async (page, file, dark) => {
    if (dark)
      await page.evaluate(() => {
        document.documentElement.dataset.theme = "dark";
      });
    await page.waitForTimeout(150);
    await page.screenshot({
      path: path.join(screenshotsDir, `${file}${dark ? "-dark" : ""}.png`),
      fullPage: true,
    });
    if (dark)
      await page.evaluate(() => {
        delete document.documentElement.dataset.theme;
      });
  };
  const row = (page, n) =>
    page
      .getByRole("listitem")
      .filter({ has: page.getByRole("switch", { name: `Enable ${n}` }) });

  try {
    const page = await open(adminToken);
    await goto(page);
    if ((await list()).length === 0) {
      await page
        .getByText("No model providers yet")
        .waitFor({ timeout: 10000 })
        .then(
          () => check(true, "empty state names the next action"),
          () => check(false, "empty state names the next action"),
        );
      await shot(page, "model-providers-empty-1440");
    }

    // 1. Add (FormDialog). Validation first: submit empty → field errors, dialog stays.
    await page.getByRole("button", { name: "Add provider" }).first().click();
    const dlg = page.getByRole("dialog", { name: "Add a model provider" });
    await dlg.getByRole("button", { name: "Add provider" }).click();
    check(
      await dlg.getByText("Enter a name people will recognise.").isVisible(),
      "empty submit shows field errors",
    );
    await dlg.getByLabel("Name").fill(name);
    await dlg.getByLabel("Model ID").fill("gpt-4o-mini");
    await dlg.getByLabel("Base URL").fill("https://api.openai.com/v1");
    await dlg.getByLabel("API key").fill("sk-e2e-first");
    await dlg.getByRole("button", { name: "Add provider" }).click();
    const outcome = await Promise.race([
      dlg.waitFor({ state: "hidden", timeout: 15000 }).then(() => "ok"),
      dlg
        .getByText("encryption isn't set up")
        .waitFor({ timeout: 15000 })
        .then(() => "nosecret"),
    ]);
    if (outcome === "nosecret") {
      check(
        true,
        "server without HOJA_SECRET_KEY: plain-language error, dialog keeps values",
      );
      check(
        (await dlg.getByLabel("Name").inputValue()) === name,
        "dialog kept typed values on failure",
      );
      await dlg.getByRole("button", { name: "Cancel" }).click();
      seed(db, {
        id: `e2e-${stamp}`,
        name,
        provider: "openai",
        modelId: "gpt-4o-mini",
        baseUrl: "https://api.openai.com/v1",
      });
      passed.push(
        "create needs HOJA_SECRET_KEY: seeded the row in SQLite instead (create step not verified here)",
      );
    } else {
      const created = (await list()).find((p) => p.name === name);
      check(
        created &&
          created.enabled &&
          created.hasApiKey &&
          created.baseUrl === "https://api.openai.com/v1",
        "create read back from API (enabled, key stored, base URL)",
      );
    }
    seed(db, {
      id: managedId,
      name: managedName,
      provider: "hoja",
      modelId: "hoja-default",
      contextWindow: 200000,
    });

    // 2. Toggle off → reload → still off; API agrees.
    await goto(page);
    const sw = page.getByRole("switch", { name: `Enable ${name}` });
    await sw.click();
    await row(page, name)
      .getByText("Saved")
      .waitFor({ timeout: 10000 })
      .then(
        () => check(true, "toggle shows Saved"),
        () => check(false, "toggle shows Saved"),
      );
    await goto(page);
    check(
      (await page
        .getByRole("switch", { name: `Enable ${name}` })
        .getAttribute("aria-checked")) === "false",
      "disabled survives reload",
    );
    check(
      (await list()).find((p) => p.name === name)?.enabled === false,
      "API reads enabled=false",
    );

    // 3. Failure: abort the update → switch snaps back, error with Retry.
    await page.route("**/graphql", (route) =>
      (route.request().postData() || "").includes("ModelProviderUpdate")
        ? route.abort()
        : route.fallback(),
    );
    await page.getByRole("switch", { name: `Enable ${name}` }).click();
    await row(page, name).getByRole("alert").waitFor({ timeout: 10000 });
    check(
      (await page
        .getByRole("switch", { name: `Enable ${name}` })
        .getAttribute("aria-checked")) === "false",
      "failed toggle rolls back",
    );
    check(
      await row(page, name).getByRole("button", { name: "Retry" }).isVisible(),
      "failed toggle offers Retry",
    );
    await shot(page, "model-providers-toggle-error-1440");
    await page.unroute("**/graphql");
    await row(page, name).getByRole("button", { name: "Retry" }).click();
    await row(page, name).getByText("Saved").waitFor({ timeout: 10000 });
    check(
      (await list()).find((p) => p.name === name)?.enabled === true,
      "Retry saves enabled=true",
    );

    // 4. Managed row: badge, no actions menu.
    await goto(page);
    const mrow = row(page, managedName);
    check(
      await mrow.getByText("Managed", { exact: true }).isVisible(),
      "managed provider marked Managed",
    );
    check(
      (await mrow
        .getByRole("button", { name: `Actions for ${managedName}` })
        .count()) === 0,
      "managed provider offers no edit/delete",
    );

    // 5. Edit: rename, change model, replace key. Key field is never pre-filled.
    const renamed = `${name} v2`;
    await page.getByRole("button", { name: `Actions for ${name}` }).click();
    await page.getByRole("menuitem", { name: "Edit" }).click();
    const edit = page.getByRole("dialog", { name: `Edit ${name}` });
    check(
      (await edit.getByLabel("Replace API key").inputValue()) === "",
      "key field is not pre-filled",
    );
    check(
      await edit.getByLabel("Provider").isDisabled(),
      "provider is read-only when editing",
    );
    await edit.getByLabel("Name").fill(renamed);
    await edit.getByLabel("Model ID").fill("gpt-4o");
    const before = (await list()).find((p) => p.name === name);
    await edit.getByLabel("Replace API key").fill("sk-e2e-rotated");
    await shot(page, "model-providers-edit-1440");
    await edit.getByRole("button", { name: "Save changes" }).click();
    const editOutcome = await Promise.race([
      edit.waitFor({ state: "hidden", timeout: 15000 }).then(() => "ok"),
      edit
        .getByText("encryption isn't set up")
        .waitFor({ timeout: 15000 })
        .then(() => "nosecret"),
    ]);
    if (editOutcome === "nosecret") {
      passed.push(
        "replace key needs HOJA_SECRET_KEY: error shown in dialog; retrying without a new key",
      );
      await edit.getByLabel("Replace API key").fill("");
      await edit.getByRole("button", { name: "Save changes" }).click();
      await edit.waitFor({ state: "hidden", timeout: 15000 });
    }
    await goto(page);
    check(
      await page.getByRole("switch", { name: `Enable ${renamed}` }).isVisible(),
      "rename visible after reload",
    );
    const after = (await list()).find((p) => p.name === renamed);
    check(
      after && after.modelId === "gpt-4o",
      "API reads new name and model ID",
    );
    if (editOutcome === "ok")
      check(
        after.hasApiKey && after.updatedAt !== before.updatedAt,
        "key replaced (hasApiKey, updatedAt moved)",
      );

    // 6. Delete answered `false` → error in the dialog, not a success.
    await page.route("**/graphql", (route) =>
      (route.request().postData() || "").includes("ModelProviderDelete")
        ? route.fulfill({ json: { data: { modelProviderDelete: false } } })
        : route.fallback(),
    );
    await page.getByRole("button", { name: `Actions for ${renamed}` }).click();
    await page.getByRole("menuitem", { name: "Delete" }).click();
    const confirm = page.getByRole("alertdialog");
    const del = confirm.getByRole("button", { name: "Delete provider" });
    check(await del.isDisabled(), "delete needs the name typed");
    await confirm.getByRole("textbox").fill(renamed);
    await del.click();
    await confirm
      .getByText("This provider no longer exists")
      .waitFor({ timeout: 10000 })
      .then(
        () => check(true, "delete=false reported as an error"),
        () => check(false, "delete=false reported as an error"),
      );
    await page.unroute("**/graphql");
    await confirm.getByRole("button", { name: "Cancel" }).click();

    // 7. Real delete.
    await goto(page);
    await page.getByRole("button", { name: `Actions for ${renamed}` }).click();
    await page.getByRole("menuitem", { name: "Delete" }).click();
    await page.getByRole("alertdialog").getByRole("textbox").fill(renamed);
    await page
      .getByRole("alertdialog")
      .getByRole("button", { name: "Delete provider" })
      .click();
    await page
      .getByRole("alertdialog")
      .waitFor({ state: "hidden", timeout: 10000 });
    check(
      !(await list()).some((p) => p.name === renamed),
      "delete read back from API",
    );
    await goto(page);
    check(
      (await page
        .getByRole("switch", { name: `Enable ${renamed}` })
        .count()) === 0,
      "deleted row gone after reload",
    );
    await shot(page, "model-providers-1440");
    await shot(page, "model-providers-1440", true);
    check(
      !page.errorsSeen.length,
      `no uncaught page errors ${page.errorsSeen.join("|")}`,
    );
    await page.context().close();

    for (const w of [390, 320]) {
      const p = await open(adminToken, { width: w, height: 844 });
      await goto(p);
      const sw2 = await p.evaluate(() => document.documentElement.scrollWidth);
      check(sw2 <= w, `@${w}: no horizontal overflow (${sw2})`);
      if (w === 390) {
        await shot(p, "model-providers-390");
        await shot(p, "model-providers-390", true);
      }
      await p.context().close();
    }

    // 8. Member: refused by the route and by the API.
    const m = await open(memberToken);
    await m.goto(`${baseUrl}/workspace/settings/model-providers`);
    await m
      .getByText("Only workspace admins can open this page")
      .waitFor({ timeout: 15000 })
      .then(
        () => check(true, "member is refused"),
        () => check(false, "member is refused"),
      );
    await m.context().close();
    await api("query{modelProviders{id}}", {}, memberToken).then(
      () => check(false, "API refuses member"),
      () => check(true, "API refuses member"),
    );
  } catch (e) {
    failed.push(`crashed: ${e.message.split("\n")[0]}`);
    for (const ctx of browser.contexts())
      for (const p of ctx.pages())
        await p
          .screenshot({ path: path.join(screenshotsDir, "crash.png") })
          .catch(() => {});
  } finally {
    // Remove this run's rows (the managed seed has no UI delete by design; the backend has no guard).
    const mine = await list().catch(() => []);
    for (const p of mine.filter(
      (x) => x.id === managedId || x.name.startsWith(name),
    ))
      await api("mutation($id:String!){modelProviderDelete(id:$id)}", {
        id: p.id,
      }).catch(() => {});
    await browser.close();
  }
  return { passed: passed.filter(Boolean), failed };
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
      process.env.SETTINGS_E2E_SHOTS || require('node:path').join(require('node:os').tmpdir(), 'settings-e2e', 'model-providers'),
  }).then(({ passed, failed }) => {
    for (const p of passed) console.log("  ok   " + p);
    for (const f of failed) console.log("  FAIL " + f);
    console.log(`${passed.length} passed, ${failed.length} failed`);
    process.exit(failed.length ? 1 : 0);
  });
}
