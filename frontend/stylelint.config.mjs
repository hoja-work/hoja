// Structural errors rather than formatting: legacy CSS is intentionally compact.
import noUnstretchedChromeWrapper from './stylelint-plugins/no-unstretched-chrome-wrapper.mjs';

export default {
  plugins: [noUnstretchedChromeWrapper],
  rules: {
    'block-no-empty': true,
    'color-no-invalid-hex': true,
    'declaration-block-no-duplicate-properties': true,
    'declaration-block-no-shorthand-property-overrides': true,
    'function-calc-no-unspaced-operator': true,
    'function-linear-gradient-no-nonstandard-direction': true,
    'no-invalid-double-slash-comments': true,
    'property-no-unknown': true,
    'selector-pseudo-class-no-unknown': true,
    'selector-pseudo-element-no-unknown': true,
    'unit-no-unknown': true,
    'clinear/no-unstretched-chrome-wrapper': true,
  },
};
