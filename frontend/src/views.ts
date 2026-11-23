/* Saved views live on the server (docs/views.md): a view is a scope, a filter tree, and the
 * display options its results render with. This module is the only place the browser talks to
 * the views API; pages keep their own presentation state and pass documents through here.
 *
 * The wire helper is local rather than imported from main.tsx because main.tsx imports this
 * module: keeping the fetch here avoids an import cycle. It reads the same token and endpoint.
 */

export type ViewDocument = Record<string, unknown>;

export type ServerView = {
  id: string;
  name: string;
  description?: string | null;
  kind: 'issue' | 'project' | 'initiative';
  scope: string;
  visibility: string;
  favorite: boolean;
  filters: ViewDocument;
  display: ViewDocument;
  team?: {id: string; key: string} | null;
  project?: {id: string} | null;
  createdAt?: string;
  updatedAt?: string;
};

export type ViewInput = {
  name: string;
  description?: string;
  kind?: 'issue' | 'project' | 'initiative';
  scope?: 'workspace' | 'team' | 'project' | 'initiative';
  teamId?: string;
  projectId?: string;
  initiativeId?: string;
  visibility?: 'private' | 'workspace' | 'team';
  filters?: ViewDocument;
  display?: ViewDocument;
};

const FIELDS = 'id name description kind scope visibility favorite filters display team{id key} project{id} createdAt updatedAt';

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
  return data.viewCreate.view;
}

export async function updateView(id: string, input: Partial<ViewInput> & {archived?: boolean; kind?: string}): Promise<ServerView> {
  const data = await request<{viewUpdate: {success: boolean; view: ServerView | null}}>(
    `mutation UpdateView($id:String!,$input:ViewUpdateInput!){viewUpdate(id:$id,input:$input){success view{${FIELDS}}}}`,
    {id, input});
  if (!data.viewUpdate.success || !data.viewUpdate.view) throw new Error('The view was not updated.');
  return data.viewUpdate.view;
}

export async function deleteView(id: string): Promise<void> {
  const data = await request<{viewDelete: {success: boolean}}>(
    'mutation DeleteView($id:String!){viewDelete(id:$id){success}}', {id});
  if (!data.viewDelete.success) throw new Error('The view was not deleted.');
}

export async function toggleViewFavorite(id: string): Promise<boolean> {
  const data = await request<{viewFavoriteToggle: {success: boolean; favorite: boolean}}>(
    'mutation FavoriteView($id:String!){viewFavoriteToggle(id:$id){success favorite}}', {id});
  return data.viewFavoriteToggle.favorite;
}

/* ------------------------------ filter translation ------------------------------
 * Saved views map form fields onto the canonical filter tree the server validates
 * (hoja_server/views.py): text search, status, and (for tasks) priority, assignee,
 * and project predicates.
 */

export type ViewFilterFields = {
  query: string;
  state: string;
  priority: string;
  assignee: string;
  project: string;
};

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

export function fieldsFromFilters(filters: ViewDocument): ViewFilterFields {
  const predicates = [filters, ...((filters.and as ViewDocument[] | undefined) || [])];
  let query = '';
  let state = '';
  let priority = '';
  let assignee = '';
  let project = '';
  for (const predicate of predicates) {
    if (!predicate || typeof predicate !== 'object') continue;
    const title = predicate.title as {contains?: string} | undefined;
    const name = predicate.name as {contains?: string} | undefined;
    if (title?.contains) query = title.contains;
    if (name?.contains) query = name.contains;
    const status = predicate.state as {is?: string} | undefined;
    if (status?.is) state = status.is;
    const prio = predicate.priority as {is?: number} | undefined;
    if (typeof prio?.is === 'number' && Number.isInteger(prio.is)) priority = String(prio.is);
    const asgn = predicate.assignee as {is?: string} | undefined;
    if (asgn?.is) assignee = asgn.is;
    const proj = predicate.project as {is?: string} | undefined;
    if (proj?.is) project = proj.is;
  }
  return {query, state, priority, assignee, project};
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
