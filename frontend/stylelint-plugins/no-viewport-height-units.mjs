// Custom stylelint rule.
//
// Context: the mobile UX audit (scripts/mobile-audit.cjs) found full-height layouts sized with `100vh`.
// On iOS Safari and Android Chrome `100vh` is the largest viewport height, measured with the browser
// toolbars collapsed, so the bottom of the layout sits behind the address bar whenever the toolbars are shown.
// Use `100dvh` (dynamic, follows the toolbars) or `100svh` (small, toolbars expanded) instead.
import stylelint from 'stylelint';

const { createPlugin, utils } = stylelint;

const ruleName = 'clinear/no-viewport-height-units';
const messages = utils.ruleMessages(ruleName, {
  expected: (prop, value) =>
    `"${prop}: ${value}" sizes against the largest viewport, which mobile browsers hide behind their toolbars. ` +
    `Use 100dvh (or 100svh) instead.`,
});

const SIZE_PROPS = new Set(['height', 'min-height', 'max-height', 'block-size', 'min-block-size', 'max-block-size']);
const VH = /(^|[^\w.])100vh\b/;

function ruleFn(primary) {
  return (root, result) => {
    if (!primary) return;
    root.walkDecls((decl) => {
      if (!SIZE_PROPS.has(decl.prop.toLowerCase())) return;
      if (!VH.test(decl.value)) return;
      utils.report({
        message: messages.expected(decl.prop, decl.value),
        node: decl,
        result,
        ruleName,
      });
    });
  };
}

ruleFn.ruleName = ruleName;
ruleFn.messages = messages;

export default createPlugin(ruleName, ruleFn);
