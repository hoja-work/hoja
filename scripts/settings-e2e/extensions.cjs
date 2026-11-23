#!/usr/bin/env node
// Settings → Extensions (HOJA-734): install from the structured form → credentials shown once in
// CopyFields → JSON editor reports a precise line/column error before any request → edit the
// manifest → reload and read back from the API → a failed update keeps the dialog open with
// the reason → uninstall behind a type-to-confirm → member refused.
//
// Installing needs HOJA_SECRET_KEY on the server (the signing secret is sealed). Against a server
// without it, the journey asserts the plain-language error and skips the steps that need an
// installed extension (reported as passed notes, never as silent success).
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
    (await api("query{extensions{id key name version access manifest}}"))
      .extensions;
  fs.mkdirSync(screenshotsDir, { recursive: true });
  const stamp = Date.now().toString(36);
  const key = `e2e-${stamp}`;
  const name = `E2E extension ${stamp}`;
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
      await page.goto(`${baseUrl}/workspace/settings/extensions`);
      try {
        await page
          .getByRole("heading", { name: "Extensions", level: 1 })
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

  try {
    const page = await open(adminToken);
    await goto(page);
    if ((await list()).length === 0) {
      await page
        .getByText("No extensions installed")
        .waitFor({ timeout: 10000 })
        .then(
          () => check(true, "empty state names the next action"),
          () => check(false, "empty state names the next action"),
        );
      await shot(page, "extensions-empty-1440");
    }
    check(
      !(await page.locator("[data-settings-page]").innerText()).includes(
        "Enabled",
      ),
      'no fake "Enabled" pill',
    );

    // 1. JSON editor: a syntax error is reported with line and column, before any request.
    await page
      .getByRole("button", { name: "Install extension" })
      .first()
      .click();
    const dlg = page.getByRole("dialog", { name: "Install an extension" });
    await dlg.getByRole("radio", { name: "JSON" }).click();
    const json = dlg.getByLabel("Manifest JSON");
    let sent = 0;
    page.on("request", (r) => {
      if ((r.postData() || "").includes("ExtensionInstall")) sent++;
    });
    await json.fill('{\n  "key": "x",\n  "name" "broken"\n}');
    await dlg.getByRole("button", { name: "Install", exact: true }).click();
    const err = dlg
      .getByRole("alert")
      .filter({ hasText: /Line 3, column \d+/ });
    await err
      .first()
      .waitFor({ timeout: 5000 })
      .then(
        () => check(true, 'invalid JSON → "Line 3, column N" error'),
        () => check(false, "invalid JSON → line/column error"),
      );
    check(sent === 0, "invalid JSON sends no request");
    await shot(page, "extensions-json-error-1440");
    await json.fill('{"key": "x", "bogus": 1}');
    await dlg.getByRole("button", { name: "Install", exact: true }).click();
    check(
      (await dlg.getByText(/Unknown field: bogus/).count()) > 0,
      "unknown manifest field named before sending",
    );

    // 2. Structured form: invalid URL caught locally, then a real install.
    await json.fill("{}");
    await dlg.getByRole("radio", { name: "Form" }).click();
    await dlg.getByLabel("Name").fill(name);
    await dlg.getByLabel("Key").fill(key);
    await dlg.getByLabel("Version").fill("1.0.0");
    await dlg.getByLabel("URL").fill("http://example.com");
    await dlg.getByRole("button", { name: "Install", exact: true }).click();
    check(
      await dlg.getByText("Enter the extension's https URL").isVisible(),
      "http URL rejected locally",
    );
    await dlg.getByLabel("URL").fill("https://example.com");
    await dlg.getByRole("radio", { name: "Read and write" }).click();
    await shot(page, "extensions-install-form-1440");
    await dlg.getByRole("button", { name: "Install", exact: true }).click();
    const creds = page.getByRole("dialog", { name: `${name} is installed` });
    const outcome = await Promise.race([
      creds.waitFor({ timeout: 20000 }).then(() => "ok"),
      dlg
        .getByText("encryption isn't set up")
        .waitFor({ timeout: 20000 })
        .then(() => "nosecret"),
    ]);
    if (outcome === "nosecret") {
      check(
        true,
        "server without HOJA_SECRET_KEY: plain-language error, dialog stays open",
      );
      passed.push(
        "install needs HOJA_SECRET_KEY: edit/uninstall steps skipped on this server",
      );
      await dlg.getByRole("button", { name: "Cancel" }).click();
    } else {
      const apiKey = await creds
        .getByRole("textbox", { name: "Extension API key" })
        .inputValue();
      const secret = await creds
        .getByRole("textbox", { name: "Extension signing secret" })
        .inputValue();
      check(
        apiKey.length > 10 && secret.length > 10,
        "API key and signing secret shown once in CopyFields",
      );
      await shot(page, "extensions-credentials-1440");
      await creds.getByRole("button", { name: "I've saved them" }).click();
      check(
        !(await page.locator("body").innerText()).includes(apiKey),
        "secret gone after dismissing",
      );
      const installed = (await list()).find((e) => e.key === key);
      check(
        installed &&
          installed.access === "read_write" &&
          installed.name === name,
        "install read back from API (name, access)",
      );

      // 3. Edit via the form → reload → API.
      await goto(page);
      await page.getByRole("button", { name: `Actions for ${name}` }).click();
      await page.getByRole("menuitem", { name: "Edit manifest" }).click();
      const edit = page.getByRole("dialog", { name: `Edit ${name}` });
      check(
        await edit.getByLabel("Key").isDisabled(),
        "key is locked when editing",
      );
      const renamed = `${name} v2`;
      await edit.getByLabel("Name").fill(renamed);
      await edit.getByLabel("Version").fill("1.1.0");
      // Failure first: the update is refused → dialog stays with the reason.
      await page.route("**/graphql", (route) =>
        (route.request().postData() || "").includes("ExtensionUpdate")
          ? route.fulfill({ status: 500, body: "<html>bad gateway</html>" })
          : route.fallback(),
      );
      await edit.getByRole("button", { name: "Save manifest" }).click();
      await edit.getByRole("alert").first().waitFor({ timeout: 10000 });
      check(
        (await edit.isVisible()) &&
          (await edit.getByLabel("Name").inputValue()) === renamed,
        "failed update keeps dialog and values",
      );
      check(
        !(await edit.innerText()).includes("<html>"),
        "raw server body not shown",
      );
      await page.unroute("**/graphql");
      await edit.getByRole("button", { name: "Save manifest" }).click();
      await edit.waitFor({ state: "hidden", timeout: 15000 });
      await goto(page);
      check(
        await page.getByText(renamed, { exact: true }).isVisible(),
        "edited name visible after reload",
      );
      const after = (await list()).find((e) => e.key === key);
      check(
        after && after.name === renamed && after.version === "1.1.0",
        "API reads edited name and version",
      );

      // 4. Details dialog.
      await page
        .getByRole("button", { name: `Actions for ${renamed}` })
        .click();
      await page.getByRole("menuitem", { name: "View details" }).click();
      const det = page.getByRole("dialog", { name: renamed });
      check(
        (await det
          .getByRole("textbox", { name: "Extension URL" })
          .inputValue()) === "https://example.com",
        "details show the endpoint",
      );
      await shot(page, "extensions-details-1440");
      await page.keyboard.press("Escape");
      await shot(page, "extensions-1440");
      await shot(page, "extensions-1440", true);

      // 5. Uninstall: type-to-confirm.
      await page
        .getByRole("button", { name: `Actions for ${renamed}` })
        .click();
      await page.getByRole("menuitem", { name: "Uninstall" }).click();
      const confirm = page.getByRole("alertdialog");
      check(
        await confirm.getByRole("button", { name: "Uninstall" }).isDisabled(),
        "uninstall needs the key typed",
      );
      await confirm.getByRole("textbox").fill(key);
      await confirm.getByRole("button", { name: "Uninstall" }).click();
      await confirm.waitFor({ state: "hidden", timeout: 15000 });
      check(
        !(await list()).some((e) => e.key === key),
        "uninstall read back from API",
      );
      await goto(page);
      check(
        (await page.getByText(renamed, { exact: true }).count()) === 0,
        "uninstalled row gone after reload",
      );
    }
    check(
      !page.errorsSeen.length,
      `no uncaught page errors ${page.errorsSeen.join("|")}`,
    );
    await page.context().close();

    for (const w of [390, 320]) {
      const p = await open(adminToken, { width: w, height: 844 });
      await goto(p);
      const sw = await p.evaluate(() => document.documentElement.scrollWidth);
      check(sw <= w, `@${w}: no horizontal overflow (${sw})`);
      if (w === 390) {
        await shot(p, "extensions-390");
        await shot(p, "extensions-390", true);
        await p
          .getByRole("button", { name: "Install extension" })
          .first()
          .click();
        await p.waitForTimeout(300);
        const dw = await p.evaluate(() => document.documentElement.scrollWidth);
        check(dw <= w, `@${w}: install dialog fits (${dw})`);
        await shot(p, "extensions-install-390");
      }
      await p.context().close();
    }

    // 6. Member: refused by the route (registry marks the section admin) and by the API.
    const m = await open(memberToken);
    await m.goto(`${baseUrl}/workspace/settings/extensions`);
    await m
      .getByText("You don't have access to this page")
      .waitFor({ timeout: 30000 })
      .then(
        () => check(true, "member is refused"),
        () => check(false, "member is refused"),
      );
    await m.context().close();
    await api("query{extensions{id}}", {}, memberToken).then(
      () => check(false, "API refuses member without extension.manage"),
      () => check(true, "API refuses member without extension.manage"),
    );
  } catch (e) {
    failed.push(`crashed: ${e.message.split("\n")[0]}`);
    for (const ctx of browser.contexts())
      for (const p of ctx.pages())
        await p
          .screenshot({ path: path.join(screenshotsDir, "crash.png") })
          .catch(() => {});
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
      process.env.SETTINGS_E2E_SHOTS || require('node:path').join(require('node:os').tmpdir(), 'settings-e2e', 'extensions'),
  }).then(({ passed, failed }) => {
    for (const p of passed) console.log("  ok   " + p);
    for (const f of failed) console.log("  FAIL " + f);
    console.log(`${passed.length} passed, ${failed.length} failed`);
    process.exit(failed.length ? 1 : 0);
  });
}
