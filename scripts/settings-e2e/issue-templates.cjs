// Task templates journey (HOJA-734): the label/project pickers load (the legacy query asked
// for a non-existent IssueLabel.team and came back empty), priority can stay unset, scope
// changes the usable labels, create/edit/duplicate/archive with read-back, forced failure,
// and the admin-only gate.
const H = require("./lib/account-helpers.cjs");

const ID = "issue-templates";
const LIST =
  "{ taskTemplates { id name description team { id } defaultTitle defaultDescription defaultPriority defaultAssignee { name } defaultProject { name } defaultLabels { name } } }";

async function template(page, name) {
  const d = await H.api(page, LIST);
  return (d.data?.taskTemplates || []).find((t) => t.name === name);
}

async function run({ baseUrl, adminToken, memberToken, screenshotsDir }) {
  const { chromium } = H.loadPlaywright();
  const { passed, failed, check, step } = H.harness();
  const browser = await chromium.launch();
  const s = Date.now().toString(36);
  const name = `E2E template ${s}`;
  const wsLabel = `E2E ws ${s}`;
  const teamLabel = `E2E eng ${s}`;
  const project = `E2E project ${s}`;
  const cleanup = { labels: [], project: null };
  const pick = async (page, scope, label, option) => {
    await scope.getByLabel(label, { exact: true }).click();
    await page.getByRole("option", { name: option, exact: true }).click();
  };
  try {
    const page = await H.openPage(browser, baseUrl, adminToken);
    await H.gotoSection(page, baseUrl, ID);

    // Fixtures through the API: a workspace label, an Engineering label and a project.
    const teamId = (await H.api(page, "{ teams { nodes { id } } }")).data.teams
      .nodes[0].id;
    for (const [n, t] of [
      [wsLabel, null],
      [teamLabel, teamId],
    ]) {
      const r = await H.api(
        page,
        "mutation($i:IssueLabelCreateInput!){issueLabelCreate(input:$i){issueLabel{id}}}",
        { i: { name: n, color: "#3b82f6", ...(t ? { teamId: t } : {}) } },
      );
      cleanup.labels.push(r.data.issueLabelCreate.issueLabel.id);
    }
    const pr = await H.api(
      page,
      "mutation($i:ProjectCreateInput!){projectCreate(input:$i){project{id}}}",
      { i: { name: project, teamIds: [teamId] } },
    );
    cleanup.project = pr.data?.projectCreate?.project?.id;
    await page.reload();
    await H.gotoSection(page, baseUrl, ID);

    await step("create", async () => {
      await page
        .getByRole("button", { name: /^(New template|Create a template)$/ })
        .first()
        .click();
      const dialog = page.getByRole("dialog");
      await dialog.getByLabel("Template name").fill(name);
      await dialog
        .getByRole("textbox", { name: /^Description/ })
        .nth(0)
        .fill("E2E description");
      await dialog.getByLabel("Title", { exact: true }).fill("Bug: ");
      await dialog
        .getByRole("textbox", { name: /^Description/ })
        .nth(1)
        .fill("## Steps");
      check(
        (await dialog
          .getByRole("checkbox", { name: new RegExp(wsLabel) })
          .count()) === 1,
        "label picker lists workspace labels (was empty)",
      );
      check(
        (await dialog
          .getByRole("checkbox", { name: new RegExp(teamLabel) })
          .count()) === 0,
        "team label hidden for a workspace template",
      );
      await dialog.getByRole("checkbox", { name: new RegExp(wsLabel) }).click();
      await pick(page, dialog, "Project", project);
      check(true, "project picker lists projects (was empty)");
      await pick(page, dialog, "Assignee", "Max Member");
      await dialog.getByRole("button", { name: "Create template" }).click();
      await dialog.waitFor({ state: "detached", timeout: 8000 });
      check(
        (await page.getByText(name, { exact: true }).count()) === 1,
        "template listed",
      );
    });

    await page.reload();
    await H.gotoSection(page, baseUrl, ID);
    await step("read back", async () => {
      const t = await template(page, name);
      check(
        !!t && t.defaultTitle === "Bug: ",
        "default title stored verbatim (trailing space kept)",
      );
      check(
        t.defaultPriority === null,
        `priority left unset (${t.defaultPriority})`,
      );
      check(
        t.defaultProject?.name === project &&
          t.defaultAssignee?.name === "Max Member",
        "project + assignee saved",
      );
      check(
        t.defaultLabels.map((l) => l.name).join() === wsLabel,
        "label saved",
      );
      check(
        t.defaultDescription === "## Steps" &&
          t.description === "E2E description",
        "descriptions saved",
      );
    });

    await step("edit: team scope + priority", async () => {
      await page
        .getByRole("button", { name: `Actions for ${name}`, exact: true })
        .click();
      await page.getByRole("menuitem", { name: "Edit…" }).click();
      const dialog = page.getByRole("dialog");
      await pick(page, dialog, "Available to", "Engineering only");
      await dialog
        .getByRole("checkbox", { name: new RegExp(teamLabel) })
        .click();
      await pick(page, dialog, "Priority", "High");
      // Forced failure: dialog stays open and explains.
      const restore = await H.failNextGraphql(page, /TemplateUpdate/);
      await dialog.getByRole("button", { name: "Save template" }).click();
      await dialog
        .getByRole("alert")
        .filter({ hasText: /Simulated failure/ })
        .waitFor({ timeout: 6000 });
      check(true, "failed save keeps the dialog open with the reason");
      await restore();
      check(
        (await template(page, name)).defaultPriority === null,
        "failed save changed nothing",
      );
      await dialog.getByRole("button", { name: "Save template" }).click();
      await dialog.waitFor({ state: "detached", timeout: 8000 });
      const t = await template(page, name);
      check(
        t.team?.id === teamId && t.defaultPriority === 2,
        "team scope + High saved",
      );
      check(
        t.defaultLabels
          .map((l) => l.name)
          .sort()
          .join() === [teamLabel, wsLabel].sort().join(),
        "team label usable once scoped to the team",
      );
      await page.reload();
      await H.gotoSection(page, baseUrl, ID);
      check(
        (await page
          .getByRole("region", { name: "Engineering" })
          .getByText(name, { exact: true })
          .count()) === 1,
        "template grouped under Engineering after reload",
      );
    });

    await step("clear priority", async () => {
      await page
        .getByRole("button", { name: new RegExp(`^${name}`) })
        .first()
        .click();
      const dialog = page.getByRole("dialog");
      await pick(page, dialog, "Priority", "Not set");
      await dialog.getByRole("button", { name: "Save template" }).click();
      await dialog.waitFor({ state: "detached", timeout: 8000 });
      check(
        (await template(page, name)).defaultPriority === null,
        "priority can be cleared back to unset",
      );
    });

    await step("duplicate", async () => {
      await page
        .getByRole("button", { name: `Actions for ${name}`, exact: true })
        .click();
      await page.getByRole("menuitem", { name: "Duplicate…" }).click();
      const dialog = page.getByRole("dialog");
      check(
        (await dialog.getByLabel("Template name").inputValue()) ===
          `${name} (copy)`,
        "duplicate prefills a copy name",
      );
      await dialog.getByRole("button", { name: "Create template" }).click();
      await dialog.waitFor({ state: "detached", timeout: 8000 });
      check(!!(await template(page, `${name} (copy)`)), "duplicate created");
    });

    if (screenshotsDir)
      await H.screenshots(browser, baseUrl, adminToken, ID, screenshotsDir);
    if (screenshotsDir) {
      await page
        .getByRole("button", { name: `Actions for ${name}`, exact: true })
        .click();
      await page.getByRole("menuitem", { name: "Edit…" }).click();
      await page.getByRole("dialog").waitFor();
      await page.screenshot({ path: `${screenshotsDir}/${ID}-dialog.png` });
      await page.getByRole("button", { name: "Cancel" }).click();
    }

    await step("archive", async () => {
      for (const n of [`${name} (copy)`, name]) {
        await page
          .getByRole("button", { name: `Actions for ${n}`, exact: true })
          .click();
        await page.getByRole("menuitem", { name: "Archive…" }).click();
        const dlg = page.getByRole("alertdialog");
        await dlg.getByRole("button", { name: "Archive template" }).click();
        await dlg.waitFor({ state: "detached", timeout: 8000 });
        check(!(await template(page, n)), `${n} archived in API`);
      }
      await page.reload();
      await H.gotoSection(page, baseUrl, ID);
      check(
        (await page.getByText(name, { exact: true }).count()) === 0,
        "archived template gone after reload",
      );
    });

    await step("member gate", async () => {
      const m = await H.openPage(browser, baseUrl, memberToken);
      await H.gotoSection(m, baseUrl, ID);
      check(
        await m
          .getByText("Only workspace admins can open this page")
          .isVisible(),
        "member sees the admin-only page",
      );
      await m.context().close();
    });

    await H.noOverflow(browser, baseUrl, adminToken, ID, check);
    const errs = page.errors.filter((e) => !/defaultDisplay/.test(e));
    check(errs.length === 0, `no page errors (${errs.join("; ")})`);

    for (const id of cleanup.labels)
      await H.api(
        page,
        "mutation($id:String!){issueLabelDelete(id:$id){success}}",
        { id },
      );
    if (cleanup.project)
      await H.api(
        page,
        "mutation($id:String!){projectArchive(id:$id){success}}",
        { id: cleanup.project },
      );
  } finally {
    await browser.close();
  }
  return { passed, failed };
}

module.exports = { run };
if (require.main === module) H.cli(run, ID);
