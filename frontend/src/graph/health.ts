/* Health of a project or an initiative, as its latest update states it. The values are
 * the stored strings; a missing or unknown value reads as "no update". */
export const HEALTH = ["on track", "at risk", "off track"] as const;
export type Health = (typeof HEALTH)[number];

export const isHealth = (value?: string | null): value is Health =>
  HEALTH.includes(value as Health);

export const healthLabel = (health?: string | null) =>
  isHealth(health)
    ? health.charAt(0).toUpperCase() + health.slice(1)
    : "No updates";

export const healthColors = (health?: string | null) =>
  health === "on track"
    ? "bg-green-100 text-green-800"
    : health === "at risk"
      ? "bg-amber-100 text-amber-800"
      : health === "off track"
        ? "bg-red-100 text-red-800"
        : "bg-subtle text-muted";

export const healthDot = (health?: string | null) =>
  health === "on track"
    ? "bg-green-600"
    : health === "at risk"
      ? "bg-amber-500"
      : health === "off track"
        ? "bg-red-600"
        : "bg-line";
