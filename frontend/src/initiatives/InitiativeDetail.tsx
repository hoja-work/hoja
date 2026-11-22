/* The initiative page: Linear's initiative overview, built on the same detail
 * primitives as the task page (src/graph/detail.tsx). Overview holds the title,
 * summary, latest update, description, projects and activity; Updates holds every
 * update. Properties live in the sidebar exactly where the task page keeps them. */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Box,
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleX,
  Link2,
  Pencil,
  Plus,
  Target,
  Trash2,
  X,
} from "lucide-react";
import { confirmDialog } from "../dialogs";
import { FollowButton } from "../inbox/follow";
import { gql, relTime } from "../main";
import type { Project, Viewer } from "../main";
import { Markdown } from "../markdown";
import { useProjectEntityVersion, useProjectSyncMeta } from "../projectStore";
import {
  ActivityLine,
  Crumb,
  CrumbCurrent,
  CrumbSep,
  DetailButton,
  DetailColumns,
  DetailHeader,
  DetailNotice,
  DetailPage,
  DetailSection,
  DetailSummary,
  DetailTabs,
  DetailTitle,
  MarkdownField,
  PropertiesGroup,
  PropertiesPanel,
} from "../graph/detail";
import { HEALTH, healthColors, healthDot, healthLabel } from "../graph/health";
import {
  Avatar,
  cx,
  fmtDate,
  IconBtn,
  LinMenu,
  LinPopover,
  PriorityIcon,
  Ring,
} from "../graph/shared";
import { PropDropdown, PropStatic } from "../graph/tasks";

type Person = { id: string; name?: string; avatarSeed?: string | null };
type InitiativeProject = Project & { lead?: { name?: string } };
export type InitiativeDetailData = {
  id: string;
  name: string;
  summary?: string | null;
  description?: string | null;
  status: string;
  priority?: number;
  targetDate?: string | null;
  createdAt?: string;
  updatedAt?: string;
  viewerSubscribed?: boolean;
  owner?: Person | null;
  creator?: Person | null;
  subscribers?: { nodes: Person[] };
  projects?: { nodes: InitiativeProject[] };
};
export type InitiativeUpdatePost = {
  id: string;
  body: string;
  health?: string | null;
  createdAt: string;
  updatedAt?: string;
  actor?: Person | null;
};
export type InitiativeTab = "overview" | "updates";

const DETAIL_FIELDS =
  "id name summary description status priority targetDate createdAt updatedAt viewerSubscribed owner{id name avatarSeed} creator{id name avatarSeed} subscribers{nodes{id name avatarSeed}} projects{nodes{id name state progress health targetDate lead{name}}}";
const UPDATE_FIELDS =
  "id body health createdAt updatedAt actor{id name avatarSeed}";

export const INITIATIVE_STATUSES = [
  { id: "planned", label: "Planned", Icon: CircleDashed, tone: "text-muted" },
  { id: "active", label: "Active", Icon: CircleDot, tone: "text-amber-600" },
  {
    id: "completed",
    label: "Completed",
    Icon: CircleCheck,
    tone: "text-indigo-600",
  },
  { id: "canceled", label: "Canceled", Icon: CircleX, tone: "text-muted" },
] as const;
const PRIORITIES = ["No priority", "Urgent", "High", "Medium", "Low"];

export function InitiativeStatusIcon({ status }: { status: string }) {
  const s =
    INITIATIVE_STATUSES.find((x) => x.id === status) || INITIATIVE_STATUSES[0];
  return <s.Icon size={14} className={cx("flex-none", s.tone)} aria-hidden />;
}
export const initiativeStatusLabel = (status: string) =>
  INITIATIVE_STATUSES.find((x) => x.id === status)?.label || status;

/** The health of the latest update, as a dot and a label. */
export function HealthBadge({
  health,
  className,
}: {
  health?: string | null;
  className?: string;
}) {
  return (
    <span
      className={cx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium",
        healthColors(health),
        className,
      )}
    >
      <span className={cx("size-2 rounded-full", healthDot(health))} />
      {healthLabel(health)}
    </span>
  );
}

const byNewest = (a: InitiativeUpdatePost, b: InitiativeUpdatePost) =>
  b.createdAt.localeCompare(a.createdAt);

export function InitiativeDetailPage({
  id,
  tab,
  viewer,
  projects,
  onBack,
  onTab,
  onOpenProject,
  onArchived,
}: {
  id: string;
  tab: InitiativeTab;
  viewer: Viewer;
  projects: Project[];
  onBack: () => void;
  onTab: (tab: InitiativeTab) => void;
  onOpenProject: (id: string) => void;
  onArchived: () => void;
}) {
  const meta = useProjectSyncMeta();
  const version = useProjectEntityVersion(["initiative", "project"], [id]);
  const [data, setData] = useState<InitiativeDetailData | null>(null);
  const [updates, setUpdates] = useState<InitiativeUpdatePost[]>([]);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [retry, setRetry] = useState(0);
  const [moreOpen, setMoreOpen] = useState(false);
  const [composing, setComposing] = useState(false);

  const load = useCallback(
    () =>
      gql(
        `query InitiativeDetail($id:String!,$fid:ID!){initiative(id:$id){${DETAIL_FIELDS}} initiativeUpdates(first:50,filter:{initiative:{id:{eq:$fid}}}){nodes{${UPDATE_FIELDS}}}}`,
        { id, fid: id },
      ).then((d) => {
        if (!d.initiative)
          throw Error("This initiative does not exist or was archived.");
        setData(d.initiative);
        setUpdates(
          [
            ...((d.initiativeUpdates?.nodes || []) as InitiativeUpdatePost[]),
          ].sort(byNewest),
        );
        setLoadError("");
      }),
    [id],
  );
  useEffect(() => {
    let live = true;
    load().catch((e) => live && setLoadError((e as Error).message));
    return () => {
      live = false;
    };
  }, [load, version, retry]);

  const members = useMemo(
    () =>
      (meta?.users || [])
        .filter((u): u is [string, string] => !!u[1])
        .map(([uid, name]) => ({ id: uid, name }))
        .sort((a, b) => a.name.localeCompare(b.name)),
    [meta],
  );
  const canEdit = Boolean(
    data && (viewer.admin || (viewer.id && data.creator?.id === viewer.id)),
  );

  /* Property edits apply at once and roll back if the server refuses them. */
  const change = async (
    input: Record<string, unknown>,
    patch: Partial<InitiativeDetailData>,
  ) => {
    if (!data) return false;
    const before = data;
    setData({ ...data, ...patch });
    setActionError("");
    try {
      const d = await gql(
        `mutation($id:String!,$input:InitiativeUpdateInput!){initiativeUpdate(id:$id,input:$input){success initiative{${DETAIL_FIELDS}}}}`,
        { id, input },
      );
      if (!d.initiativeUpdate?.success)
        throw Error("The initiative could not be updated.");
      if (d.initiativeUpdate.initiative) setData(d.initiativeUpdate.initiative);
      return true;
    } catch (e) {
      setData(before);
      setActionError((e as Error).message);
      return false;
    }
  };

  const archive = async () => {
    if (
      !data ||
      !(await confirmDialog(`Archive initiative ${data.name}?`, {
        danger: true,
      }))
    )
      return;
    try {
      const d = await gql(
        `mutation($id:String!){initiativeArchive(id:$id){success}}`,
        { id },
      );
      if (!d.initiativeArchive?.success)
        throw Error("The initiative could not be archived.");
      onArchived();
    } catch (e) {
      setActionError((e as Error).message);
    }
  };

  const postUpdate = async (body: string, health: string) => {
    setActionError("");
    try {
      const d = await gql(
        `mutation($input:InitiativeUpdateCreateInput!){initiativeUpdateCreate(input:$input){success initiativeUpdate{${UPDATE_FIELDS}}}}`,
        { input: { initiativeId: id, body, health } },
      );
      const created = d.initiativeUpdateCreate?.initiativeUpdate;
      if (!d.initiativeUpdateCreate?.success || !created)
        throw Error("The update could not be posted.");
      setUpdates((rows) => [created, ...rows].sort(byNewest));
      return true;
    } catch (e) {
      setActionError((e as Error).message);
      return false;
    }
  };
  const editUpdate = async (
    post: InitiativeUpdatePost,
    body: string,
    health: string,
  ) => {
    setActionError("");
    try {
      const d = await gql(
        `mutation($id:String!,$input:InitiativeUpdateUpdateInput!){initiativeUpdateUpdate(id:$id,input:$input){success initiativeUpdate{${UPDATE_FIELDS}}}}`,
        { id: post.id, input: { body, health } },
      );
      const saved = d.initiativeUpdateUpdate?.initiativeUpdate;
      if (!d.initiativeUpdateUpdate?.success || !saved)
        throw Error("Only the author can edit an update.");
      setUpdates((rows) => rows.map((r) => (r.id === saved.id ? saved : r)));
      return true;
    } catch (e) {
      setActionError((e as Error).message);
      return false;
    }
  };
  const deleteUpdate = async (post: InitiativeUpdatePost) => {
    if (!(await confirmDialog("Delete this update?", { danger: true }))) return;
    setActionError("");
    try {
      const d = await gql(
        `mutation($id:String!){initiativeUpdateDelete(id:$id){success}}`,
        {
          id: post.id,
        },
      );
      if (!d.initiativeUpdateDelete?.success)
        throw Error("The update could not be deleted.");
      setUpdates((rows) => rows.filter((r) => r.id !== post.id));
    } catch (e) {
      setActionError((e as Error).message);
    }
  };

  const linked = data?.projects?.nodes || [];
  const setProjects = (ids: string[]) =>
    change(
      { projectIds: ids },
      {
        projects: {
          nodes: ids.map(
            (pid) =>
              linked.find((p) => p.id === pid) ||
              projects.find((p) => p.id === pid) || { id: pid, name: "" },
          ),
        },
      },
    );
  const latest = updates[0];
  const progress = linked.length
    ? linked.reduce((sum, p) => sum + (p.progress || 0), 0) / linked.length
    : 0;

  const crumbs = (
    <>
      <Crumb onClick={onBack}>Initiatives</Crumb>
      <CrumbSep />
      <CrumbCurrent title={data?.name}>
        {data?.name || "Initiative"}
      </CrumbCurrent>
    </>
  );

  if (!data)
    return (
      <DetailPage label="Initiative" surface="initiative-detail">
        <DetailHeader crumbs={crumbs} />
        {loadError ? (
          <DetailNotice
            action={
              <DetailButton onClick={() => setRetry((n) => n + 1)}>
                Retry
              </DetailButton>
            }
          >
            {loadError}
          </DetailNotice>
        ) : (
          <p className="p-4 text-sm text-muted" role="status">
            Loading initiative…
          </p>
        )}
      </DetailPage>
    );

  return (
    <DetailPage
      label={data.name}
      surface="initiative-detail"
      data-initiative-detail=""
    >
      <DetailHeader
        crumbs={crumbs}
        actions={
          <>
            <FollowButton
              type="initiative"
              id={data.id}
              known={data.viewerSubscribed}
              compact
              onError={setActionError}
            />
            <IconBtn
              label="Copy link"
              title="Copy link"
              onClick={() => void navigator.clipboard?.writeText(location.href)}
            >
              <Link2 size={15} />
            </IconBtn>
            {canEdit && (
              <span className="relative">
                <IconBtn
                  label="Initiative options"
                  title="Initiative options"
                  onClick={() => setMoreOpen((v) => !v)}
                >
                  <span className="font-bold">···</span>
                </IconBtn>
                {moreOpen && (
                  <LinMenu
                    onClose={() => setMoreOpen(false)}
                    className="absolute top-full right-0 z-50 mt-1 min-w-56"
                    data-style-hooks="lin-dd-right"
                    items={[
                      {
                        key: "archive",
                        label: "Archive initiative",
                        danger: true,
                        icon: <Trash2 size={14} />,
                        onSelect: () => void archive(),
                      },
                    ]}
                  />
                )}
              </span>
            )}
          </>
        }
      />
      <DetailColumns
        data-initiative-content=""
        mainLabel={tab === "updates" ? "Updates" : "Overview"}
        toolbar={
          <DetailTabs
            label="Initiative views"
            current={tab}
            onSelect={onTab}
            tabs={[
              {
                id: "overview",
                label: "Overview",
                href: `/workspace/initiative/${id}`,
              },
              {
                id: "updates",
                label: (
                  <>
                    Updates
                    {updates.length > 0 && (
                      <span className="ml-1.5 text-muted">
                        {updates.length}
                      </span>
                    )}
                  </>
                ),
                href: `/workspace/initiative/${id}/updates`,
              },
            ]}
          />
        }
        main={
          <div className="mx-auto max-w-3xl">
            {actionError && (
              <DetailNotice
                action={
                  <DetailButton onClick={() => setActionError("")}>
                    Dismiss
                  </DetailButton>
                }
              >
                {actionError}
              </DetailNotice>
            )}
            {tab === "updates" ? (
              <UpdatesFeed
                updates={updates}
                viewer={viewer}
                composing={composing}
                onCompose={setComposing}
                onPost={postUpdate}
                onEdit={editUpdate}
                onDelete={deleteUpdate}
              />
            ) : (
              <>
                <span className="mb-4 grid size-10 place-items-center rounded-lg bg-subtle text-muted">
                  <Target size={20} aria-hidden />
                </span>
                <DetailTitle
                  value={data.name}
                  label="Initiative name"
                  disabled={!canEdit}
                  onCommit={(name) => void change({ name }, { name })}
                />
                <DetailSummary
                  value={data.summary}
                  disabled={!canEdit}
                  onCommit={(summary) => void change({ summary }, { summary })}
                />
                <LatestUpdate
                  latest={latest}
                  count={updates.length}
                  composing={composing}
                  onCompose={setComposing}
                  onPost={postUpdate}
                  onSeeAll={() => onTab("updates")}
                />
                <h2 className="mt-8 mb-2 text-sm font-semibold text-muted">
                  Description
                </h2>
                <MarkdownField
                  data-initiative-description=""
                  value={data.description}
                  disabled={!canEdit}
                  placeholder="Add description…"
                  onSave={(description) =>
                    change({ description }, { description })
                  }
                />
                <DetailSection
                  title="Projects"
                  badge={
                    <span className="text-xs font-medium text-muted">
                      {linked.length}
                    </span>
                  }
                  actions={
                    canEdit && (
                      <ProjectPicker
                        all={projects}
                        linked={linked.map((p) => p.id)}
                        onChange={(ids) => void setProjects(ids)}
                      />
                    )
                  }
                >
                  <ProjectTable
                    rows={linked}
                    canEdit={canEdit}
                    onOpen={onOpenProject}
                    onRemove={(pid) =>
                      void setProjects(
                        linked.map((p) => p.id).filter((x) => x !== pid),
                      )
                    }
                  />
                </DetailSection>
                <DetailSection title="Activity" variant="activity">
                  <InitiativeActivity
                    data={data}
                    updates={updates}
                    onOpen={() => onTab("updates")}
                  />
                </DetailSection>
              </>
            )}
          </div>
        }
        aside={
          <PropertiesPanel>
            <PropDropdown
              label="Status"
              icon={<InitiativeStatusIcon status={data.status} />}
              value={initiativeStatusLabel(data.status)}
              disabled={!canEdit}
              onPick={(status) => status && void change({ status }, { status })}
              options={INITIATIVE_STATUSES.map((s) => ({
                id: s.id,
                selected: s.id === data.status,
                label: (
                  <span className="inline-flex items-center gap-2">
                    <InitiativeStatusIcon status={s.id} />
                    {s.label}
                  </span>
                ),
              }))}
            />
            <PropDropdown
              label="Priority"
              icon={<PriorityIcon priority={data.priority || 0} />}
              value={PRIORITIES[data.priority || 0]}
              disabled={!canEdit}
              onPick={(v) => {
                const priority = Number(v);
                void change({ priority }, { priority });
              }}
              options={PRIORITIES.map((label, v) => ({
                id: String(v),
                selected: (data.priority || 0) === v,
                label: (
                  <span className="inline-flex items-center gap-2">
                    <PriorityIcon priority={v} />
                    {label}
                  </span>
                ),
              }))}
            />
            <PropDropdown
              label="Owner"
              icon={
                <Avatar
                  name={data.owner?.name}
                  seed={data.owner?.avatarSeed}
                  size={16}
                />
              }
              value={data.owner?.name || "No owner"}
              disabled={!canEdit}
              onPick={(ownerId) =>
                void change(
                  { ownerId },
                  {
                    owner: ownerId
                      ? {
                          id: ownerId,
                          name: members.find((m) => m.id === ownerId)?.name,
                        }
                      : null,
                  },
                )
              }
              options={[
                { id: null, selected: !data.owner, label: "No owner" },
                ...members.map((m) => ({
                  id: m.id,
                  selected: m.id === data.owner?.id,
                  label: (
                    <span className="inline-flex items-center gap-2">
                      <Avatar name={m.name} size={16} />
                      {m.name}
                    </span>
                  ),
                })),
              ]}
            />
            <PropStatic
              label="Target date"
              icon={null}
              value={
                <input
                  type="date"
                  aria-label="Target date"
                  className="min-w-0 rounded-md border border-line bg-panel px-2 py-1 text-sm text-foreground disabled:border-transparent disabled:bg-transparent"
                  value={(data.targetDate || "").slice(0, 10)}
                  disabled={!canEdit}
                  onChange={(e) => {
                    const targetDate = e.target.value || null;
                    void change({ targetDate }, { targetDate });
                  }}
                />
              }
            />
            <PropertiesGroup>Health</PropertiesGroup>
            <div className="flex min-h-8 items-center gap-2 text-sm">
              <HealthBadge health={latest?.health} />
              {latest && (
                <span className="text-xs text-muted">
                  {relTime(latest.createdAt)}
                </span>
              )}
            </div>
            <PropertiesGroup>Progress</PropertiesGroup>
            <div className="flex min-h-8 items-center gap-2 text-sm">
              <Ring frac={progress} size={16} />
              <span>{Math.round(progress * 100)}%</span>
              <span className="text-muted">
                across {linked.length}{" "}
                {linked.length === 1 ? "project" : "projects"}
              </span>
            </div>
            <PropertiesGroup>Followers</PropertiesGroup>
            <div className="flex min-h-8 flex-wrap items-center gap-1">
              {(data.subscribers?.nodes || []).length ? (
                (data.subscribers?.nodes || []).map((s) => (
                  <Avatar
                    key={s.id}
                    name={s.name}
                    seed={s.avatarSeed}
                    title={s.name}
                    size={20}
                  />
                ))
              ) : (
                <span className="text-sm text-muted">No followers</span>
              )}
            </div>
          </PropertiesPanel>
        }
      />
    </DetailPage>
  );
}

/** `/workspace/initiative/<id>[/updates]`: the page plus its tab, kept in the URL. */
export function parseInitiativePath(path: string): {
  id: string;
  tab: InitiativeTab;
} {
  const rest = path.split("/").filter(Boolean);
  const at = rest.indexOf("initiative");
  return {
    id: decodeURIComponent(rest[at + 1] || ""),
    tab: rest[at + 2] === "updates" ? "updates" : "overview",
  };
}

export function InitiativeRoute(
  props: Omit<
    React.ComponentProps<typeof InitiativeDetailPage>,
    "id" | "tab" | "onTab"
  >,
) {
  const [route, setRoute] = useState(() =>
    parseInitiativePath(location.pathname),
  );
  useEffect(() => {
    const onPop = () => setRoute(parseInitiativePath(location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return (
    <InitiativeDetailPage
      key={route.id}
      {...props}
      id={route.id}
      tab={route.tab}
      onTab={(tab) => {
        const path = `/workspace/initiative/${encodeURIComponent(route.id)}${tab === "updates" ? "/updates" : ""}`;
        history.pushState({}, "", path);
        setRoute({ id: route.id, tab });
      }}
    />
  );
}

/* ---------------- updates ---------------- */

function UpdateComposer({
  initial,
  initialHealth,
  submitLabel,
  onSubmit,
  onCancel,
}: {
  initial?: string;
  initialHealth?: string | null;
  submitLabel: string;
  onSubmit: (body: string, health: string) => Promise<boolean>;
  onCancel: () => void;
}) {
  const [body, setBody] = useState(initial || "");
  const [health, setHealth] = useState<string>(initialHealth || "on track");
  const [busy, setBusy] = useState(false);
  /* Opening the composer is an explicit request to write, so the caret goes there. */
  const bodyRef = React.useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => bodyRef.current?.focus(), []);
  return (
    <form
      className="grid gap-3 rounded-lg border border-line bg-panel p-4"
      data-initiative-update-composer=""
      onSubmit={(e) => {
        e.preventDefault();
        if (!body.trim() || busy) return;
        setBusy(true);
        void onSubmit(body.trim(), health).then((ok) => {
          setBusy(false);
          if (ok) onCancel();
        });
      }}
    >
      <div
        className="flex flex-wrap gap-1.5"
        role="radiogroup"
        aria-label="Health"
      >
        {HEALTH.map((h) => (
          <button
            key={h}
            type="button"
            role="radio"
            aria-checked={health === h}
            onClick={() => setHealth(h)}
            className="rounded-full border border-transparent focus-visible:outline-2 focus-visible:outline-accent aria-checked:border-line aria-[checked=false]:opacity-60"
          >
            <HealthBadge health={h} />
          </button>
        ))}
      </div>
      <textarea
        className="min-h-24 w-full resize-y rounded-md border border-line bg-bg p-2 text-sm text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent"
        aria-label="Update"
        placeholder="Write an update on progress, risks and what is next…"
        ref={bodyRef}
        value={body}
        onChange={(e) => setBody(e.target.value)}
      />
      <div className="flex justify-end gap-2">
        <DetailButton onClick={onCancel}>Cancel</DetailButton>
        <DetailButton primary type="submit" disabled={!body.trim() || busy}>
          {busy ? "Posting…" : submitLabel}
        </DetailButton>
      </div>
    </form>
  );
}

function UpdateCard({
  post,
  viewer,
  onEdit,
  onDelete,
}: {
  post: InitiativeUpdatePost;
  viewer?: Viewer;
  onEdit?: (
    post: InitiativeUpdatePost,
    body: string,
    health: string,
  ) => Promise<boolean>;
  onDelete?: (post: InitiativeUpdatePost) => void;
}) {
  const [editing, setEditing] = useState(false);
  const mine = Boolean(viewer?.id && post.actor?.id === viewer.id);
  if (editing && onEdit)
    return (
      <UpdateComposer
        initial={post.body}
        initialHealth={post.health}
        submitLabel="Save"
        onSubmit={(body, health) => onEdit(post, body, health)}
        onCancel={() => setEditing(false)}
      />
    );
  return (
    <article
      className="group grid gap-3 rounded-lg border border-line bg-panel p-4"
      data-initiative-update=""
    >
      <header className="flex items-center gap-2 text-sm">
        <HealthBadge health={post.health} />
        <Avatar
          name={post.actor?.name}
          seed={post.actor?.avatarSeed}
          size={18}
        />
        <span className="font-medium">{post.actor?.name || "Someone"}</span>
        <time
          className="text-muted"
          dateTime={post.createdAt}
          title={new Date(post.createdAt).toLocaleString()}
        >
          {relTime(post.createdAt)}
        </time>
        <span className="flex-1" />
        {mine && onEdit && (
          <IconBtn
            label="Edit update"
            title="Edit update"
            onClick={() => setEditing(true)}
          >
            <Pencil size={14} />
          </IconBtn>
        )}
        {(mine || viewer?.admin) && onDelete && (
          <IconBtn
            label="Delete update"
            title="Delete update"
            onClick={() => onDelete(post)}
          >
            <Trash2 size={14} />
          </IconBtn>
        )}
      </header>
      <div className="text-sm leading-relaxed">
        <Markdown text={post.body} density="block" />
      </div>
    </article>
  );
}

function LatestUpdate({
  latest,
  count,
  composing,
  onCompose,
  onPost,
  onSeeAll,
}: {
  latest?: InitiativeUpdatePost;
  count: number;
  composing: boolean;
  onCompose: (open: boolean) => void;
  onPost: (body: string, health: string) => Promise<boolean>;
  onSeeAll: () => void;
}) {
  if (composing)
    return (
      <UpdateComposer
        submitLabel="Post update"
        onSubmit={onPost}
        onCancel={() => onCompose(false)}
      />
    );
  if (!latest)
    return (
      <button
        type="button"
        className="flex w-full items-center justify-center gap-2 rounded-lg border border-line bg-panel p-4 text-sm text-muted transition-colors duration-150 ease-out hover:bg-subtle hover:text-foreground hover:transition-none focus-visible:outline-2 focus-visible:outline-accent"
        onClick={() => onCompose(true)}
      >
        <Pencil size={14} aria-hidden /> Write first initiative update
      </button>
    );
  return (
    <section className="grid gap-2" aria-label="Latest update">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-semibold text-muted">Latest update</span>
        <span className="flex-1" />
        {count > 1 && (
          <button
            type="button"
            className="text-muted hover:text-foreground"
            onClick={onSeeAll}
          >
            See all {count}
          </button>
        )}
        <DetailButton onClick={() => onCompose(true)}>
          <Plus size={14} aria-hidden /> New update
        </DetailButton>
      </div>
      <UpdateCard post={latest} />
    </section>
  );
}

function UpdatesFeed({
  updates,
  viewer,
  composing,
  onCompose,
  onPost,
  onEdit,
  onDelete,
}: {
  updates: InitiativeUpdatePost[];
  viewer: Viewer;
  composing: boolean;
  onCompose: (open: boolean) => void;
  onPost: (body: string, health: string) => Promise<boolean>;
  onEdit: (
    post: InitiativeUpdatePost,
    body: string,
    health: string,
  ) => Promise<boolean>;
  onDelete: (post: InitiativeUpdatePost) => void;
}) {
  return (
    <div className="grid gap-4" data-initiative-updates="">
      <div className="flex items-center">
        <h2 className="m-0 flex-1 text-base font-semibold">Updates</h2>
        {!composing && (
          <DetailButton primary onClick={() => onCompose(true)}>
            <Plus size={14} aria-hidden /> New update
          </DetailButton>
        )}
      </div>
      {composing && (
        <UpdateComposer
          submitLabel="Post update"
          onSubmit={onPost}
          onCancel={() => onCompose(false)}
        />
      )}
      {updates.map((u) => (
        <UpdateCard
          key={u.id}
          post={u}
          viewer={viewer}
          onEdit={onEdit}
          onDelete={onDelete}
        />
      ))}
      {!updates.length && !composing && (
        <p className="text-sm text-muted">
          No updates yet. An update states how the initiative is going and is
          shown on the overview until the next one.
        </p>
      )}
    </div>
  );
}

/* ---------------- projects ---------------- */

function ProjectTable({
  rows,
  canEdit,
  onOpen,
  onRemove,
}: {
  rows: InitiativeProject[];
  canEdit: boolean;
  onOpen: (id: string) => void;
  onRemove: (id: string) => void;
}) {
  if (!rows.length)
    return (
      <p className="py-2 text-sm text-muted">
        No projects yet. Add the projects that move this initiative forward.
      </p>
    );
  return (
    <div
      role="table"
      aria-label="Projects"
      className="text-sm"
      data-initiative-projects=""
    >
      <div
        role="row"
        className="hidden grid-cols-12 gap-3 border-b border-line px-2 py-2 text-xs font-medium text-muted md:grid"
      >
        <span role="columnheader" className="col-span-5">
          Name
        </span>
        <span role="columnheader" className="col-span-2">
          Health
        </span>
        <span role="columnheader" className="col-span-2">
          Lead
        </span>
        <span role="columnheader" className="col-span-2">
          Target date
        </span>
        <span role="columnheader" className="col-span-1">
          Progress
        </span>
      </div>
      {rows.map((p) => (
        <div
          role="row"
          key={p.id}
          className="group flex items-center gap-3 rounded-md px-2 py-2 transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none md:grid md:grid-cols-12"
        >
          <span
            role="cell"
            className="flex min-w-0 flex-1 items-center gap-2 md:col-span-5"
          >
            <Box size={14} className="flex-none text-muted" aria-hidden />
            <button
              type="button"
              className="min-w-0 truncate text-left font-medium hover:underline"
              onClick={() => onOpen(p.id)}
            >
              {p.name || "Untitled project"}
            </button>
            {canEdit && (
              <span className="ml-auto opacity-0 group-hover:opacity-100 group-focus-within:opacity-100">
                <IconBtn
                  label={`Remove ${p.name}`}
                  title="Remove from initiative"
                  onClick={() => onRemove(p.id)}
                >
                  <X size={13} />
                </IconBtn>
              </span>
            )}
          </span>
          <span role="cell" className="flex-none md:col-span-2">
            <HealthBadge health={p.health} />
          </span>
          <span
            role="cell"
            className="hidden min-w-0 items-center gap-1.5 md:col-span-2 md:flex"
          >
            {p.lead?.name ? (
              <>
                <Avatar name={p.lead.name} size={16} />
                <span className="truncate">{p.lead.name}</span>
              </>
            ) : (
              <span className="text-muted">No lead</span>
            )}
          </span>
          <span
            role="cell"
            className="hidden text-muted md:col-span-2 md:block"
          >
            {p.targetDate ? fmtDate(p.targetDate) : "—"}
          </span>
          <span
            role="cell"
            className="flex flex-none items-center gap-1.5 md:col-span-1"
          >
            <Ring frac={p.progress || 0} size={14} />
            {Math.round((p.progress || 0) * 100)}%
          </span>
        </div>
      ))}
    </div>
  );
}

function ProjectPicker({
  all,
  linked,
  onChange,
}: {
  all: Project[];
  linked: string[];
  onChange: (ids: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const findRef = React.useRef<HTMLInputElement | null>(null);
  useEffect(() => {
    if (open) findRef.current?.focus();
  }, [open]);
  const shown = all
    .filter((p) => !p.id.startsWith("optimistic:"))
    .filter((p) => p.name.toLowerCase().includes(query.trim().toLowerCase()));
  return (
    <span className="relative">
      <IconBtn
        label="Add project"
        title="Add project"
        onClick={() => setOpen((v) => !v)}
      >
        <Plus size={14} />
      </IconBtn>
      {open && (
        <LinPopover
          onClose={() => setOpen(false)}
          className="absolute top-full right-0 z-50 mt-1 grid max-h-80 w-64 gap-1 overflow-y-auto rounded-lg border border-line bg-panel p-1 shadow-lg"
          data-style-hooks="lin-dd-right"
          role="listbox"
          aria-label="Projects"
        >
          <input
            className="rounded-md border border-line bg-bg px-2 py-1.5 text-sm placeholder:text-muted"
            placeholder="Find a project…"
            aria-label="Find a project"
            ref={findRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {shown.map((p) => {
            const on = linked.includes(p.id);
            return (
              <button
                key={p.id}
                type="button"
                role="option"
                aria-selected={on}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-subtle aria-selected:font-semibold"
                onClick={() =>
                  onChange(
                    on ? linked.filter((x) => x !== p.id) : [...linked, p.id],
                  )
                }
              >
                <Box size={14} className="flex-none text-muted" aria-hidden />
                <span className="min-w-0 flex-1 truncate">{p.name}</span>
                {on && (
                  <CircleCheck size={14} className="text-muted" aria-hidden />
                )}
              </button>
            );
          })}
          {!shown.length && (
            <p className="p-2 text-sm text-muted">No matching projects.</p>
          )}
        </LinPopover>
      )}
    </span>
  );
}

/* ---------------- activity ---------------- */

function InitiativeActivity({
  data,
  updates,
  onOpen,
}: {
  data: InitiativeDetailData;
  updates: InitiativeUpdatePost[];
  onOpen: () => void;
}) {
  const time = (iso?: string) =>
    iso ? (
      <time dateTime={iso} title={new Date(iso).toLocaleString()}>
        {relTime(iso)}
      </time>
    ) : undefined;
  return (
    <div>
      {updates.slice(0, 10).map((u) => (
        <ActivityLine
          key={u.id}
          avatar={
            <Avatar name={u.actor?.name} seed={u.actor?.avatarSeed} size={18} />
          }
          time={time(u.createdAt)}
        >
          <b>{u.actor?.name || "Someone"}</b> posted an update ·{" "}
          <button
            type="button"
            className="text-muted underline-offset-2 hover:underline"
            onClick={onOpen}
          >
            {healthLabel(u.health)}
          </button>
        </ActivityLine>
      ))}
      <ActivityLine
        avatar={
          <Avatar
            name={data.creator?.name}
            seed={data.creator?.avatarSeed}
            size={18}
          />
        }
        time={time(data.createdAt)}
      >
        <b>{data.creator?.name || "Someone"}</b> created the initiative
      </ActivityLine>
    </div>
  );
}
