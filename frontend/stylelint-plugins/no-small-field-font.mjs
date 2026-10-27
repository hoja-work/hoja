// Custom stylelint rule.
//
// Context: the mobile UX audit (scripts/mobile-audit.cjs) found 200+ form fields set below 16px. iOS Safari
// zooms the whole page when a field under 16px takes focus, and the zoom does not reset on blur. Desktop sizes
// stay as they are, so any stylesheet that sizes a field below 16px must also carry the phone guard:
//   @media (max-width: 700px) { input, select, textarea { font-size: 16px !important } }
// The guard is what makes a small desktop field safe on phones, so a stylesheet that drops it is reported.
import stylelint from 'stylelint';

const { createPlugin, utils } = stylelint;

const ruleName = 'clinear/no-small-field-font';
const messages = utils.ruleMessages(ruleName, {
  expected: (selector, value) =>
    `"${selector}" sets field font-size ${value}, under 16px. iOS zooms the page when this field is focused. ` +
    `Keep the phone guard "@media (max-width: 700px) { input, select, textarea { font-size: 16px !important } }" in this stylesheet.`,
});

const FIELD = /(^|[\s>+~,(])(input|select|textarea)\b/;

// The guard is a max-width media query whose rule targets input/select/textarea with font-size 16px !important.
function hasPhoneGuard(root) {
  let found = false;
  root.walkAtRules('media', (at) => {
    if (!/max-width/.test(at.params)) return;
    at.walkRules((rule) => {
      if (!FIELD.test(rule.selector)) return;
      rule.walkDecls('font-size', (decl) => {
        if (decl.value.trim() === '16px' && decl.important) found = true;
      });
    });
  });
  return found;
}

function ruleFn(primary) {
  return (root, result) => {
    if (!primary) return;
    const guarded = hasPhoneGuard(root);
    root.walkRules((rule) => {
      if (!FIELD.test(rule.selector)) return;
      rule.walkDecls('font-size', (decl) => {
        const px = decl.value.trim().match(/^(\d+(?:\.\d+)?)px$/);
        if (!px || Number(px[1]) >= 16 || guarded) return;
        utils.report({ message: messages.expected(rule.selector, decl.value), node: decl, result, ruleName });
      });
    });
  };
}

ruleFn.ruleName = ruleName;
ruleFn.messages = messages;

export default createPlugin(ruleName, ruleFn);
