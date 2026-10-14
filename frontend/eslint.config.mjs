import js from '@eslint/js';
import tsParser from '@typescript-eslint/parser';
import i18next from 'eslint-plugin-i18next';

// Blocks hardcoded user-facing text in src. Every visible string must go through
// tr() and have en+es catalog keys. Code-shaped literals (URLs, API paths, GraphQL
// documents, storage keys, enum/state IDs, CSS values, callee names) are excluded.
export default [
  {ignores: ['dist/**', 'node_modules/**', 'stylelint-plugins/**', 'src/i18n/locales/**']},
  js.configs.recommended,
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {parser: tsParser, parserOptions: {ecmaFeatures: {jsx: true}}},
    plugins: {i18next},
    rules: {
      'no-undef': 'off',
      'no-unused-vars': 'off',
      'i18next/no-literal-string': ['error', {
        mode: 'all',
        'should-validate-template': true,
        callees: {
          exclude: ['tr', 't', 'i18next(ext)?', 'gql', 'require', 'addEventListener', 'removeEventListener',
            'postMessage', 'getElementById', 'dispatch', 'commit', 'includes', 'indexOf', 'endsWith',
            'startsWith', 'split', 'replace', 'fetch', 'setItem', 'getItem', 'removeItem', 'navigate',
            'setView', 'Error', 'Date', 'Number', 'String', 'parseInt', 'parseFloat', 'match', 'test',
            'querySelector', 'querySelectorAll', 'setAttribute', 'getAttribute', 'hasAttribute',
            'toLocaleString', 'toLocaleDateString', 'toLocaleTimeString', 'savePreference',
            'JSON\\..*', 'console\\..*', 'localStorage\\..*', 'window\\..*', 'document\\..*',
            'Object\\..*', 'Array\\..*', 'Math\\..*', 'Promise\\..*', 'Intl\\..*', 'URL.*', 'URLSearchParams'],
        },
        words: {
          exclude: ['[0-9!-/:-@[-`{-~]+', '[A-Z_-]+', '^\\p{Emoji}+$', '^[^A-Za-z]*$', '^[a-z0-9_.\\-/]+$', '^#[0-9a-fA-F]{3,8}$'],
        },
        'object-properties': {exclude: ['[A-Z_-]+']},
        'class-properties': {exclude: ['.*']},
      }],
    },
  },
];
