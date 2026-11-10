// HOJA-288: view identifiers are routing/state constants — they are compared with
// `view === ...` and passed to navigate(). They must not change. Display labels are
// derived from them here instead, so renaming the vocabulary cannot break navigation.
const DISPLAY_LABELS: Record<string, string> = {
  'Issues': 'Tasks',
  'My issues': 'My tasks',
  'Team issues': 'Team tasks',
  'All issues': 'All tasks',
  'Active issues': 'Active tasks',
  'Recent issues': 'Recent tasks',
  'New issues': 'New tasks',
  'Favorite views': 'Saved views',
};

export function displayLabel(view: string): string {
  return DISPLAY_LABELS[view] ?? view;
}
