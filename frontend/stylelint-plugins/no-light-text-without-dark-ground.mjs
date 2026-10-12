// Custom stylelint rule.
//
// Context: the sidebar team icon rendered as light text on a light sidebar in some themes because
// a selector set a light color (white or near-white) while its background came from an inherited or
// theme-variable surface that is light. A declaration that sets a light text color must also set an
// explicit dark or opaque background on the same rule, so the pairing is visible in the source.
import stylelint from 'stylelint';

const { createPlugin, utils } = stylelint;

const ruleName = 'clinear/no-light-text-without-dark-ground';
const messages = utils.ruleMessages(ruleName, {
  expected: (selector, value) =>
    `"${selector}" sets light text color "${value}" without its own background. Light text on a light ` +
    `surface is unreadable; set an explicit dark background on the same rule, or use a theme token.`,
});

// Light text: pure white, or hex/rgb values with every channel >= 0xE0 (224).
const WHITE_NAMES = new Set(['white', '#fff', '#ffffff']);

function isLightColor(value) {
  const v = value.trim().toLowerCase();
  if (WHITE_NAMES.has(v)) return true;
  let m = v.match(/^#([0-9a-f]{3})$/);
  if (m) return m[1].split('').every((c) => parseInt(c + c, 16) >= 0xe0);
  m = v.match(/^#([0-9a-f]{6})$/);
  if (m) return [0, 2, 4].every((i) => parseInt(m[1].slice(i, i + 2), 16) >= 0xe0);
  m = v.match(/^rgba?\((\d+)[ ,]+(\d+)[ ,]+(\d+)/);
  if (m) return [m[1], m[2], m[3]].every((n) => Number(n) >= 0xe0);
  return false;
}

const hasOwnBackground = (rule) => {
  let found = false;
  rule.walkDecls((d) => {
    const p = d.prop.toLowerCase();
    if (p === 'background' || p === 'background-color') found = true;
  });
  return found;
};

function ruleFn(primary) {
  return (root, result) => {
    if (!primary) return;
    root.walkRules((rule) => {
      // Rules inside an at-rule (e.g. @media) are still checked; only direct declarations count.
      let light = null;
      rule.walkDecls('color', (decl) => {
        if (isLightColor(decl.value)) light = decl;
      });
      if (!light || hasOwnBackground(rule)) return;
      utils.report({
        message: messages.expected(rule.selector, light.value),
        node: light,
        result,
        ruleName,
      });
    });
  };
}

ruleFn.ruleName = ruleName;
ruleFn.messages = messages;

export default createPlugin(ruleName, ruleFn);
