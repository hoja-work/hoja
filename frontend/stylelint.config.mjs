// Structural errors rather than formatting: legacy CSS is intentionally compact.
import noUnstretchedChromeWrapper from './stylelint-plugins/no-unstretched-chrome-wrapper.mjs';
import noLightTextWithoutDarkGround from './stylelint-plugins/no-light-text-without-dark-ground.mjs';
import noViewportHeightUnits from './stylelint-plugins/no-viewport-height-units.mjs';
import noSmallFieldFont from './stylelint-plugins/no-small-field-font.mjs';

export default {
  plugins: [noUnstretchedChromeWrapper, noLightTextWithoutDarkGround, noViewportHeightUnits, noSmallFieldFont],
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
    'clinear/no-light-text-without-dark-ground': true,
    'clinear/no-viewport-height-units': true,
    'clinear/no-small-field-font': true,
  },
};
