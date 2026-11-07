// Thin HTTP/GraphQL helpers for the setup wizard.
//
// The wizard itself never calls `fetch`: it talks to a `SetupAdapter`. These
// helpers exist so both editions can share the same error handling, and so a
// proxy that answers with HTML (a 502 page, for example) produces a sentence a
// person can act on instead of "Unexpected token <".

export class SetupApiError extends Error {
  readonly status: number;
  readonly reason?: string;

  constructor(message: string, status = 0, reason?: string) {
    super(message);
    this.name = 'SetupApiError';
    this.status = status;
    this.reason = reason;
  }
}

async function body(response: Response): Promise<Record<string, unknown>> {
  const text = await response.text();
  if (!text) return {};
  try {
    const parsed: unknown = JSON.parse(text);
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {};
  } catch {
    throw new SetupApiError(
      response.ok
        ? 'Hoja answered with something this page could not read. Reload and try again.'
        : `Hoja could not complete that request (HTTP ${response.status}).`,
      response.status,
    );
  }
}

function apiToken(): string | null {
  try {
    const token = localStorage.getItem('hoja_token');
    return token && token !== 'session' ? token : null;
  } catch {
    return null;
  }
}

export async function postJson<T>(path: string, payload: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: 'POST',
      credentials: 'include',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify(payload),
    });
  } catch {
    throw new SetupApiError('Could not reach Hoja. Check your connection and try again.');
  }
  const parsed = await body(response);
  if (!response.ok) {
    throw new SetupApiError(
      typeof parsed.error === 'string' ? parsed.error : `Request failed (${response.status}).`,
      response.status,
      typeof parsed.reason === 'string' ? parsed.reason : undefined,
    );
  }
  return parsed as T;
}

export async function getJson<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {credentials: 'include'});
  } catch {
    throw new SetupApiError('Could not reach Hoja. Check your connection and try again.');
  }
  const parsed = await body(response);
  if (!response.ok) {
    throw new SetupApiError(
      typeof parsed.error === 'string' ? parsed.error : `Request failed (${response.status}).`,
      response.status,
    );
  }
  return parsed as T;
}

export async function graphql<T>(query: string, variables?: Record<string, unknown>): Promise<T> {
  const token = apiToken();
  let response: Response;
  try {
    response = await fetch('/graphql', {
      method: 'POST',
      credentials: 'include',
      headers: {
        'content-type': 'application/json',
        ...(token ? {authorization: `Bearer ${token}`} : {}),
      },
      body: JSON.stringify({query, variables}),
    });
  } catch {
    throw new SetupApiError('Could not reach Hoja. Check your connection and try again.');
  }
  const parsed = await body(response);
  const errors = parsed.errors;
  if (!response.ok || Array.isArray(errors)) {
    const first = Array.isArray(errors) ? (errors[0] as {message?: unknown} | undefined) : undefined;
    throw new SetupApiError(
      typeof first?.message === 'string' ? first.message : `Request failed (${response.status}).`,
      response.status,
    );
  }
  return parsed.data as T;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'Something went wrong. Try again.';
}
