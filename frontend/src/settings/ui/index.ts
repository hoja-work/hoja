// The settings design system's public surface. Settings screens import from here and
// from ../../components/ui only (enforced by local/settings-primitives-only).
export {
  SettingsPage,
  SettingsSection,
  SettingsCard,
  SettingRow,
  SaveStatusLine,
  Notice,
  EmptyState,
  ErrorState,
  LoadingRows,
  Loadable,
  ResourceRow,
  Badge,
  CopyField,
} from "./layout";
export { ConfirmAction, FormDialog, Field, DangerZone } from "./dialogs";
export {
  useSetting,
  useSettingText,
  useResource,
  useAction,
  errorMessage,
  type SaveStatus,
  type SaveState,
} from "./useSetting";
export { gql, rest } from "./api";
