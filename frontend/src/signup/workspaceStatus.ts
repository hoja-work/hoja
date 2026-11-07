// Reads the signed-in requester's own workspace state after POST /api/signup.
//
// The sign-up request is asynchronous once accepted: a host-side worker creates
// the database, container and route. This module turns that small polling
// contract into values the page can render, and never treats a transient poll
// failure as a terminal state.

export const STATUS_ENDPOINT = '/api/signup/status';

export type WorkspaceState = 'eligible' | 'provisioning' | 'active' | 'failed';

export type WorkspaceStatus =
  | {
      kind: 'state';
      slug: string;
      status: WorkspaceState;
      workspaceUrl: string | null;
      signInUrl: string | null;
      message: string;
    }
  | {kind: 'unauthenticated'}
  | {kind: 'unavailable'; message: string};

/** The subset of `fetch` this module needs, so tests can supply their own. */
export type FetchLike = (input: string, init: RequestInit) => Promise<Response>;

function parseState(value: unknown): WorkspaceState {
  return value === 'provisioning' || value === 'active' || value === 'failed'
    ? value
    : 'eligible';
}

/**
 * Fetch the state for one workspace request. The sign-up cookie identifies the
 * owner; the slug is only a selector and cannot expose someone else's request.
 */
export async function fetchWorkspaceStatus({
  slug,
  fetcher,
}: {
  slug: string;
  fetcher?: FetchLike;
}): Promise<WorkspaceStatus> {
  const send = fetcher ?? ((input: string, init: RequestInit) => fetch(input, init));
  const query = new URLSearchParams({slug});
  let response: Response;
  try {
    response = await send(`${STATUS_ENDPOINT}?${query.toString()}`, {
      method: 'GET',
      credentials: 'include',
      headers: {accept: 'application/json'},
    });
  } catch {
    return {kind: 'unavailable', message: 'Could not check the workspace yet.'};
  }
  if (response.status === 401) return {kind: 'unauthenticated'};
  let body: Record<string, unknown> | null = null;
  try {
    const parsed: unknown = await response.json();
    body = parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : null;
  } catch {
    body = null;
  }
  if (response.status < 200 || response.status >= 300 || !body || body.ok !== true) {
    return {
      kind: 'unavailable',
      message: `Could not check the workspace (HTTP ${response.status}).`,
    };
  }
  return {
    kind: 'state',
    slug: typeof body.slug === 'string' && body.slug ? body.slug : slug,
    status: parseState(body.status),
    workspaceUrl:
      typeof body.workspaceUrl === 'string' && body.workspaceUrl.startsWith('https://')
        ? body.workspaceUrl
        : null,
    signInUrl:
      typeof body.signInUrl === 'string' && body.signInUrl.startsWith('https://')
        ? body.signInUrl
        : null,
    message:
      typeof body.message === 'string' && body.message.trim()
        ? body.message.trim()
        : 'Your workspace request is queued for creation.',
  };
}
