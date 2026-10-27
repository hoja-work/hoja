import noDeadButton from './no-dead-button.mjs';
import noNativeDialog from './no-native-dialog.mjs';

export default {
  rules: {
    'no-dead-button': noDeadButton.rules['no-dead-button'],
    'no-native-dialog': noNativeDialog.rules['no-native-dialog'],
  },
};
