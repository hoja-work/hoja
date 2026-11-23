// Cycles journey (HOJA-734): every team's real schedule and the switches the server stores.
// Turning cycles on is one-way, so the run creates its own throwaway team for it and archives
// that team afterwards. Auto-add switches save instantly, survive reload, roll back on failure;
// a member sees everything disabled with the reason.
const H = require("./lib/account-helpers.cjs");

const ID = "cycles";

async function teamFlags(page, id) {
  const d = await H.api(
    page,
    "query($id:String!){ team(id:$id){ autoAddStarted autoAddCompleted autoAddBacklog } teamCycleCadence(teamId:$id){ enabled weekday } }",
    { id },
  );
  return { ...d.data.team, cadence: d.data.teamCycleCadence };
}

async function run({ baseUrl, adminToken, memberToken, screenshotsDir }) {
  const { chromium } = H.loadPlaywright();
  const { passed, failed, check, step } = H.harness();
  const browser = await chromium.launch();
  const s = Date.now().toString(36).slice(-5).toUpperCase();
  const teamName = `E2E Cycles ${s}`;
  let teamId = null;
  const section = (page) =>
    page.getByRole("region", { name: teamName, exact: true });
  try {
    const page = await H.openPage(browser, baseUrl, adminToken);
    await H.gotoSection(page, baseUrl, ID);
    const made = await H.api(
      page,
      "mutation($i:TeamCreateInput!){teamCreate(input:$i){team{id}}}",
      { i: { name: teamName, key: `C${s}` } },
    );
    teamId = made.data?.teamCreate?.team?.id;
    check(!!teamId, "fixture team created");
    await page.reload();
    await H.gotoSection(page, baseUrl, ID);

    await step("copy is honest", async () => {
      check(
        (await page.getByText(/Length|Cooldown|Auto-complete/).count()) === 0,
        "no promise of length/cooldown/auto-complete controls",
      );
      check(
        (await section(page).getByText("No automatic cycles.").count()) === 1,
        "team shows its cadence state (off)",
      );
      check(
        (await section(page)
          .getByRole("button", { name: /cycle page/ })
          .count()) === 1,
        "link to the team cycle page",
      );
    });

    await step("auto-add switches", async () => {
      const sw = section(page).getByRole("switch", {
        name: /Add started tasks/,
      });
      check(
        (await sw.getAttribute("aria-checked")) === "true",
        "started default on",
      );
      await sw.click();
      await section(page).getByText("Saved").first().waitFor({ timeout: 6000 });
      await section(page)
        .getByRole("switch", { name: /Add tasks moved to Backlog/ })
        .click();
      await page.waitForTimeout(800);
      const f = await teamFlags(page, teamId);
      check(
        f.autoAddStarted === false &&
          f.autoAddBacklog === false &&
          f.autoAddCompleted === true,
        "switches saved independently to the API",
      );
      await page.reload();
      await H.gotoSection(page, baseUrl, ID);
      check(
        (await section(page)
          .getByRole("switch", { name: /Add started tasks/ })
          .getAttribute("aria-checked")) === "false",
        "started off survives reload",
      );
      check(
        (await section(page)
          .getByRole("switch", { name: /Add tasks moved to Backlog/ })
          .getAttribute("aria-checked")) === "false",
        "backlog off survives reload",
      );
    });

    await step("switch failure rolls back", async () => {
      const restore = await H.failNextGraphql(page, /CycleAutomation/);
      await section(page)
        .getByRole("switch", { name: /Add completed tasks/ })
        .click();
      await section(page).getByRole("alert").waitFor({ timeout: 6000 });
      check(true, "failed toggle shows the error on the row");
      check(
        (await section(page)
          .getByRole("switch", { name: /Add completed tasks/ })
          .getAttribute("aria-checked")) === "true",
        "switch snapped back on",
      );
      await restore();
      check(
        (await teamFlags(page, teamId)).autoAddCompleted === true,
        "API unchanged",
      );
    });

    await step("turn on cycles", async () => {
      await section(page).getByRole("button", { name: "Turn on…" }).click();
      const dialog = page.getByRole("dialog");
      check(
        (await dialog.getByText(/can't be undone yet/).count()) === 1,
        "dialog warns the change is one-way",
      );
      await dialog.getByLabel("Cycles start on").click();
      await page.getByRole("option", { name: "Wednesday" }).click();
      const restore = await H.failNextGraphql(page, /CadenceEnable/);
      await dialog.getByRole("button", { name: "Turn on cycles" }).click();
      await dialog
        .getByRole("alert")
        .filter({ hasText: /Simulated failure/ })
        .waitFor({ timeout: 6000 });
      check(true, "failed enable keeps the dialog open with the reason");
      await restore();
      check(
        (await teamFlags(page, teamId)).cadence.enabled === false,
        "failed enable changed nothing",
      );
      await dialog.getByRole("button", { name: "Turn on cycles" }).click();
      await dialog.waitFor({ state: "detached", timeout: 10000 });
      const f = await teamFlags(page, teamId);
      check(
        f.cadence.enabled === true && f.cadence.weekday === 2,
        "cadence on, Wednesday, in the API",
      );
      await page.reload();
      await H.gotoSection(page, baseUrl, ID);
      check(
        (await section(page)
          .getByText(/Cycles start every Wednesday/)
          .count()) === 1,
        "cadence state shown after reload",
      );
      check(
        (await section(page)
          .getByText(
            "Changing the start day or turning cycles off isn't available yet.",
          )
          .count()) === 1,
        "says it cannot be changed yet",
      );
    });

    if (screenshotsDir)
      await H.screenshots(browser, baseUrl, adminToken, ID, screenshotsDir);

    await step("member is read-only", async () => {
      const m = await H.openPage(browser, baseUrl, memberToken);
      await H.gotoSection(m, baseUrl, ID);
      check(
        await m
          .getByText("Only workspace admins can change cycles")
          .isVisible(),
        "member sees the locked notice",
      );
      const switches = m.getByRole("switch");
      const n = await switches.count();
      let disabled = 0;
      for (let i = 0; i < n; i++)
        if (await switches.nth(i).isDisabled()) disabled++;
      check(
        n > 0 && disabled === n,
        `member switches all disabled (${disabled}/${n})`,
      );
      check(
        (await m.getByRole("button", { name: "Turn on…" }).count()) === 0,
        "member has no Turn on button",
      );
      if (screenshotsDir)
        await m.screenshot({
          path: `${screenshotsDir}/${ID}-member.png`,
          fullPage: true,
        });
      await m.context().close();
    });

    await H.noOverflow(browser, baseUrl, adminToken, ID, check);
    const errs = page.errors.filter((e) => !/defaultDisplay/.test(e));
    check(errs.length === 0, `no page errors (${errs.join("; ")})`);
    if (teamId)
      await H.api(page, "mutation($id:String!){teamArchive(id:$id){success}}", {
        id: teamId,
      });
  } finally {
    await browser.close();
  }
  return { passed, failed };
}

module.exports = { run };
if (require.main === module) H.cli(run, ID);
