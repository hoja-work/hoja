// Labels journey (HOJA-734): create (workspace + team), validation, rename, colour (instant,
// rollback on failure), delete with usage count, and the read-only view for a member without
// label.manage. Every change is read back from /graphql after a reload.
const H = require("./lib/account-helpers.cjs");

const ID = "issue-labels";
const LIST = "{ issueLabelSettings { id name color description } }";

async function labelByName(page, name) {
  const d = await H.api(page, LIST);
  return (d.data?.issueLabelSettings || []).find((l) => l.name === name);
}

async function run({ baseUrl, adminToken, memberToken, screenshotsDir }) {
  const { chromium } = H.loadPlaywright();
  const { passed, failed, check, step } = H.harness();
  const browser = await chromium.launch();
  const stamp = Date.now().toString(36);
  const name = `E2E label ${stamp}`;
  const renamed = `E2E renamed ${stamp}`;
  const teamLabel = `E2E team ${stamp}`;
  const row = (page, n) =>
    page
      .locator("[data-resource-row]")
      .filter({ has: page.getByText(n, { exact: true }) });
  try {
    const page = await H.openPage(browser, baseUrl, adminToken);
    await H.gotoSection(page, baseUrl, ID);

    await step("create validation", async () => {
      await page.getByRole("button", { name: "New label" }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("Name").fill("x".repeat(65));
      await dialog
        .getByText(/Keep the name to 64 characters/)
        .waitFor({ timeout: 4000 });
      check(
        true,
        "too-long name shows the 64-character limit (not a permissions error)",
      );
      check(
        await dialog.getByRole("button", { name: "Create label" }).isDisabled(),
        "Create is disabled while the name is invalid",
      );
      await dialog.getByLabel("Name").fill(name);
      await dialog.getByLabel("Description").fill("Created by e2e");
      await dialog.getByRole("radio", { name: "Blue" }).click();
      // Forced failure: the dialog stays open with the reason and keeps the values.
      const restore = await H.failNextGraphql(page, /LabelCreate/);
      await dialog.getByRole("button", { name: "Create label" }).click();
      await dialog
        .getByRole("alert")
        .filter({ hasText: /Simulated failure/ })
        .waitFor({ timeout: 6000 });
      check(
        (await dialog.getByLabel("Name").inputValue()) === name,
        "failed create keeps the dialog open with the typed name",
      );
      await restore();
      await dialog.getByRole("button", { name: "Create label" }).click();
      await dialog.waitFor({ state: "detached", timeout: 8000 });
      check(
        (await row(page, name).count()) === 1,
        "created label appears in the list",
      );
    });

    await step("create team label", async () => {
      await page.getByRole("button", { name: "New label" }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("Name").fill(teamLabel);
      await dialog.getByLabel("Available to").click();
      await page.getByRole("option", { name: "Engineering only" }).click();
      await dialog.getByRole("button", { name: "Create label" }).click();
      await dialog.waitFor({ state: "detached", timeout: 8000 });
    });

    await page.reload();
    await H.gotoSection(page, baseUrl, ID);
    await step("reload + API read-back", async () => {
      check((await row(page, name).count()) === 1, "label survives reload");
      const api = await labelByName(page, name);
      check(
        api && api.color === "#3b82f6" && api.description === "Created by e2e",
        `API has the label with colour + description (${api && api.color})`,
      );
      const teamSection = page.getByRole("region", {
        name: "Engineering labels",
      });
      check(
        (await teamSection.getByText(teamLabel, { exact: true }).count()) === 1,
        "team label is listed under Engineering labels",
      );
      check(
        (await row(page, teamLabel)
          .getByText("ENG", { exact: true })
          .count()) === 1,
        "team label shows the ENG badge",
      );
      const scoped = await H.api(page, "{ teams { nodes { id } } }").then((t) =>
        H.api(
          page,
          "query($t:ID){ issueLabels(filter:{team:{id:{eq:$t}}}) { nodes { name } } }",
          { t: t.data.teams.nodes[0].id },
        ),
      );
      check(
        scoped.data.issueLabels.nodes.some((l) => l.name === teamLabel),
        "API scopes the team label to Engineering",
      );
    });

    await step("rename", async () => {
      await page.getByRole("button", { name: `Actions for ${name}` }).click();
      await page.getByRole("menuitem", { name: "Rename…" }).click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("Name").fill(renamed);
      await dialog.getByLabel("Description").fill("Renamed by e2e");
      await dialog.getByRole("button", { name: "Save" }).click();
      await dialog.waitFor({ state: "detached", timeout: 8000 });
      await page.reload();
      await H.gotoSection(page, baseUrl, ID);
      check(
        (await row(page, renamed).count()) === 1,
        "renamed label shown after reload",
      );
      const api = await labelByName(page, renamed);
      check(
        api && api.description === "Renamed by e2e",
        "rename + description read back from API",
      );
    });

    await step("colour via swatch (instant)", async () => {
      await page
        .getByRole("button", { name: new RegExp(`^Colour of ${renamed}`) })
        .click();
      await page.getByRole("radio", { name: "Green" }).click();
      await row(page, renamed).getByText("Saved").waitFor({ timeout: 6000 });
      check(true, "colour change shows Saved");
      check(
        (await labelByName(page, renamed)).color === "#22c55e",
        "green colour read back from API",
      );
    });

    await step("colour via ⋯ menu", async () => {
      await page
        .getByRole("button", { name: `Actions for ${renamed}` })
        .click();
      await page.getByRole("menuitem", { name: "Change colour" }).click();
      await page
        .getByRole("radiogroup", { name: `Colour for ${renamed}` })
        .waitFor({ timeout: 4000 });
      await page.getByRole("radio", { name: "Red" }).click();
      await page.waitForTimeout(800);
      check(
        (await labelByName(page, renamed)).color === "#ef4444",
        "Change colour from the menu opens the picker and saves",
      );
    });

    await step("colour failure rolls back", async () => {
      const restore = await H.failNextGraphql(page, /LabelUpdate/);
      await page
        .getByRole("button", { name: new RegExp(`^Colour of ${renamed}`) })
        .click();
      await page.getByRole("radio", { name: "Violet" }).click();
      await row(page, renamed).getByRole("alert").waitFor({ timeout: 6000 });
      check(true, "failed colour save shows an error on the row");
      const btn = await page
        .getByRole("button", { name: new RegExp(`^Colour of ${renamed}`) })
        .getAttribute("aria-label");
      check(/Red/.test(btn), `swatch rolled back to Red (${btn})`);
      check(
        await row(page, renamed)
          .getByRole("button", { name: "Retry" })
          .isVisible(),
        "Retry offered",
      );
      await restore();
      check(
        (await labelByName(page, renamed)).color === "#ef4444",
        "API still has Red",
      );
    });

    if (screenshotsDir)
      await H.screenshots(browser, baseUrl, adminToken, ID, screenshotsDir);

    await step("member is read-only", async () => {
      const m = await H.openPage(browser, baseUrl, memberToken);
      await H.gotoSection(m, baseUrl, ID);
      check(
        await m.getByText("You can use labels but not change them").isVisible(),
        "member sees the locked notice",
      );
      check(
        (await m.getByRole("button", { name: "New label" }).count()) === 0,
        "member has no New label button",
      );
      check(
        (await m
          .getByRole("button", { name: `Actions for ${renamed}` })
          .count()) === 0,
        "member has no row actions",
      );
      check(
        (await m.getByText(renamed, { exact: true }).count()) === 1,
        "member still sees the label",
      );
      if (screenshotsDir)
        await m.screenshot({
          path: `${screenshotsDir}/${ID}-member.png`,
          fullPage: true,
        });
      await m.context().close();
    });

    await step("delete", async () => {
      for (const n of [renamed, teamLabel]) {
        await page.getByRole("button", { name: `Actions for ${n}` }).click();
        await page.getByRole("menuitem", { name: "Delete…" }).click();
        const dlg = page.getByRole("alertdialog");
        await dlg
          .getByText(/No task uses it|task uses it|tasks use it|loses it/)
          .waitFor({ timeout: 6000 });
        check(true, `delete confirm for ${n} states the consequence`);
        await dlg.getByRole("button", { name: "Delete label" }).click();
        await dlg.waitFor({ state: "detached", timeout: 8000 });
        check(!(await labelByName(page, n)), `${n} deleted in the API`);
      }
      await page.reload();
      await H.gotoSection(page, baseUrl, ID);
      check(
        (await page.getByText(renamed, { exact: true }).count()) === 0,
        "deleted label gone after reload",
      );
    });

    await H.noOverflow(browser, baseUrl, adminToken, ID, check);
    // A circular import in src/agents (defaultDisplay TDZ) throws on some loads; it is outside settings.
    const errs = page.errors.filter((e) => !/defaultDisplay/.test(e));
    check(errs.length === 0, `no page errors (${errs.join("; ")})`);
  } finally {
    await browser.close();
  }
  return { passed, failed };
}

module.exports = { run };
if (require.main === module) H.cli(run, ID);
