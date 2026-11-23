export type ApiKey = {
  id: string;
  label: string | null;
  access: string | null;
  hint: string | null;
  expiresAt: string | null;
  lastUsedAt: string | null;
  createdAt: string | null;
  revokedAt: string | null;
};

export type App = {
  id: string;
  clientId: string;
  clientName?: string | null;
  firstParty: boolean;
  scopes: string[];
  deviceLabel?: string | null;
  createdAt: string;
  lastUsedAt?: string | null;
  lastUsedIp?: string | null;
  user?: { id: string; name?: string | null; email?: string | null } | null;
};

export type Session = {
  id: string;
  createdAt: string;
  lastSeenAt?: string | null;
  expiresAt: string;
  current: boolean;
  /** Set for app sessions, e.g. "Pixel 8 · Hoja app · via QR". */
  label?: string | null;
  kind?: "browser" | "app" | null;
};

export const API_KEY_FIELDS =
  "id label access hint expiresAt lastUsedAt createdAt revokedAt";
export const APP_FIELDS =
  "id clientId clientName firstParty scopes deviceLabel createdAt lastUsedAt lastUsedIp user { id name email }";

export function formatWhen(value?: string | null, never = "Never") {
  if (!value) return never;
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? never
    : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function formatDay(value: string) {
  return new Date(value).toLocaleDateString(undefined, { dateStyle: "medium" });
}

export function appName(app: App) {
  return app.clientName || app.clientId;
}

export function appAccess(scopes: string[]) {
  return scopes.includes("hoja:write") ? "Read and write" : "Read only";
}

export type KeyStatus = "active" | "expired" | "revoked";
export function keyStatus(k: ApiKey, now = Date.now()): KeyStatus {
  if (k.revokedAt) return "revoked";
  if (k.expiresAt && Date.parse(k.expiresAt) <= now) return "expired";
  return "active";
}
