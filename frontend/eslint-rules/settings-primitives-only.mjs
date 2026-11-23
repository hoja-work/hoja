// Local ESLint rule: settings screens are composed from the design system only.
//
// Files under src/settings/sections/ may not render native form controls or hand-rolled buttons.
// They use src/components/ui (Radix primitives) and src/settings/ui (settings composites), so
// every setting gets the same focus ring, touch target, save feedback and keyboard behaviour.
// Also refuses the legacy one-off pieces those primitives replace.
const NATIVE = new Set(['input', 'select', 'textarea', 'button']);
const LEGACY_IMPORTS = [/(^|\/)SettingsSelect$/, /(^|\/)dialogs$/, /(^|\/)main$/];

export default {
  rules: {
    'settings-primitives-only': {
      meta: {
        type: 'problem',
        docs: { description: 'Settings sections use components/ui and settings/ui, not native controls' },
        schema: [],
        messages: {
          native: '<{{name}}> in a settings section. Use the matching primitive from components/ui or a composite from settings/ui.',
          legacy: 'Settings sections may not import {{source}}. Use settings/ui (gql, ConfirmAction, Select) instead.',
        },
      },
      create(context) {
        const file = (context.filename || context.getFilename()).replaceAll('\\', '/');
        if (!file.includes('/src/settings/sections/')) return {};
        return {
          JSXOpeningElement(node) {
            if (node.name.type === 'JSXIdentifier' && NATIVE.has(node.name.name))
              context.report({ node, messageId: 'native', data: { name: node.name.name } });
          },
          ImportDeclaration(node) {
            const source = String(node.source.value);
            if (LEGACY_IMPORTS.some((re) => re.test(source)))
              context.report({ node, messageId: 'legacy', data: { source } });
          },
        };
      },
    },
  },
};
