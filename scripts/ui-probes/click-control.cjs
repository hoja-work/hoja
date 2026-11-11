// Shared "click the control whose visible label is X" helper for scripts/ui-probes/*.cjs.
// My issues and Team issues tabs are <button role="tab">, and an explicit role overrides the
// implicit button role in the accessibility tree, so getByRole('button', {name}) can never match
// them no matter how the name is spelled. Resolve by tab role first, then button role, then by
// exact text inside the tab strip, and fail fast with the labels that are actually on the page.
const DEFAULT_TIMEOUT = 5000;

const escapeRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const strategies = label => ([
  { name: 'role=tab', locate: page => page.getByRole('tab', { name: label, exact: true }) },
  { name: 'role=button', locate: page => page.getByRole('button', { name: label, exact: true }) },
  { name: 'tab-strip-text', locate: page => page.locator('.my-issue-tabs > button', { hasText: new RegExp('^' + escapeRe(label) + '$') }) },
]);

async function visibleLabels(page) {
  const tabs = await page.getByRole('tab').allInnerTexts().catch(() => []);
  const strip = await page.locator('.my-issue-tabs > button').allInnerTexts().catch(() => []);
  return [...new Set([...tabs, ...strip].map(t => t.trim()).filter(Boolean))];
}

// Returns the strategy name that matched, so callers can log how the control was found.
async function clickControl(page, label, { timeout = DEFAULT_TIMEOUT } = {}) {
  const tried = [];
  for (const s of strategies(label)) {
    const loc = s.locate(page).first();
    if (!(await loc.count())) { tried.push(`${s.name}: absent`); continue; }
    try {
      await loc.click({ timeout });
      return s.name;
    } catch (e) {
      tried.push(`${s.name}: ${String(e.message).split('\n')[0].slice(0, 120)}`);
    }
  }
  const available = await visibleLabels(page);
  throw new Error(`no clickable control named ${JSON.stringify(label)} (tried ${tried.join(' | ')}); available: ${available.length ? available.map(l => JSON.stringify(l)).join(', ') : 'none'}`);
}

module.exports = { clickControl, visibleLabels, escapeRe, DEFAULT_TIMEOUT };
