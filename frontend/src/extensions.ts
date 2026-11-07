/* Extensions: GraphQL client, shared types, and the session-cached action list the
 * issue view uses for its menu entries. Every name here follows the frozen contract
 * in .pi/plans/hoja-extensions.md ("GraphQL contract (frozen for the frontend worker)").
 * The server reports install/update problems as BAD_USER_INPUT with `extensions.field`
 * (the manifest path, e.g. hooks[0].path); ExtensionError keeps that field so the
 * settings page can name the offending key instead of dumping a bare message. */
import {useEffect, useState} from 'react';

export type ExtensionHook = {key: string; on: string; failurePolicy: string; position: number};
export type ExtensionAction = {id: string; key: string; title: string; entity: string; extensionId: string; extensionName: string};
export type ExtensionFailure = {at: string; kind: string; key?: string | null; httpStatus?: number | null; message?: string | null};
export type Extension = {
  id: string; key: string; name: string; version: string; url: string; access: string; enabled: boolean;
  manifest: unknown; botUser?: {id: string; name?: string} | null; createdAt: string; updatedAt: string;
  hooks: ExtensionHook[]; actions: ExtensionAction[]; failures: ExtensionFailure[];
};
export type ExtensionInstallPayload = {success: boolean; extension?: Extension | null; apiKey?: string | null; signingSecret?: string | null};
export type ExtensionPayload = {success: boolean; extension?: Extension | null};
export type ExtensionRunPayload = {success: boolean; runId?: string | null; status?: string | null; message?: string | null};

export const extensionActionFields = 'id key title entity extensionId extensionName';
export const extensionFields =
  `id key name version url access enabled manifest botUser{id name} createdAt updatedAt ` +
  `hooks{key on failurePolicy position} actions{${extensionActionFields}} failures{at kind key httpStatus message}`;

export class ExtensionError extends Error {
  readonly code?: string;
  readonly field?: string;
  constructor(message: string, code?: string, field?: string) {
    super(message);
    this.name = 'ExtensionError';
    this.code = code;
    this.field = field;
  }
}

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('hoja_token');
  return token && token !== 'session' ? {authorization: `Bearer ${token}`} : {};
}

type GraphQLBody<T> = {data?: T; errors?: {message?: string; extensions?: {code?: string; field?: string}}[]};

export async function extensionsGql<T>(query: string, variables: Record<string, unknown> = {}): Promise<T> {
  const res = await fetch('/graphql', {
    method: 'POST',
    credentials: 'include',
    headers: {'content-type': 'application/json', ...authHeaders()},
    body: JSON.stringify({query, variables}),
  });
  const body = (await res.json().catch(() => null)) as GraphQLBody<T> | null;
  const first = body?.errors?.[0];
  if (first) {
    const field = typeof first.extensions?.field === 'string' ? first.extensions.field : undefined;
    const text = first.message || 'Request failed';
    const labelled = field && !text.startsWith(field) ? `${field}: ${text}` : text;
    throw new ExtensionError(labelled, first.extensions?.code, field);
  }
  if (!res.ok || !body?.data) throw new ExtensionError(`Request failed (${res.status})`);
  return body.data;
}

export function errorText(value: unknown): string {
  return value instanceof Error ? value.message : String(value);
}

export async function fetchExtensions(): Promise<Extension[]> {
  const data = await extensionsGql<{extensions: Extension[]}>(`query Extensions{extensions{${extensionFields}}}`);
  return data.extensions || [];
}

/** The parse is done by the caller so the textarea can show the JSON error before any request. */
export function installExtension(manifest: unknown) {
  return extensionsGql<{extensionInstall: ExtensionInstallPayload}>(
    `mutation($manifest:JSON!){extensionInstall(manifest:$manifest){success extension{${extensionFields}} apiKey signingSecret}}`,
    {manifest},
  ).then(d => d.extensionInstall);
}

export function updateExtension(id: string, manifest: unknown) {
  return extensionsGql<{extensionUpdate: ExtensionPayload}>(
    `mutation($id:String!,$manifest:JSON!){extensionUpdate(id:$id,manifest:$manifest){success extension{${extensionFields}}}}`,
    {id, manifest},
  ).then(d => d.extensionUpdate);
}

export function uninstallExtension(id: string) {
  return extensionsGql<{extensionUninstall: {success: boolean}}>(
    'mutation($id:String!){extensionUninstall(id:$id){success}}',
    {id},
  ).then(d => d.extensionUninstall);
}

export function runExtensionAction(actionId: string, entityId: string) {
  return extensionsGql<{extensionActionRun: ExtensionRunPayload}>(
    'mutation($actionId:String!,$entityId:String!){extensionActionRun(actionId:$actionId,entityId:$entityId){success runId status message}}',
    {actionId, entityId},
  ).then(d => d.extensionActionRun);
}

/* Action entries are workspace-wide (entity only), so one query per session is enough:
 * every issue detail reuses the cached list instead of asking again. */
let actionsCache: ExtensionAction[] | null = null;
let actionsInflight: Promise<ExtensionAction[]> | null = null;

export function clearExtensionActionsCache() {
  actionsCache = null;
  actionsInflight = null;
}

export function loadIssueExtensionActions(): Promise<ExtensionAction[]> {
  if (actionsCache) return Promise.resolve(actionsCache);
  if (actionsInflight) return actionsInflight;
  actionsInflight = extensionsGql<{extensionActions: ExtensionAction[]}>(
    `query IssueExtensionActions{extensionActions(entity:"Issue"){${extensionActionFields}}}`,
  ).then(data => {
    const items = data.extensionActions || [];
    actionsCache = items;
    actionsInflight = null;
    return items;
  }).catch((error: unknown) => {
    actionsInflight = null;
    throw error;
  });
  return actionsInflight;
}

export type IssueExtensionActions = {actions: ExtensionAction[]; error: string; loading: boolean};

/** Session-cached action list for one issue. Errors are reported, never thrown into render. */
export function useIssueExtensionActions(): IssueExtensionActions {
  const [state, setState] = useState<IssueExtensionActions>(() =>
    actionsCache ? {actions: actionsCache, error: '', loading: false} : {actions: [], error: '', loading: true},
  );
  useEffect(() => {
    if (actionsCache) return;
    let live = true;
    loadIssueExtensionActions()
      .then(actions => { if (live) setState({actions, error: '', loading: false}); })
      .catch(e => { if (live) setState({actions: [], error: errorText(e), loading: false}); });
    return () => { live = false; };
  }, []);
  return state;
}
