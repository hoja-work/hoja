/* Saved views live on the server (docs/views.md): a view is a scope, a filter tree, and the
 * display options its results render with. This module is the only place the browser talks to
 * the views API; pages keep their own presentation state and pass documents through here.
 *
 * The wire helper is local rather than imported from main.tsx because main.tsx imports this
 * module: keeping the fetch here avoids an import cycle. It reads the same token and endpoint.
 */

export type ViewDocument = Record<string, unknown>;

export type ViewUser = {id: string; name?: string | null; avatarSeed?: string | null};

export type ServerView = {
  id: string;
  name: string;
  description?: string | null;
  icon?: string | null;
  color?: string | null;
  slugId?: string;
  kind: 'issue' | 'project' | 'initiative';
  scope: string;
  visibility: string;
  favorite: boolean;
  pinned?: boolean;
  sortOrder?: number | null;
  filters: ViewDocument;
  display: ViewDocument;
  team?: {id: string; key: string; name?: string} | null;
  project?: {id: string; name?: string} | null;
  initiative?: {id: string} | null;
  owner?: ViewUser | null;
  subscription?: {onAdded: boolean; onCompleted: boolean} | null;
  url?: string;
  createdAt?: string;
  updatedAt?: string;
};

export type ViewInput = {
  name: string;
  description?: string | null;
  icon?: string | null;
  color?: string | null;
  kind?: 'issue' | 'project' | 'initiative';
  scope?: 'workspace' | 'team' | 'project' | 'initiative';
  teamId?: string;
  projectId?: string;
  initiativeId?: string;
  visibility?: 'private' | 'workspace' | 'team';
  filters?: ViewDocument;
  display?: ViewDocument;
  ownerId?: string;
};

const FIELDS = 'id name description icon color slugId kind scope visibility favorite pinned sortOrder filters display team{id key name} project{id name} initiative{id} owner{id name avatarSeed} subscription{onAdded onCompleted} url createdAt updatedAt';

async function request<T>(query: string, variables?: Record<string, unknown>): Promise<T> {
  const stored = localStorage.getItem('hoja_token');
  const token = stored && stored !== 'session' ? stored : null;
  const response = await fetch('/graphql', {
    method: 'POST',
    credentials: 'include',
    headers: {'content-type': 'application/json', ...(token ? {authorization: `Bearer ${token}`} : {})},
    body: JSON.stringify({query, variables}),
  });
  const body = await response.json();
  if (!response.ok || body.errors) throw new Error(body.errors?.[0]?.message || 'Request failed');
  return body.data as T;
}

/** Fired after any write that changes which views exist or how they are listed (favorites,
 * names, pins), so the sidebar, palette and lists re-read without a page reload. */
export const VIEWS_CHANGED = 'hoja:views-changed';

function changed<T>(value: T): T {
  try {
    window.dispatchEvent(new Event(VIEWS_CHANGED));
  } catch {
    /* no window (tests) */
  }
  return value;
}

export async function listViews(kind?: 'issue' | 'project' | 'initiative', scope?: string): Promise<ServerView[]> {
  const data = await request<{views: ServerView[]}>(
    `query Views($kind:String,$scope:String){views(kind:$kind,scope:$scope){${FIELDS}}}`,
    {kind: kind ?? null, scope: scope ?? null});
  return data.views;
}

export async function createView(input: ViewInput): Promise<ServerView> {
  const data = await request<{viewCreate: {success: boolean; view: ServerView | null}}>(
    `mutation CreateView($input:ViewCreateInput!){viewCreate(input:$input){success view{${FIELDS}}}}`,
    {input});
  if (!data.viewCreate.success || !data.viewCreate.view) throw new Error('The view was not created.');
  return changed(data.viewCreate.view);
}

export async function updateView(id: string, input: Partial<ViewInput> & {archived?: boolean; kind?: string}): Promise<ServerView> {
  const data = await request<{viewUpdate: {success: boolean; view: ServerView | null}}>(
    `mutation UpdateView($id:String!,$input:ViewUpdateInput!){viewUpdate(id:$id,input:$input){success view{${FIELDS}}}}`,
    {id, input});
  if (!data.viewUpdate.success || !data.viewUpdate.view) throw new Error('The view was not updated.');
  return changed(data.viewUpdate.view);
}

export async function deleteView(id: string): Promise<void> {
  const data = await request<{viewDelete: {success: boolean}}>(
    'mutation DeleteView($id:String!){viewDelete(id:$id){success}}', {id});
  if (!data.viewDelete.success) throw new Error('The view was not deleted.');
  changed(null);
}

export async function getView(ident: string): Promise<ServerView> {
  const data = await request<{view: ServerView}>(
    `query View($id:String!){view(id:$id){${FIELDS}}}`, {id: ident});
  return data.view;
}

/** Plain-language filters: the server's rules tier, then the workspace's model (ai_filter.py). */
export async function suggestFilters(prompt: string, teamId?: string): Promise<{filters: Record<string, unknown> | null; message?: string | null; source: string}> {
  const data = await request<{viewFilterSuggestion: {filters: Record<string, unknown> | null; message: string | null; source: string}}>(
    `query SuggestFilters($p:String!,$t:String){viewFilterSuggestion(prompt:$p,teamId:$t){filters message source}}`,
    {p: prompt, t: teamId || null});
  return data.viewFilterSuggestion;
}

export async function duplicateView(id: string, name?: string, visibility?: string): Promise<ServerView> {
  const data = await request<{viewDuplicate: {success: boolean; view: ServerView | null}}>(
    `mutation DuplicateView($id:String!,$name:String,$visibility:String){viewDuplicate(id:$id,name:$name,visibility:$visibility){success view{${FIELDS}}}}`,
    {id, name: name ?? null, visibility: visibility ?? null});
  if (!data.viewDuplicate.success || !data.viewDuplicate.view) throw new Error('The view was not duplicated.');
  return changed(data.viewDuplicate.view);
}

/** Pin a team or project view as a tab on that page, or unpin it. */
export async function pinView(id: string, pinned: boolean, sortOrder?: number): Promise<ServerView> {
  const data = await request<{viewPin: {success: boolean; view: ServerView | null}}>(
    `mutation PinView($id:String!,$pinned:Boolean!,$sortOrder:Float){viewPin(id:$id,pinned:$pinned,sortOrder:$sortOrder){success view{${FIELDS}}}}`,
    {id, pinned, sortOrder: sortOrder ?? null});
  if (!data.viewPin.view) throw new Error('The view was not pinned.');
  return changed(data.viewPin.view);
}

/** Follow a view: `onAdded` a task starts matching, `onCompleted` a matching task closes.
 * Both off unfollows it. */
export async function setViewSubscription(id: string, onAdded: boolean, onCompleted: boolean): Promise<ServerView['subscription']> {
  if (!onAdded && !onCompleted) {
    await request('mutation Unsubscribe($id:String!){viewUnsubscribe(viewId:$id){success}}', {id});
    return null;
  }
  const data = await request<{viewSubscribe: {subscription: ServerView['subscription']}}>(
    'mutation Subscribe($id:String!,$a:Boolean,$c:Boolean){viewSubscribe(viewId:$id,onAdded:$a,onCompleted:$c){subscription{onAdded onCompleted}}}',
    {id, a: onAdded, c: onCompleted});
  return data.viewSubscribe.subscription;
}

/** Download the view's tasks as CSV (GET /api/views/<id>/export.csv), with the same credentials
 * as every other call, then hand the file to the browser. */
export async function exportViewCsv(id: string, fallbackName = 'view'): Promise<void> {
  const stored = localStorage.getItem('hoja_token');
  const token = stored && stored !== 'session' ? stored : null;
  const response = await fetch(`/api/views/${encodeURIComponent(id)}/export.csv`, {
    credentials: 'include',
    headers: token ? {authorization: `Bearer ${token}`} : {},
  });
  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(text.slice(0, 200) || `Export failed (${response.status}).`);
  }
  const disposition = response.headers.get('content-disposition') || '';
  const name = /filename="?([^";]+)"?/.exec(disposition)?.[1] || `${fallbackName}.csv`;
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export type ViewPage<T> = {
  nodes: T[];
  pageInfo: {hasNextPage: boolean; endCursor: string | null};
  totalCount?: number | null;
};

/** One page of a view's rows, run on the server: a saved view (`viewId`, optionally with unsaved
 * `filters`/`display` overrides) or an unsaved draft (`preview`). `fields` is the row selection
 * the caller renders, so this module does not have to know the task or project shape. */
export async function runView<T>(args: {
  kind: 'issue' | 'project';
  fields: string;
  first?: number;
  after?: string | null;
  viewId?: string;
  filter?: ViewDocument | null;
  display?: ViewDocument | null;
  preview?: {filters: ViewDocument; display?: ViewDocument; scope?: string; teamId?: string; projectId?: string; initiativeId?: string};
}): Promise<ViewPage<T>> {
  const connection = args.kind === 'issue' ? 'issues' : 'projects';
  const page = `${connection}(first:$first,after:$after,display:$display){nodes ${args.fields} pageInfo{hasNextPage endCursor} totalCount}`;
  if (args.preview) {
    const data = await request<{viewPreview: Record<string, ViewPage<T>>}>(
      `query RunPreview($kind:String!,$filters:JSON,$display:JSON,$scope:String,$teamId:String,$projectId:String,$initiativeId:String,$first:Int,$after:String){viewPreview(kind:$kind,filters:$filters,display:$display,scope:$scope,teamId:$teamId,projectId:$projectId,initiativeId:$initiativeId){${page}}}`,
      {kind: args.kind, filters: args.preview.filters, display: args.preview.display ?? args.display ?? null,
        scope: args.preview.scope ?? null, teamId: args.preview.teamId ?? null, projectId: args.preview.projectId ?? null,
        initiativeId: args.preview.initiativeId ?? null,
        first: args.first ?? 50, after: args.after ?? null});
    return data.viewPreview[connection];
  }
  /* `View.issues(filter:)` ANDs onto the saved tree, so a caller with edited filters (which may
     drop saved chips) runs them as a preview in the view's own scope instead. */
  const data = await request<{view: Record<string, ViewPage<T>>}>(
    `query RunView($id:String!,$first:Int,$after:String,$display:JSON){view(id:$id){${page}}}`,
    {id: args.viewId, first: args.first ?? 50, after: args.after ?? null, display: args.display ?? null});
  return data.view[connection];
}

export async function toggleViewFavorite(id: string): Promise<boolean> {
  const data = await request<{viewFavoriteToggle: {success: boolean; favorite: boolean}}>(
    'mutation FavoriteView($id:String!){viewFavoriteToggle(id:$id){success favorite}}', {id});
  return changed(data.viewFavoriteToggle.favorite);
}

/* ------------------------------ legacy filter translation ------------------------------
 * A browser-local view (before views lived on the server) stored only a text box and a status
 * picker; migrateLegacyViews maps those two fields onto the canonical filter tree.
 */

export type ViewFilterExtras = {
  priority?: string;
  assignee?: string;
  project?: string;
};

export function filtersFromFields(
  kind: 'issue' | 'project',
  query: string,
  state: string,
  extra?: ViewFilterExtras,
): ViewDocument {
  const predicates: ViewDocument[] = [];
  const text = query.trim();
  if (text) {
    predicates.push(kind === 'issue'
      ? {title: {contains: text}}
      : {name: {contains: text}});
  }
  if (state) predicates.push({state: {is: state}});
  if (kind === 'issue' && extra) {
    if (extra.priority !== undefined && extra.priority !== '') {
      const parsed = Number(extra.priority);
      if (Number.isInteger(parsed) && parsed >= 0 && parsed <= 4) {
        predicates.push({priority: {is: parsed}});
      }
    }
    if (extra.assignee) predicates.push({assignee: {is: extra.assignee}});
    if (extra.project) predicates.push({project: {is: extra.project}});
  }
  if (!predicates.length) return {};
  return predicates.length === 1 ? predicates[0] : {and: predicates};
}

/* --------------------------- one-time local migration --------------------------- */

const MIGRATED_KEY = (orgId: string) => `hoja_views_migrated_${orgId}`;
const WORKSPACE_KEY = (orgId: string) => `hoja_saved_views_${orgId}`;
const PROJECT_PREFIX = 'hoja_project_views_';

function readRows(key: string): Record<string, unknown>[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

/* Moves the rows a previous version kept in localStorage into server rows, once.
 * A row the server rejects is skipped (it never blocks the app); the marker is written
 * whether or not every row landed, so a permanently invalid row is not retried forever.
 * Server rows are never deleted here, so re-running can only add. */
export async function migrateLegacyViews(orgId: string): Promise<number> {
  if (!orgId || localStorage.getItem(MIGRATED_KEY(orgId))) return 0;
  let migrated = 0;
  /* Names already on the server let a lost marker re-run without duplicating rows. */
  let existing: Set<string>;
  try {
    existing = new Set((await listViews()).map(view => view.name.toLowerCase()));
  } catch {
    return 0;
  }
  const keys = [WORKSPACE_KEY(orgId),
    ...Object.keys(localStorage).filter(key => key.startsWith(PROJECT_PREFIX))];
  for (const key of keys) {
    const projectScoped = key.startsWith(PROJECT_PREFIX);
    for (const row of readRows(key)) {
      const name = typeof row.name === 'string' ? row.name.trim().slice(0, 80) : '';
      if (!name || existing.has(name.toLowerCase())) continue;
      const kind = row.kind === 'projects' ? 'project' : 'issue';
      const projectId = projectScoped
        ? (typeof row.projectId === 'string' ? row.projectId : key.slice(PROJECT_PREFIX.length))
        : undefined;
      try {
        const created = await createView({
          name,
          kind,
          scope: projectId ? 'project' : 'workspace',
          ...(projectId ? {projectId} : {}),
          visibility: 'private',
          filters: filtersFromFields(kind, typeof row.query === 'string' ? row.query : '',
            typeof row.state === 'string' ? row.state : ''),
          display: {},
        });
        migrated += 1;
        existing.add(name.toLowerCase());
        if (row.favorite) await toggleViewFavorite(created.id);
      } catch {
        /* skip a row the server refuses; the marker below keeps it from blocking loads */
      }
    }
    localStorage.removeItem(key);
  }
  localStorage.setItem(MIGRATED_KEY(orgId), new Date().toISOString());
  return migrated;
}

/* ------------------------------ display preferences ------------------------------
 * A page's display options are layered on the server (docs/views.md, "Display preferences"):
 * the default everyone starts from, the caller's own layer, and the effective result. Built-in
 * pages address theirs by `viewType` ("team:<id>:active", "my-issues:assigned"), saved views
 * by `viewId`.
 */

export type ViewPreferenceState = {
  default: ViewDocument;
  user: ViewDocument | null;
  effective: ViewDocument;
  hasUserOverrides: boolean;
};

export type PreferenceTarget = {viewId?: string; viewType?: string};

const PREFERENCE_FIELDS = 'default user effective hasUserOverrides';

export async function getViewPreferences(target: PreferenceTarget): Promise<ViewPreferenceState> {
  const data = await request<{viewPreferences: ViewPreferenceState}>(
    `query ViewPreferences($viewId:String,$viewType:String){viewPreferences(viewId:$viewId,viewType:$viewType){${PREFERENCE_FIELDS}}}`,
    {viewId: target.viewId ?? null, viewType: target.viewType ?? null});
  return data.viewPreferences;
}

/** Lay `values` over the caller's layer (`scope: "user"`) or the default for everyone
 * (`scope: "default"`); a null value clears that key. */
export async function setViewPreferences(target: PreferenceTarget, values: ViewDocument,
  scope: 'user' | 'default' = 'user'): Promise<ViewPreferenceState> {
  const data = await request<{viewPreferencesSet: ViewPreferenceState}>(
    `mutation SetViewPreferences($viewId:String,$viewType:String,$values:JSON!,$scope:String){viewPreferencesSet(viewId:$viewId,viewType:$viewType,values:$values,scope:$scope){${PREFERENCE_FIELDS}}}`,
    {viewId: target.viewId ?? null, viewType: target.viewType ?? null, values, scope});
  return data.viewPreferencesSet;
}

export async function resetViewPreferences(target: PreferenceTarget): Promise<ViewPreferenceState> {
  const data = await request<{viewPreferencesReset: ViewPreferenceState}>(
    `mutation ResetViewPreferences($viewId:String,$viewType:String){viewPreferencesReset(viewId:$viewId,viewType:$viewType){${PREFERENCE_FIELDS}}}`,
    {viewId: target.viewId ?? null, viewType: target.viewType ?? null});
  return data.viewPreferencesReset;
}

/* ------------------------------ "Save as view" drafts ------------------------------
 * A list hands its current filters and display to the new-view editor through session
 * storage, so the editor opens prefilled (Alt V) without the URL carrying the display.
 */

export type ViewDraft = {
  kind: 'issue' | 'project';
  filters: ViewDocument;
  display: ViewDocument;
  teamId?: string;
  projectId?: string;
  name?: string;
  /** The "Save to" the editor starts on (a section's "+" on the views list). */
  saveTo?: 'personal' | 'workspace';
  /** Duplicate (Linear's flow, linear-capture/s7-*): the source's identity, and the tab to pin
   * the copy right after when the source is a pinned tab. */
  description?: string | null;
  icon?: string | null;
  color?: string | null;
  pinAfter?: {sortOrder: number; nextSortOrder: number | null};
};

const DRAFT_KEY = 'hoja_view_draft';

export function stashViewDraft(draft: ViewDraft): void {
  try {
    sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    /* private mode: the editor opens empty instead */
  }
}

export function takeViewDraft(): ViewDraft | null {
  try {
    const raw = sessionStorage.getItem(DRAFT_KEY);
    sessionStorage.removeItem(DRAFT_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === 'object' && parsed.filters && typeof parsed.filters === 'object' ? parsed : null;
  } catch {
    return null;
  }
}
