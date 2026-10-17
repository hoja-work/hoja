// Local ESLint rule: no native browser dialogs.
//
// window.confirm, window.prompt and window.alert are unstyled, block the whole page, and behave differently across
// phone browsers. Use the in-app dialogs in src/dialogs.tsx (confirmDialog, promptDialog) instead.
const BLOCKED = new Set(['alert', 'confirm', 'prompt']);
const GLOBALS = new Set(['window', 'globalThis', 'self']);

export default {
  rules: {
    'no-native-dialog': {
      meta: {
        type: 'problem',
        docs: { description: 'Use the in-app dialogs instead of window.confirm, window.prompt or window.alert' },
        schema: [],
        messages: {
          native: '{{name}} is a native browser dialog. Use confirmDialog or promptDialog from src/dialogs.tsx.',
        },
      },
      create(context) {
        return {
          CallExpression(node) {
            const callee = node.callee;
            if (callee.type === 'Identifier' && BLOCKED.has(callee.name)) {
              context.report({ node, messageId: 'native', data: { name: callee.name } });
              return;
            }
            if (callee.type === 'MemberExpression' && !callee.computed
                && callee.object.type === 'Identifier' && GLOBALS.has(callee.object.name)
                && callee.property.type === 'Identifier' && BLOCKED.has(callee.property.name)) {
              context.report({ node, messageId: 'native', data: { name: `${callee.object.name}.${callee.property.name}` } });
            }
          },
        };
      },
    },
  },
};
