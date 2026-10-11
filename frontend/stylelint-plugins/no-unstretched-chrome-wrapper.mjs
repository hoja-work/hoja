// Custom stylelint rule.
//
// Context: a real bug shipped in the agent chat composer. The project's
// override CSS gave visual "chrome" (border/border-radius/box-shadow/
// background) to a BEM child selector like `.foo__bar` while its direct
// parent `.foo` was a flex/grid container that never stretched that child
// (no `flex: 1`, `width: 100%`, `align-self: stretch`, etc). When the
// library's DOM nested an extra wrapper div, the "chrome" element
// shrink-wrapped to its content instead of filling the container, making
// the composer collapse to about half width after the first message.
//
// This rule looks for that specific shape across the whole stylesheet so
// the same mistake is caught automatically in any other message/composer
// style block, not just the one we happened to notice by eye.
import stylelint from 'stylelint';

const { createPlugin, utils } = stylelint;

const ruleName = 'clinear/no-unstretched-chrome-wrapper';
const messages = utils.ruleMessages(ruleName, {
  expected: (child, parent) =>
    `"${child}" carries visual chrome (border/shadow/background) but its parent "${parent}" ` +
    `does not stretch it (no width:100%/flex:1/align-self:stretch). This reproduces the ` +
    `composer shrink-after-send bug: the child will shrink-wrap to its content instead of ` +
    `filling the container. Add width:100%/flex:1/align-self:stretch to the parent, or to the child itself.`,
});

const CHROME_PROPS = ['border', 'border-radius', 'box-shadow', 'background', 'background-color'];
const STRETCH_DECL = /(^|[^-])\b(width\s*:\s*100%|flex\s*:\s*1|flex-grow\s*:\s*1|align-self\s*:\s*stretch)/;

// Given a selector like `.foo__bar-baz`, return its BEM "block" prefix
// (everything before the first `__`), or null if it isn't a BEM element.
function bemParent(selector) {
  const trimmed = selector.trim();
  const match = trimmed.match(/^(\.[a-zA-Z0-9-]+)__[a-zA-Z0-9-]+$/);
  return match ? match[1] : null;
}

function ruleFn(primary) {
  return (root, result) => {
    if (!primary) return;

    const chromeRules = new Map(); // selector -> rule node
    const parentStretch = new Map(); // selector -> boolean (has a stretch declaration anywhere)
    const allSelectors = new Set();

    root.walkRules((rule) => {
      const selectors = rule.selector.split(',').map((s) => s.trim());
      let hasChrome = false;
      let hasStretch = false;

      rule.walkDecls((decl) => {
        const prop = decl.prop.toLowerCase();
        if (CHROME_PROPS.some((p) => prop === p || prop.startsWith(`${p}-`))) {
          hasChrome = true;
        }
        const declText = `${decl.prop}:${decl.value}`;
        if (STRETCH_DECL.test(declText)) {
          hasStretch = true;
        }
      });

      for (const sel of selectors) {
        allSelectors.add(sel);
        if (hasChrome && !chromeRules.has(sel)) chromeRules.set(sel, rule);
        if (hasStretch) parentStretch.set(sel, true);
      }
    });

    for (const [childSel, rule] of chromeRules) {
      const parentBem = bemParent(childSel);
      if (!parentBem) continue;
      if (!allSelectors.has(parentBem)) continue; // parent block has no rule of its own at all
      if (parentStretch.get(parentBem) || parentStretch.get(childSel)) continue;

      utils.report({
        message: messages.expected(childSel, parentBem),
        node: rule,
        result,
        ruleName,
      });
    }
  };
}

ruleFn.ruleName = ruleName;
ruleFn.messages = messages;

export default createPlugin(ruleName, ruleFn);
