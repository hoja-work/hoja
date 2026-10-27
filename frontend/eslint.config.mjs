// Lint for mobile-facing UI. Run with `npm run lint:ui`.
//
// The mobile UX audit (scripts/mobile-audit.cjs) found controls that a phone user cannot use: icon-only buttons with no
// name, controls that only respond to a mouse, and labels not tied to fields. These rules stop those from being built
// again. Layout (sizes, overlaps) is checked at runtime by `npm run test:mobile`, because a linter cannot see rendered
// geometry; CSS invariants live in stylelint (`npm run lint:styles`).
import tsParser from '@typescript-eslint/parser';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import reactHooks from 'eslint-plugin-react-hooks';
import localRules from './eslint-rules/index.mjs';

export default [
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      parser: tsParser,
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: { 'jsx-a11y': jsxA11y, 'react-hooks': reactHooks, local: localRules },
    rules: {
      // A tap must do something: no button without a handler, disabled state or submit type.
      'local/no-dead-button': 'error',
      // Native confirm, prompt and alert are unstyled and block the page; use the in-app dialogs.
      'local/no-native-dialog': 'error',
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'error',
      // An icon-only button must have a name a screen reader and a phone's voice control can use.
      // Form fields are covered by label-has-associated-control below, so they are ignored here (the rule default).
      'jsx-a11y/control-has-associated-label': ['error', { labelAttributes: ['aria-label', 'title'], depth: 3, ignoreElements: ['audio', 'canvas', 'embed', 'input', 'textarea', 'tr', 'video'] }],
      // Clickable non-buttons must be reachable and operable by keyboard and assistive tech.
      'jsx-a11y/click-events-have-key-events': 'error',
      'jsx-a11y/no-static-element-interactions': 'error',
      'jsx-a11y/interactive-supports-focus': 'error',
      // Every field needs a label a user can tap to focus.
      'jsx-a11y/label-has-associated-control': ['error', { assert: 'either' }],
      // Components that forward autoFocus to their own input are allowed; DOM autofocus is not.
      'jsx-a11y/no-autofocus': ['error', { ignoreNonDOM: true }],
      'jsx-a11y/alt-text': 'error',
      'jsx-a11y/anchor-is-valid': 'error',
    },
  },
];
