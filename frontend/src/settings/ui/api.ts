/* Settings transport. Same wire as main.tsx's gql, but a non-JSON body (a 502 HTML page,
 * an empty 204) becomes a readable error instead of a JSON.parse exception (HOJA-390). */
function token(): string | null {
  try {
    const t = localStorage.getItem("hoja_token");
    return t && t !== "session" ? t : null;
  } catch {
    return null;
  }
}

async function body(r: Response): Promise<unknown> {
  const text = await r.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    throw new Error(
      r.ok
        ? "The server returned an unexpected response."
        : `The server is unavailable (HTTP ${r.status}). Try again in a moment.`,
    );
  }
}

export async function gql<T = Record<string, any>>(
  query: string,
  variables?: Record<string, unknown>,
): Promise<T> {
  const t = token();
  const r = await fetch("/graphql", {
    method: "POST",
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...(t ? { authorization: `Bearer ${t}` } : {}),
    },
    body: JSON.stringify({ query, variables }),
  });
  const b = (await body(r)) as {
    data?: T;
    errors?: { message?: string }[];
  } | null;
  if (!r.ok || b?.errors?.length)
    throw new Error(
      b?.errors?.[0]?.message || `Request failed (HTTP ${r.status})`,
    );
  return (b?.data ?? {}) as T;
}

export async function rest<T = unknown>(
  path: string,
  init: { method?: string; json?: unknown } = {},
): Promise<T> {
  const t = token();
  const r = await fetch(path, {
    method: init.method || (init.json === undefined ? "GET" : "POST"),
    credentials: "include",
    headers: {
      ...(init.json !== undefined
        ? { "content-type": "application/json" }
        : {}),
      ...(t ? { authorization: `Bearer ${t}` } : {}),
    },
    body: init.json === undefined ? undefined : JSON.stringify(init.json),
  });
  const b = (await body(r)) as {
    detail?: string;
    error?: string;
    message?: string;
    errors?: { message?: string }[];
  } | null;
  if (!r.ok)
    throw new Error(
      b?.detail ||
        b?.error ||
        b?.message ||
        b?.errors?.[0]?.message ||
        `Request failed (HTTP ${r.status})`,
    );
  return b as T;
}
