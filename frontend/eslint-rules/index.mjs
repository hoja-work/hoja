import noDeadButton from './no-dead-button.mjs';
import noNativeDialog from './no-native-dialog.mjs';
import settingsPrimitivesOnly from './settings-primitives-only.mjs';

export default {
  rules: {
    'no-dead-button': noDeadButton.rules['no-dead-button'],
    'no-native-dialog': noNativeDialog.rules['no-native-dialog'],
    'settings-primitives-only': settingsPrimitivesOnly.rules['settings-primitives-only'],
  },
};
