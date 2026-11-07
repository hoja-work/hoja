// Field rules for the setup wizard.
//
// They mirror what the server enforces today rather than inventing a stricter
// product rule: `/onboarding/bootstrap` accepts a workspace key of letters,
// digits and hyphens and a team key of alphanumerics. Everything here is pure so
// the same rules can be tested without a DOM, and the wizard never has to rely
// on an HTML `pattern` attribute to be correct.

export type FieldIssue = {field: string; message: string};

const ADDRESS = /^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$/;
const TEAM_KEY = /^[A-Z0-9]{1,8}$/;

export function checkWorkspaceName(value: string): string {
  const name = value.trim();
  if (!name) return 'Give your workspace a name.';
  if (name.length > 80) return 'Keep the name under 80 characters.';
  return '';
}

export function checkAddress(value: string): string {
  const address = value.trim();
  if (!address) return 'Choose an address for your workspace.';
  if (address.length > 63) return 'Keep the address under 64 characters.';
  if (!ADDRESS.test(address)) {
    return 'Use lowercase letters, numbers and hyphens; start and end with a letter or number.';
  }
  return '';
}

/**
 * The address is derived from the name until the person edits it. Editing it
 * sets `addressTouched`, after which the name never overwrites the choice — that
 * is how a manually chosen address survives a later rename.
 */
export function deriveAddress(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63)
    .replace(/-+$/g, '');
}

export function checkTeamName(value: string): string {
  const name = value.trim();
  if (!name) return 'Name your first team.';
  if (name.length > 60) return 'Keep the team name under 60 characters.';
  return '';
}

export function checkTeamKey(value: string): string {
  const key = value.trim().toUpperCase();
  if (!key) return 'Choose a short key for the team.';
  if (!TEAM_KEY.test(key)) return 'Use 1–8 letters or numbers; this becomes the issue prefix.';
  return '';
}

export function checkProjectName(value: string): string {
  const name = value.trim();
  if (!name) return 'Give the project a name, or skip this step.';
  if (name.length > 80) return 'Keep the project name under 80 characters.';
  return '';
}

export function checkInvitee(name: string, email: string): string {
  if (!name.trim()) return 'Add the person’s name.';
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) return 'Use a valid email address.';
  return '';
}

export function checkPassword(value: string): string {
  if (value.length < 12) return 'Use at least 12 characters.';
  return '';
}

/** Normalised values, so callers never have to remember which casing the API wants. */
export function normaliseAddress(value: string): string {
  return value.trim().toLowerCase().replace(/^-+|-+$/g, '');
}

export function normaliseTeamKey(value: string): string {
  return value.trim().toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);
}

/**
 * Address editing rule: until the person edits the address themselves, renaming
 * the workspace regenerates it. Once touched, the manual choice is preserved.
 */
export function addressOnNameChange(name: string, address: string, touched: boolean): string {
  return touched ? address : deriveAddress(name);
}

export function checkName(value: string): string {
  if (!value.trim()) return 'Add your name.';
  if (value.trim().length > 120) return 'Keep the name under 120 characters.';
  return '';
}

export function checkEmail(value: string): string {
  const email = value.trim();
  if (!email) return 'Add your work email.';
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return 'Use a valid email address.';
  return '';
}
