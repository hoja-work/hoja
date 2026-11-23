/* Manifest parsing and validation that runs before any request, so a typo is explained
 * where it is (line and column) instead of after a round trip. Mirrors the server's
 * parse_manifest (hoja_server/extensions.py) for the common fields; the server stays the
 * authority for everything else (URL resolution, hooks, events). */

export type ManifestBasics = {
  key: string;
  name: string;
  version: string;
  url: string;
  access: "read" | "read_write";
  description: string;
};

export const BASIC_KEYS = [
  "key",
  "name",
  "version",
  "url",
  "access",
  "description",
] as const;
const KNOWN = new Set<string>([...BASIC_KEYS, "events", "hooks", "actions"]);
export const KEY_PATTERN = /^[a-z0-9][a-z0-9-]{0,62}$/;

export const EMPTY_BASICS: ManifestBasics = {
  key: "",
  name: "",
  version: "1.0.0",
  url: "https://",
  access: "read",
  description: "",
};

export const EXAMPLE_MANIFEST = {
  key: "worktrees",
  name: "Worktree provisioner",
  version: "1.0.0",
  url: "https://ext.example.com",
  access: "read_write",
  events: [{ key: "issues", resourceTypes: ["Issue"], allPublicTeams: true }],
  hooks: [
    {
      key: "guard",
      on: "issue.beforeCreate",
      path: "/hooks/guard",
      failurePolicy: "allow",
    },
  ],
  actions: [
    {
      key: "provision",
      title: "Provision environment",
      entity: "Issue",
      path: "/actions/provision",
    },
  ],
};

function lineCol(text: string, pos: number) {
  const before = text.slice(0, pos);
  const line = before.split("\n").length;
  const col = pos - before.lastIndexOf("\n");
  return { line, col };
}

/** JSON.parse with a human error: "Line 3, column 12: Expected ',' or '}' after property value." */
export function parseJson(text: string): {
  value?: unknown;
  error?: string;
} {
  if (!text.trim()) return { error: "Paste a manifest to continue." };
  try {
    return { value: JSON.parse(text) };
  } catch (e) {
    const raw = e instanceof Error ? e.message : String(e);
    let where: { line: number; col: number } | null = null;
    const lc = /line (\d+) column (\d+)/i.exec(raw);
    const at = /position (\d+)/i.exec(raw);
    if (lc) where = { line: Number(lc[1]), col: Number(lc[2]) };
    else if (at) where = lineCol(text, Number(at[1]));
    let reason = raw
      .replace(/^JSON\.parse:\s*/i, "")
      .replace(/\s*\(line \d+ column \d+\)/i, "")
      .replace(/\s*(in JSON )?at position \d+.*$/i, "")
      .replace(/\s*at line \d+ column \d+.*$/i, "")
      .trim();
    if (/unexpected end/i.test(reason))
      reason = "The JSON ends too early. Check for a missing } or ]";
    if (!where && /unexpected end/i.test(raw))
      where = lineCol(text, text.length);
    reason = reason.charAt(0).toUpperCase() + reason.slice(1);
    return {
      error: where
        ? `Line ${where.line}, column ${where.col}: ${reason.replace(/\.$/, "")}.`
        : `${reason.replace(/\.$/, "")}.`,
    };
  }
}

export function checkBasics(
  b: Partial<ManifestBasics>,
): Partial<Record<keyof ManifestBasics, string>> {
  const e: Partial<Record<keyof ManifestBasics, string>> = {};
  if (!b.key || !KEY_PATTERN.test(b.key))
    e.key =
      "Use lowercase letters, numbers and dashes, starting with a letter or number.";
  if (!b.name?.trim()) e.name = "Enter a name.";
  else if (b.name.trim().length > 80)
    e.name = "Keep the name to 80 characters.";
  if (!b.version?.trim()) e.version = "Enter a version, for example 1.0.0.";
  else if (b.version.trim().length > 40)
    e.version = "Keep the version to 40 characters.";
  const url = (b.url ?? "").trim();
  let parsed: URL | null = null;
  try {
    parsed = new URL(url);
  } catch {
    parsed = null;
  }
  if (!parsed || parsed.protocol !== "https:" || !parsed.hostname)
    e.url =
      "Enter the extension's https URL, for example https://ext.example.com.";
  else if (parsed.search || parsed.hash)
    e.url = "Remove the query string or #fragment from the URL.";
  else if (parsed.port && parsed.port !== "443")
    e.url = "Extensions are called on port 443. Remove the port.";
  if (b.access && b.access !== "read" && b.access !== "read_write")
    e.access = 'access must be "read" or "read_write".';
  return e;
}

/** Validate a whole manifest object; returns the first problem, phrased for a person. */
export function checkManifest(value: unknown): string | null {
  if (!value || typeof value !== "object" || Array.isArray(value))
    return "The manifest must be a JSON object, starting with { and ending with }.";
  const m = value as Record<string, unknown>;
  const unknown = Object.keys(m).filter((k) => !KNOWN.has(k));
  if (unknown.length)
    return `Unknown field${unknown.length > 1 ? "s" : ""}: ${unknown.join(", ")}. Allowed: ${[...KNOWN].join(", ")}.`;
  for (const k of ["key", "name", "version", "url"] as const)
    if (m[k] !== undefined && typeof m[k] !== "string")
      return `"${k}" must be a string.`;
  const problems = checkBasics({
    key: m.key as string,
    name: m.name as string,
    version: m.version as string,
    url: m.url as string,
    access: (m.access as ManifestBasics["access"]) ?? "read",
  });
  const first = Object.entries(problems)[0];
  if (first) return `"${first[0]}": ${first[1]}`;
  for (const list of ["events", "hooks", "actions"] as const)
    if (m[list] !== undefined && !Array.isArray(m[list]))
      return `"${list}" must be a list ([ … ]).`;
  return null;
}

export function splitManifest(m: Record<string, unknown>): {
  basics: ManifestBasics;
  rest: Record<string, unknown>;
} {
  const rest: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(m))
    if (!(BASIC_KEYS as readonly string[]).includes(k)) rest[k] = v;
  return {
    basics: {
      key: typeof m.key === "string" ? m.key : "",
      name: typeof m.name === "string" ? m.name : "",
      version: typeof m.version === "string" ? m.version : "",
      url: typeof m.url === "string" ? m.url : "",
      access: m.access === "read_write" ? "read_write" : "read",
      description: typeof m.description === "string" ? m.description : "",
    },
    rest,
  };
}

export function joinManifest(
  basics: ManifestBasics,
  rest: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {
    key: basics.key.trim(),
    name: basics.name.trim(),
    version: basics.version.trim(),
    url: basics.url.trim(),
    access: basics.access,
  };
  if (basics.description.trim()) out.description = basics.description.trim();
  return { ...out, ...rest };
}
