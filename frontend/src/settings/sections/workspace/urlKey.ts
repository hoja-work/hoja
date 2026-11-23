/* The workspace URL key rule, mirrored from the server (writer.normalize_workspace_url_key):
 * 1–48 lowercase ASCII letters or digits in hyphen-separated groups. workspaceCreate and
 * workspaceUpdate share that rule, so a key this accepts is one the server accepts. */
export const URL_KEY_MAX = 48;
const URL_KEY = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export function normalizeUrlKey(raw: string): string {
  return raw.trim().toLowerCase();
}

export function urlKeyProblem(raw: string): string | null {
  const key = normalizeUrlKey(raw);
  if (!key)
    return "Enter a URL key. Links to tasks and projects start with it.";
  if (key.length > URL_KEY_MAX)
    return `Use ${URL_KEY_MAX} characters or fewer.`;
  if (/[^a-z0-9-]/.test(key)) return "Use only letters, numbers and hyphens.";
  if (!URL_KEY.test(key))
    return "Don't start or end with a hyphen, or use two hyphens in a row.";
  return null;
}

export function nameProblem(raw: string): string | null {
  return raw.trim() ? null : "Enter a name for the workspace.";
}
