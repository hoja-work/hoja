/* The initiative page: Linear's initiative overview, built on the same detail
 * primitives as the task page (src/graph/detail.tsx). Overview holds the title,
 * summary, property chips, resources, latest update, description, sub-initiatives,
 * projects, the progress graph and activity; Updates holds every update with its
 * reactions and replies. The properties sidebar can be hidden, and below 900px it
 * opens as an overlay instead of pushing the page down. */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  BellRing,
  Box,
  Calendar,
  CornerLeftUp,
  Hash,
  Link2,
  PanelRight,
  Pencil,
  Plus,
  SmilePlus,
  Sparkles,
  Tag,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { confirmDialog } from "../dialogs";
import { FollowButton } from "../inbox/follow";
import { gql, relTime } from "../main";
import type { Project, Viewer } from "../main";
import { Markdown } from "../markdown";
import { useMenuDismiss } from "../menu";
import { useProjectEntityVersion } from "../projectStore";
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
  PickerMenu,
  PropertiesPanel,
  PropertyChip,
} from "../graph/detail";
import { HEALTH, healthLabel } from "../graph/health";
import {
  Avatar,
  fmtDate,
  IconBtn,
  LinMenu,
  LinPopover,
  PriorityIcon,
  Ring,
} from "../graph/shared";
import { PropDropdown, PropStatic } from "../graph/tasks";
import {
  deleteInitiativeView,
  fetchAllInitiatives,
  fetchPageViews,
  fetchReminderPolicy,
  fetchSlackChannels,
  policyLabel,
  progressFraction,
  updateInitiative,
  type PageView,
  type ReminderPolicy,
  type SlackChannel,
  type InitiativeLabel,
  type InitiativeProject,
  type InitiativeRef,
  type Person,
} from "./api";
import { CreateInitiativeDialog } from "./CreateInitiativeDialog";
import { AddPageView, InsightsTab, PageViewTab } from "./InitiativeTabs";
import {
  DateMenu,
  HealthBadge,
  IconColorMenu,
  InitiativeIcon,
  InitiativeMenu,
  InitiativeStatusIcon,
  initiativeStatusLabel,
  INITIATIVE_STATUSES,
  LabelDot,
  LabelMenu,
  MemberMenu,
  PRIORITIES,
  PriorityMenu,
  REMINDERS,
  StatusMenu,
  TeamMenu,
  useMembers,
  useTeams,
} from "./fields";
import { ProgressGraph, type ProgressSeries } from "./ProgressGraph";

export { HealthBadge, InitiativeStatusIcon, initiativeStatusLabel };

export type InitiativeDetailData = {
  id: string;
  identifier: string;
  number: number;
  name: string;
  summary?: string | null;
  description?: string | null;
  status: string;
  priority?: number;
  targetDate?: string | null;
  createdAt?: string;
  updatedAt?: string;
  viewerSubscribed?: boolean;
  icon?: string | null;
  color?: string | null;
  health?: string | null;
  updateReminder?: string | null;
  updateDueAt?: string | null;
  owner?: Person | null;
  creator?: Person | null;
  parents?: InitiativeRef[];
  slackChannel?: { id: string; name: string } | null;
  children?: (InitiativeRef & {
    status: string;
    health?: string | null;
    owner?: Person | null;
    targetDate?: string | null;
  })[];
  leadTeam?: { id: string; name: string; key: string } | null;
  labels?: InitiativeLabel[];
  resources?: { id: string; title: string; url: string }[];
  subscribers?: { nodes: Person[] };
  projects?: { nodes: InitiativeProject[] };
  allProjects?: InitiativeProject[];
  projectHealth?: {
    onTrack: number;
    atRisk: number;
    offTrack: number;
    noUpdate: number;
  };
  progressHistory?: ProgressSeries[];
};
type Reaction = {
  emoji: string;
  count: number;
  viewerReacted: boolean;
  users: Person[];
};
type Reply = {
  id: string;
  body: string;
  createdAt: string;
  user?: Person | null;
};
export type InitiativeUpdatePost = {
  id: string;
  body: string;
  health?: string | null;
  createdAt: string;
  updatedAt?: string;
  actor?: Person | null;
  reactions?: Reaction[];
  comments?: { nodes: Reply[] };
};
export type InitiativeTab =
  "overview" | "updates" | "insights" | `view:${string}`;

const PROJECT_FIELDS =
  "id name state progress health targetDate lead{name} teams{nodes{id name key}}";
const DETAIL_FIELDS = `id identifier number name summary description status priority targetDate createdAt updatedAt viewerSubscribed icon color health updateReminder updateDueAt owner{id name avatarSeed} creator{id name avatarSeed} subscribers{nodes{id name avatarSeed}} parents{id identifier name icon color} slackChannel{id name} children{id identifier name icon color status health targetDate owner{id name avatarSeed}} leadTeam{id name key} labels{id name color} resources{id title url} projects{nodes{${PROJECT_FIELDS}}} allProjects{${PROJECT_FIELDS}} projectHealth{onTrack atRisk offTrack noUpdate} progressHistory(weeks:12){project{id name} scope points{weekStart completed}}`;
const UPDATE_FIELDS =
  "id body health createdAt updatedAt actor{id name avatarSeed} reactions{emoji count viewerReacted users{id name}} comments{nodes{id body createdAt user{id name avatarSeed}}}";
const REACTIONS = ["👍", "🎉", "❤️", "🚀", "👀", "🙏"];
const PANEL_KEY = "hoja.initiative.panel";

const byNewest = (a: InitiativeUpdatePost, b: InitiativeUpdatePost) =>
  b.createdAt.localeCompare(a.createdAt);

// On touch screens every control on the page (tabs, property chips, resource and project rows,
// the properties drawer) gets a 44px target, and the hover-only tooltips stay hidden: a tap
// emulates hover, which left a tooltip stuck over the drawer or menu it had just opened.
const TOUCH_FIT = [
  "pointer-coarse:[&_button]:min-h-11 shell-phone:[&_button]:min-h-11",
  "pointer-coarse:[&_[role=tab]]:min-h-11 shell-phone:[&_[role=tab]]:min-h-11",
  "pointer-coarse:[&_input[type=date]]:min-h-11 shell-phone:[&_input[type=date]]:min-h-11",
  "pointer-coarse:[&_[data-style-hooks~='lin-icon-btn']]:min-w-11 shell-phone:[&_[data-style-hooks~='lin-icon-btn']]:min-w-11",
  "pointer-coarse:[&_a[href]]:inline-flex pointer-coarse:[&_a[href]]:items-center pointer-coarse:[&_a[href]]:min-h-11",
  "pointer-coarse:[&_[data-tip]]:before:hidden pointer-coarse:[&_[data-tip]]:after:hidden",
].join(" ");

function useNarrow() {
  const query = "(max-width: 900px)";
  const [narrow, setNarrow] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setNarrow(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return narrow;
}

function readPanelPref(): boolean {
  try {
    return localStorage.getItem(PANEL_KEY) !== "hidden";
  } catch {
    return true;
  }
}

export function InitiativeDetailPage({
  id,
  tab,
  viewer,
  projects,
  onBack,
  onTab,
  onOpenProject,
  onOpenInitiative,
  onArchived,
}: {
  id: string;
  tab: InitiativeTab;
  viewer: Viewer;
  projects: Project[];
  onBack: () => void;
  onTab: (tab: InitiativeTab) => void;
  onOpenProject: (id: string) => void;
  onOpenInitiative: (id: string) => void;
  onArchived: () => void;
}) {
  const [data, setData] = useState<InitiativeDetailData | null>(null);
  const version = useProjectEntityVersion(
    ["initiative", "project", "issue"],
    data ? [data.id] : [id],
  );
  const [updates, setUpdates] = useState<InitiativeUpdatePost[]>([]);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [retry, setRetry] = useState(0);
  const [moreOpen, setMoreOpen] = useState(false);
  const [composing, setComposing] = useState(false);
  const [creatingSub, setCreatingSub] = useState(false);
  const [others, setOthers] = useState<
    (InitiativeRef & { parents?: { id: string }[] })[]
  >([]);
  const narrow = useNarrow();
  const [panelPref, setPanelPref] = useState(readPanelPref);
  const [overlayOpen, setOverlayOpen] = useState(false);
  const panelShown = narrow ? overlayOpen : panelPref;
  /* The phone overlay closes on Escape or an outside press. Its dropdowns render in
   * portals, so presses inside popover content still count as inside. */
  const overlayRef = React.useRef<HTMLDivElement | null>(null);
  useMenuDismiss({
    open: narrow && overlayOpen,
    within: [overlayRef],
    alsoWithin: (node) =>
      Boolean(
        (node as Element).closest?.(
          '[data-slot="popover-content"],[data-radix-popper-content-wrapper]',
        ),
      ),
    onDismiss: () => setOverlayOpen(false),
  });
  useEffect(() => {
    if (narrow && overlayOpen)
      overlayRef.current?.querySelector<HTMLElement>("button")?.focus();
  }, [narrow, overlayOpen]);
  const togglePanel = () => {
    if (narrow) return setOverlayOpen((v) => !v);
    setPanelPref((v) => {
      try {
        localStorage.setItem(PANEL_KEY, v ? "hidden" : "shown");
      } catch {
        /* The preference is a convenience; the toggle still works without it. */
      }
      return !v;
    });
  };

  const load = useCallback(
    () =>
      gql(
        `query InitiativeDetail($id:String!){initiative(id:$id){${DETAIL_FIELDS}}}`,
        { id },
      ).then(async (d) => {
        if (!d.initiative)
          throw Error("This initiative does not exist or was archived.");
        const u = await gql(
          `query InitiativeUpdates($fid:ID!){initiativeUpdates(first:50,filter:{initiative:{id:{eq:$fid}}}){nodes{${UPDATE_FIELDS}}}}`,
          { fid: d.initiative.id },
        );
        setData(d.initiative);
        setUpdates(
          [
            ...((u.initiativeUpdates?.nodes || []) as InitiativeUpdatePost[]),
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
  /* Other initiatives, for the parent and sub-initiative pickers. */
  const loadOthers = useCallback(() => {
    fetchAllInitiatives()
      .then((rows) => setOthers(rows))
      .catch(() => undefined);
  }, []);
  useEffect(loadOthers, [loadOthers]);

  const members = useMembers();
  const teams = useTeams();
  /* Any member edits an initiative, as on Linear; archiving stays with its owner, its
   * creator or an admin. */
  const canEdit = Boolean(data && viewer.id);
  const canArchive = Boolean(
    data &&
    (viewer.admin ||
      (viewer.id &&
        (data.creator?.id === viewer.id || data.owner?.id === viewer.id))),
  );
  const [policy, setPolicy] = useState<ReminderPolicy | null>(null);
  useEffect(() => {
    fetchReminderPolicy()
      .then(setPolicy)
      .catch(() => undefined);
  }, []);
  const [pageViews, setPageViews] = useState<PageView[]>([]);
  const [addingView, setAddingView] = useState(false);
  useEffect(() => {
    if (data?.id)
      fetchPageViews(data.id)
        .then(setPageViews)
        .catch(() => undefined);
  }, [data?.id]);

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
        { id: data.id, input },
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
        { id: data.id },
      );
      if (!d.initiativeArchive?.success)
        throw Error("The initiative could not be archived.");
      onArchived();
    } catch (e) {
      setActionError((e as Error).message);
    }
  };

  const run = async <T,>(fn: () => Promise<T>) => {
    setActionError("");
    try {
      return await fn();
    } catch (e) {
      setActionError((e as Error).message);
      return undefined;
    }
  };
  const replaceUpdate = (saved: InitiativeUpdatePost) =>
    setUpdates((rows) => rows.map((r) => (r.id === saved.id ? saved : r)));
  const postUpdate = async (body: string, health: string) =>
    Boolean(
      await run(async () => {
        const d = await gql(
          `mutation($input:InitiativeUpdateCreateInput!){initiativeUpdateCreate(input:$input){success initiativeUpdate{${UPDATE_FIELDS}}}}`,
          { input: { initiativeId: data!.id, body, health } },
        );
        const created = d.initiativeUpdateCreate?.initiativeUpdate;
        if (!d.initiativeUpdateCreate?.success || !created)
          throw Error("The update could not be posted.");
        setUpdates((rows) => [created, ...rows].sort(byNewest));
        setData((cur) => (cur ? { ...cur, health } : cur));
        return true;
      }),
    );
  const editUpdate = async (
    post: InitiativeUpdatePost,
    body: string,
    health: string,
  ) =>
    Boolean(
      await run(async () => {
        const d = await gql(
          `mutation($id:String!,$input:InitiativeUpdateUpdateInput!){initiativeUpdateUpdate(id:$id,input:$input){success initiativeUpdate{${UPDATE_FIELDS}}}}`,
          { id: post.id, input: { body, health } },
        );
        const saved = d.initiativeUpdateUpdate?.initiativeUpdate;
        if (!d.initiativeUpdateUpdate?.success || !saved)
          throw Error("Only the author can edit an update.");
        replaceUpdate(saved);
        return true;
      }),
    );
  const deleteUpdate = async (post: InitiativeUpdatePost) => {
    if (!(await confirmDialog("Delete this update?", { danger: true }))) return;
    await run(async () => {
      const d = await gql(
        `mutation($id:String!){initiativeUpdateDelete(id:$id){success}}`,
        { id: post.id },
      );
      if (!d.initiativeUpdateDelete?.success)
        throw Error("The update could not be deleted.");
      setUpdates((rows) => rows.filter((r) => r.id !== post.id));
    });
  };
  const react = (post: InitiativeUpdatePost, emoji: string) =>
    void run(async () => {
      const d = await gql(
        `mutation($u:String!,$e:String!){initiativeUpdateReact(updateId:$u,emoji:$e){${UPDATE_FIELDS}}}`,
        { u: post.id, e: emoji },
      );
      if (d.initiativeUpdateReact) replaceUpdate(d.initiativeUpdateReact);
    });
  const reply = async (post: InitiativeUpdatePost, body: string) =>
    Boolean(
      await run(async () => {
        const d = await gql(
          `mutation($input:CommentCreateInput!){commentCreate(input:$input){success comment{id body createdAt user{id name avatarSeed}}}}`,
          { input: { initiativeUpdateId: post.id, body } },
        );
        if (!d.commentCreate?.success || !d.commentCreate.comment)
          throw Error("The reply could not be posted.");
        replaceUpdate({
          ...post,
          comments: {
            nodes: [...(post.comments?.nodes || []), d.commentCreate.comment],
          },
        });
        return true;
      }),
    );
  const draft = () =>
    run(async () => {
      const d = await gql(
        `mutation($id:String!){initiativeUpdateDraft(initiativeId:$id){body health source}}`,
        { id: data!.id },
      );
      return d.initiativeUpdateDraft as DraftResult;
    });
  const addResource = (url: string, title: string) =>
    run(async () => {
      const d = await gql(
        `mutation($i:String!,$u:String!,$t:String){initiativeResourceCreate(initiativeId:$i,url:$u,title:$t){success resource{id title url}}}`,
        { i: data!.id, u: url, t: title || null },
      );
      const res = d.initiativeResourceCreate?.resource;
      if (!res) throw Error("The link could not be added.");
      setData((cur) =>
        cur ? { ...cur, resources: [...(cur.resources || []), res] } : cur,
      );
      return true;
    });
  const removeResource = (rid: string) =>
    void run(async () => {
      const d = await gql(
        `mutation($id:String!){initiativeResourceDelete(id:$id){success}}`,
        { id: rid },
      );
      if (!d.initiativeResourceDelete?.success)
        throw Error("The link could not be removed.");
      setData((cur) =>
        cur
          ? {
              ...cur,
              resources: (cur.resources || []).filter((r) => r.id !== rid),
            }
          : cur,
      );
    });

  const direct = data?.projects?.nodes || [];
  const setProjects = (ids: string[]) =>
    change(
      { projectIds: ids },
      {
        projects: {
          nodes: ids.map(
            (pid) =>
              direct.find((p) => p.id === pid) ||
              (projects.find((p) => p.id === pid) as InitiativeProject) || {
                id: pid,
                name: "",
              },
          ),
        },
      },
    );
  const latest = updates[0];
  const allProjects = data?.allProjects || direct;
  const progress = progressFraction(allProjects);
  /* A parent may not be the initiative itself or anything below it. */
  const below = useMemo(() => {
    const out = new Set<string>(data ? [data.id] : []);
    let grew = true;
    while (grew) {
      grew = false;
      for (const o of others) {
        if (!out.has(o.id) && (o.parents || []).some((p) => out.has(p.id))) {
          out.add(o.id);
          grew = true;
        }
      }
    }
    return out;
  }, [data, others]);
  const currentParents = new Set((data?.parents || []).map((p) => p.id));
  const parentOptions = others.filter(
    (o) => !below.has(o.id) && !currentParents.has(o.id),
  );
  /* Existing initiatives that can become a sub-initiative here: not this one, not
   * already a child, and not above it (that would close a loop). */
  const above = useMemo(() => {
    const out = new Set<string>();
    let frontier = (data?.parents || []).map((p) => p.id);
    while (frontier.length) {
      frontier.forEach((id) => out.add(id));
      frontier = others
        .filter((o) => frontier.includes(o.id))
        .flatMap((o) => (o.parents || []).map((p) => p.id))
        .filter((id) => !out.has(id));
    }
    return out;
  }, [data, others]);
  const childIds = new Set((data?.children || []).map((c) => c.id));
  const childOptions = others.filter(
    (o) => o.id !== data?.id && !childIds.has(o.id) && !above.has(o.id),
  );
  const firstParent = data?.parents?.[0];

  const crumbs = (
    <>
      <Crumb onClick={onBack}>Initiatives</Crumb>
      <CrumbSep />
      {firstParent && (
        <>
          <Crumb onClick={() => onOpenInitiative(firstParent.id)}>
            {firstParent.name}
          </Crumb>
          <CrumbSep />
        </>
      )}
      <CrumbCurrent
        title={data ? `${data.identifier} ${data.name}` : undefined}
      >
        {data ? (
          <>
            <span className="mr-1.5 text-muted">{data.identifier}</span>
            {data.name}
          </>
        ) : (
          "Initiative"
        )}
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

  const sidebar = (
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
      <PropDropdown
        label="Lead team"
        icon={<Users size={14} aria-hidden />}
        value={data.leadTeam?.name || "No lead team"}
        disabled={!canEdit}
        onPick={(leadTeamId) =>
          void change(
            { leadTeamId },
            { leadTeam: teams.find((t) => t.id === leadTeamId) || null },
          )
        }
        options={[
          { id: null, selected: !data.leadTeam, label: "No lead team" },
          ...teams.map((t) => ({
            id: t.id,
            selected: t.id === data.leadTeam?.id,
            label: `${t.name} (${t.key})`,
          })),
        ]}
      />
      <PropStatic
        label="Target date"
        icon={<Calendar size={14} aria-hidden />}
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
      <PropDropdown
        label="Reminders"
        icon={<BellRing size={14} aria-hidden />}
        value={
          data.updateReminder
            ? REMINDERS.find((r) => r.id === data.updateReminder)?.label ||
              data.updateReminder
            : `Workspace: ${policyLabel(policy)}`
        }
        disabled={!canEdit}
        onPick={(rid) => {
          const updateReminder = rid === "default" ? null : rid;
          void change({ updateReminder }, { updateReminder }).then(() =>
            load(),
          );
        }}
        options={REMINDERS.map((r) => ({
          id: r.id,
          selected: (data.updateReminder || "default") === r.id,
          label:
            r.id === "default"
              ? `Workspace schedule (${policyLabel(policy)})`
              : r.label,
        }))}
      />
      {data.updateDueAt && (
        <p
          className="m-0 pl-1 text-xs text-muted"
          data-initiative-update-due=""
        >
          {new Date(data.updateDueAt) < new Date()
            ? `Update overdue since ${fmtDate(data.updateDueAt)}`
            : `Next update due ${fmtDate(data.updateDueAt)}`}
        </p>
      )}
      <PropertiesGroup>Labels</PropertiesGroup>
      <div className="flex flex-wrap items-center gap-1.5 py-1">
        {(data.labels || []).map((l) => (
          <span
            key={l.id}
            className="inline-flex items-center gap-1.5 rounded-full border border-line px-2 py-0.5 text-xs"
          >
            <LabelDot color={l.color} />
            {l.name}
          </span>
        ))}
        {canEdit && (
          <PropertyChip
            label="Edit labels"
            icon={<Tag size={13} aria-hidden />}
            menu={(close) => (
              <LabelMenu
                value={(data.labels || []).map((l) => l.id)}
                close={close}
                onChange={(labelIds) => void change({ labelIds }, {})}
              />
            )}
          >
            {(data.labels || []).length ? "Edit" : "Add label"}
          </PropertyChip>
        )}
        {!canEdit && !(data.labels || []).length && (
          <span className="text-sm text-muted">No labels</span>
        )}
      </div>
      <PropertiesGroup>Parent initiatives</PropertiesGroup>
      <div
        className="flex flex-wrap items-center gap-1.5 py-1"
        data-initiative-parents=""
      >
        {(data.parents || []).map((p) => (
          <span
            key={p.id}
            className="inline-flex max-w-full items-center gap-1 rounded-md border border-line px-1.5 py-0.5 text-sm"
          >
            <button
              type="button"
              className="inline-flex min-w-0 items-center gap-1.5 hover:underline"
              onClick={() => onOpenInitiative(p.id)}
            >
              <InitiativeIcon icon={p.icon} color={p.color} />
              <span className="truncate">{p.name}</span>
            </button>
            {canEdit && (
              <button
                type="button"
                aria-label={`Remove parent ${p.name}`}
                className="grid place-items-center rounded text-muted hover:text-foreground"
                onClick={() =>
                  void change(
                    { removeParentId: p.id },
                    {
                      parents: (data.parents || []).filter(
                        (x) => x.id !== p.id,
                      ),
                    },
                  ).then(loadOthers)
                }
              >
                <X size={12} />
              </button>
            )}
          </span>
        ))}
        {canEdit ? (
          <PropertyChip
            label="Add parent initiative"
            icon={<CornerLeftUp size={13} aria-hidden />}
            menu={(close) => (
              <InitiativeMenu
                label="Parent initiative"
                options={parentOptions}
                close={close}
                onPick={(parentId) =>
                  parentId &&
                  void change(
                    { addParentId: parentId },
                    {
                      parents: [
                        ...(data.parents || []),
                        others.find((o) => o.id === parentId) as InitiativeRef,
                      ],
                    },
                  ).then(loadOthers)
                }
              />
            )}
          >
            {(data.parents || []).length ? "Add" : "Add parent"}
          </PropertyChip>
        ) : (
          !(data.parents || []).length && (
            <span className="text-sm text-muted">None</span>
          )
        )}
      </div>
      <PropertiesGroup>Slack channel</PropertiesGroup>
      <div className="flex flex-wrap items-center gap-1.5 py-1">
        <PropertyChip
          label={`Slack channel: ${data.slackChannel?.name || "none"}`}
          icon={<Hash size={13} aria-hidden />}
          menu={
            canEdit
              ? (close) => (
                  <SlackChannelMenu
                    value={data.slackChannel?.id}
                    close={close}
                    onPick={(ch) =>
                      void change(
                        {
                          slackChannelId: ch?.id || null,
                          slackChannelName: ch?.name || null,
                        },
                        { slackChannel: ch },
                      )
                    }
                  />
                )
              : undefined
          }
        >
          {data.slackChannel ? `#${data.slackChannel.name}` : "Link a channel"}
        </PropertyChip>
      </div>
      <PropertiesGroup>Health</PropertiesGroup>
      <div className="flex min-h-8 items-center gap-2 text-sm">
        <HealthBadge health={data.health} />
        {latest && (
          <span className="text-xs text-muted">
            {relTime(latest.createdAt)}
          </span>
        )}
      </div>
      <HealthRollup counts={data.projectHealth} />
      <PropertiesGroup>Progress</PropertiesGroup>
      <div className="flex min-h-8 items-center gap-2 text-sm">
        <Ring frac={progress} size={16} />
        <span>{Math.round(progress * 100)}%</span>
        <span className="text-muted">
          across {allProjects.length}{" "}
          {allProjects.length === 1 ? "project" : "projects"}
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
  );

  return (
    <DetailPage
      label={data.name}
      surface="initiative-detail"
      className={TOUCH_FIT}
      data-initiative-detail=""
    >
      <DetailHeader
        crumbs={crumbs}
        actions={
          <span className="contents pointer-coarse:[&_button]:min-h-11 pointer-coarse:[&_button]:min-w-11 shell-phone:[&_button]:min-h-11 shell-phone:[&_button]:min-w-11">
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
              onClick={() =>
                void navigator.clipboard?.writeText(
                  `${location.origin}/workspace/initiative/${data.identifier}`,
                )
              }
            >
              <Link2 size={15} />
            </IconBtn>
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
                      key: "copy-id",
                      label: `Copy ID ${data.identifier}`,
                      onSelect: () =>
                        void navigator.clipboard?.writeText(data.identifier),
                    },
                    ...(canEdit
                      ? [
                          {
                            key: "sub",
                            label: "New sub-initiative",
                            icon: <Plus size={14} />,
                            onSelect: () => setCreatingSub(true),
                          },
                        ]
                      : []),
                    ...(canArchive
                      ? [
                          {
                            key: "archive",
                            label: "Archive initiative",
                            danger: true,
                            icon: <Trash2 size={14} />,
                            onSelect: () => void archive(),
                          },
                        ]
                      : []),
                  ]}
                />
              )}
            </span>
          </span>
        }
      />
      <DetailColumns
        data-initiative-content=""
        mainLabel={tab === "updates" ? "Updates" : "Overview"}
        toolbar={
          <>
            <DetailTabs
              label="Initiative views"
              current={tab}
              onSelect={onTab}
              tabs={[
                {
                  id: "overview",
                  label: "Overview",
                  href: `/workspace/initiative/${data.identifier}`,
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
                  href: `/workspace/initiative/${data.identifier}/updates`,
                },
                {
                  id: "insights",
                  label: "Insights",
                  href: `/workspace/initiative/${data.identifier}/insights`,
                },
                ...pageViews.map((v) => ({
                  id: `view:${v.id}` as InitiativeTab,
                  label: v.name,
                  href: `/workspace/initiative/${data.identifier}/view/${v.id}`,
                })),
              ]}
            />
            <span className="contents pointer-coarse:[&_button]:min-h-11 pointer-coarse:[&_button]:min-w-11 shell-phone:[&_button]:min-h-11 shell-phone:[&_button]:min-w-11">
              <IconBtn
                label="New view of this initiative"
                title="New view"
                onClick={() => setAddingView(true)}
              >
                <Plus size={14} />
              </IconBtn>
              <IconBtn
                label={panelShown ? "Hide properties" : "Show properties"}
                title={panelShown ? "Hide properties" : "Show properties"}
                active={panelShown}
                onClick={togglePanel}
              >
                <PanelRight size={15} />
              </IconBtn>
            </span>
          </>
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
            {addingView ? (
              <AddPageView
                initiativeId={data.id}
                projects={allProjects}
                onCancel={() => setAddingView(false)}
                onSaved={(v) => {
                  setPageViews((cur) => [...cur, v]);
                  setAddingView(false);
                  onTab(`view:${v.id}`);
                }}
              />
            ) : tab === "insights" ? (
              <InsightsTab id={data.id} />
            ) : tab.startsWith("view:") ? (
              (() => {
                const v = pageViews.find((x) => `view:${x.id}` === tab);
                return v ? (
                  <PageViewTab
                    view={v}
                    projects={allProjects}
                    onOpenProject={onOpenProject}
                    onDelete={() =>
                      void deleteInitiativeView(v.id)
                        .then(() => {
                          setPageViews((cur) =>
                            cur.filter((x) => x.id !== v.id),
                          );
                          onTab("overview");
                        })
                        .catch((e: Error) => setActionError(e.message))
                    }
                  />
                ) : (
                  <p className="text-sm text-muted" role="status">
                    Loading view…
                  </p>
                );
              })()
            ) : tab === "updates" ? (
              <UpdatesFeed
                updates={updates}
                viewer={viewer}
                composing={composing}
                onCompose={setComposing}
                onPost={postUpdate}
                onDraft={draft}
                onEdit={editUpdate}
                onDelete={deleteUpdate}
                onReact={react}
                onReply={reply}
              />
            ) : (
              <>
                <div className="mb-4">
                  {canEdit ? (
                    <PropertyChip
                      label="Icon and colour"
                      icon={
                        <InitiativeIcon
                          icon={data.icon}
                          color={data.color}
                          size={22}
                        />
                      }
                      menu={(close) => (
                        <IconColorMenu
                          icon={data.icon}
                          color={data.color}
                          close={close}
                          onChange={(next) => void change(next, next)}
                        />
                      )}
                    >
                      <span className="sr-only">Icon</span>
                    </PropertyChip>
                  ) : (
                    <InitiativeIcon
                      icon={data.icon}
                      color={data.color}
                      size={22}
                    />
                  )}
                </div>
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
                <PropertyChips
                  data={data}
                  canEdit={canEdit}
                  members={members}
                  teams={teams}
                  change={change}
                />
                <Resources
                  items={data.resources || []}
                  onAdd={addResource}
                  onRemove={removeResource}
                />
                <LatestUpdate
                  latest={latest}
                  count={updates.length}
                  viewer={viewer}
                  composing={composing}
                  onCompose={setComposing}
                  onPost={postUpdate}
                  onDraft={draft}
                  onReact={react}
                  onReply={reply}
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
                  title="Sub-initiatives"
                  badge={
                    <span className="text-xs font-medium text-muted">
                      {(data.children || []).length}
                    </span>
                  }
                  actions={
                    canEdit && (
                      <>
                        <PropertyChip
                          label="Add an existing initiative as a sub-initiative"
                          menu={(close) => (
                            <InitiativeMenu
                              label="Add existing"
                              options={childOptions}
                              close={close}
                              onPick={(childId) =>
                                childId &&
                                void run(() =>
                                  updateInitiative(childId, {
                                    addParentId: data.id,
                                  }),
                                ).then(() => {
                                  void load();
                                  loadOthers();
                                })
                              }
                            />
                          )}
                        >
                          Add existing
                        </PropertyChip>
                        <IconBtn
                          label="New sub-initiative"
                          title="New sub-initiative"
                          onClick={() => setCreatingSub(true)}
                        >
                          <Plus size={14} />
                        </IconBtn>
                      </>
                    )
                  }
                >
                  <SubInitiatives
                    rows={data.children || []}
                    onOpen={onOpenInitiative}
                  />
                </DetailSection>
                <ProjectsSection
                  direct={direct}
                  all={allProjects}
                  canEdit={canEdit}
                  projects={projects}
                  onOpen={onOpenProject}
                  onChange={(ids) => void setProjects(ids)}
                />
                <DetailSection title="Progress">
                  <ProgressGraph series={data.progressHistory || []} />
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
        aside={!narrow && panelShown ? sidebar : undefined}
      />
      {narrow && overlayOpen && (
        <div className="fixed inset-0 z-50 bg-black/30" role="presentation">
          <div
            ref={overlayRef}
            role="dialog"
            aria-modal="true"
            aria-label="Properties"
            className="absolute inset-y-0 right-0 flex w-80 max-w-full flex-col overflow-y-auto bg-panel shadow-xl"
            data-initiative-properties-overlay=""
          >
            <div className="flex justify-end p-2">
              <IconBtn
                label="Close properties"
                title="Close properties"
                onClick={() => setOverlayOpen(false)}
              >
                <X size={15} />
              </IconBtn>
            </div>
            {sidebar}
          </div>
        </div>
      )}
      {creatingSub && (
        <CreateInitiativeDialog
          defaults={{ parentId: data.id, status: data.status }}
          initiatives={others}
          projects={projects}
          onClose={() => setCreatingSub(false)}
          onCreated={(_row, more) => {
            if (!more) setCreatingSub(false);
            void load();
            loadOthers();
          }}
        />
      )}
    </DetailPage>
  );
}

/* ---------------- chips under the summary ---------------- */

function PropertyChips({
  data,
  canEdit,
  members,
  teams,
  change,
}: {
  data: InitiativeDetailData;
  canEdit: boolean;
  members: { id: string; name: string }[];
  teams: { id: string; name: string; key: string }[];
  change: (
    input: Record<string, unknown>,
    patch: Partial<InitiativeDetailData>,
  ) => Promise<boolean>;
}) {
  const menu = (render: (close: () => void) => React.ReactNode) =>
    canEdit ? render : undefined;
  return (
    <div
      className="mb-4 flex flex-wrap items-center gap-1.5"
      data-initiative-property-chips=""
    >
      <span className="mr-1 text-sm text-muted">Properties</span>
      <PropertyChip
        label={`Status: ${initiativeStatusLabel(data.status)}`}
        icon={<InitiativeStatusIcon status={data.status} />}
        menu={menu((close) => (
          <StatusMenu
            value={data.status}
            close={close}
            onPick={(status) => void change({ status }, { status })}
          />
        ))}
      >
        {initiativeStatusLabel(data.status)}
      </PropertyChip>
      <PropertyChip
        label={`Priority: ${PRIORITIES[data.priority || 0]}`}
        icon={<PriorityIcon priority={data.priority || 0} />}
        menu={menu((close) => (
          <PriorityMenu
            value={data.priority || 0}
            close={close}
            onPick={(priority) => void change({ priority }, { priority })}
          />
        ))}
      >
        {PRIORITIES[data.priority || 0]}
      </PropertyChip>
      <PropertyChip
        label={`Owner: ${data.owner?.name || "none"}`}
        icon={
          <Avatar
            name={data.owner?.name}
            seed={data.owner?.avatarSeed}
            size={14}
          />
        }
        menu={menu((close) => (
          <MemberMenu
            label="Owner"
            value={data.owner?.id}
            close={close}
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
          />
        ))}
      >
        {data.owner?.name || "Owner"}
      </PropertyChip>
      <PropertyChip
        label={`Target date: ${data.targetDate || "none"}`}
        icon={<Calendar size={13} aria-hidden />}
        menu={menu((close) => (
          <DateMenu
            label="Target date"
            value={data.targetDate}
            close={close}
            onPick={(targetDate) => void change({ targetDate }, { targetDate })}
          />
        ))}
      >
        {data.targetDate ? fmtDate(data.targetDate) : "Target date"}
      </PropertyChip>
      <PropertyChip
        label={`Lead team: ${data.leadTeam?.name || "none"}`}
        icon={<Users size={13} aria-hidden />}
        menu={menu((close) => (
          <TeamMenu
            value={data.leadTeam?.id}
            close={close}
            onPick={(leadTeamId) =>
              void change(
                { leadTeamId },
                { leadTeam: teams.find((t) => t.id === leadTeamId) || null },
              )
            }
          />
        ))}
      >
        {data.leadTeam?.name || "Lead team"}
      </PropertyChip>
      {(data.labels || []).map((l) => (
        <span
          key={l.id}
          className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line px-2 text-xs"
        >
          <LabelDot color={l.color} />
          {l.name}
        </span>
      ))}
    </div>
  );
}

/* ---------------- resources ---------------- */

function Resources({
  items,
  onAdd,
  onRemove,
}: {
  items: { id: string; title: string; url: string }[];
  onAdd: (url: string, title: string) => Promise<boolean | undefined>;
  onRemove: (id: string) => void;
}) {
  const [adding, setAdding] = useState(false);
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  return (
    <div
      className="mb-6 flex flex-wrap items-center gap-1.5"
      data-initiative-resources=""
    >
      <span className="mr-1 text-sm text-muted">Resources</span>
      {items.map((r) => (
        <span
          key={r.id}
          className="inline-flex h-7 max-w-full items-center gap-1.5 rounded-md border border-line bg-panel px-2 text-xs"
        >
          <Link2 size={12} aria-hidden className="flex-none text-muted" />
          <a
            href={r.url}
            target="_blank"
            rel="noopener noreferrer"
            className="truncate text-foreground no-underline hover:underline"
          >
            {r.title}
          </a>
          <button
            type="button"
            aria-label={`Remove ${r.title}`}
            className="grid place-items-center rounded text-muted hover:text-foreground"
            onClick={() => onRemove(r.id)}
          >
            <X size={12} />
          </button>
        </span>
      ))}
      {adding ? (
        <form
          className="flex w-full flex-wrap gap-1.5"
          onSubmit={(e) => {
            e.preventDefault();
            if (!url.trim()) return;
            void onAdd(url.trim(), title.trim()).then((ok) => {
              if (!ok) return;
              setUrl("");
              setTitle("");
              setAdding(false);
            });
          }}
        >
          <input
            aria-label="Link"
            placeholder="https://…"
            type="url"
            required
            className="min-w-0 flex-1 rounded-md border border-line bg-bg px-2 py-1 text-sm"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <input
            aria-label="Title"
            placeholder="Title (optional)"
            className="min-w-0 flex-1 rounded-md border border-line bg-bg px-2 py-1 text-sm"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <DetailButton primary type="submit">
            Add
          </DetailButton>
          <DetailButton onClick={() => setAdding(false)}>Cancel</DetailButton>
        </form>
      ) : (
        <button
          type="button"
          className="inline-flex h-7 items-center gap-1 rounded-md px-2 text-xs text-muted hover:bg-subtle hover:text-foreground"
          onClick={() => setAdding(true)}
        >
          <Plus size={12} aria-hidden /> Add document or link…
        </button>
      )}
    </div>
  );
}

/* ---------------- health roll-up ---------------- */

function HealthRollup({
  counts,
}: {
  counts?: InitiativeDetailData["projectHealth"];
}) {
  if (!counts) return null;
  const parts = [
    {
      key: "onTrack",
      label: "On track",
      n: counts.onTrack,
      cls: "bg-green-600",
    },
    { key: "atRisk", label: "At risk", n: counts.atRisk, cls: "bg-amber-500" },
    {
      key: "offTrack",
      label: "Off track",
      n: counts.offTrack,
      cls: "bg-red-600",
    },
    { key: "noUpdate", label: "No update", n: counts.noUpdate, cls: "bg-line" },
  ];
  const total = parts.reduce((n, p) => n + p.n, 0);
  return (
    <div className="grid gap-1.5 py-1" data-initiative-health-rollup="">
      <span className="text-xs text-muted">
        Active projects{total ? ` (${total})` : ""}
      </span>
      {total > 0 && (
        <div
          className="flex h-1.5 gap-0.5 overflow-hidden rounded-full"
          aria-hidden
        >
          {parts
            .filter((p) => p.n)
            .map((p) => (
              <span key={p.key} className={p.cls} style={{ flex: p.n }} />
            ))}
        </div>
      )}
      <ul className="m-0 grid list-none gap-0.5 p-0 text-xs">
        {parts.map((p) => (
          <li key={p.key} className="flex items-center gap-2">
            <span className={`size-2 rounded-full ${p.cls}`} aria-hidden />
            <span className="flex-1 text-muted">{p.label}</span>
            <span className="tabular-nums">{p.n}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ---------------- sub-initiatives & projects ---------------- */

function SubInitiatives({
  rows,
  onOpen,
}: {
  rows: NonNullable<InitiativeDetailData["children"]>;
  onOpen: (id: string) => void;
}) {
  if (!rows.length)
    return (
      <p className="m-0 py-2 text-sm text-muted">
        No sub-initiatives. Break a large initiative into smaller ones that roll
        up here.
      </p>
    );
  return (
    <ul className="m-0 grid list-none p-0 text-sm" data-initiative-children="">
      {rows.map((c) => (
        <li key={c.id}>
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none"
            onClick={() => onOpen(c.id)}
          >
            <InitiativeIcon icon={c.icon} color={c.color} />
            <span className="flex-none text-xs text-muted">{c.identifier}</span>
            <span className="min-w-0 flex-1 truncate font-medium">
              {c.name}
            </span>
            <span className="hidden md:inline">
              <HealthBadge health={c.health} />
            </span>
            <InitiativeStatusIcon status={c.status} />
            <span className="hidden text-muted md:inline">
              {initiativeStatusLabel(c.status)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

function ProjectsSection({
  direct,
  all,
  canEdit,
  projects,
  onOpen,
  onChange,
}: {
  direct: InitiativeProject[];
  all: InitiativeProject[];
  canEdit: boolean;
  projects: Project[];
  onOpen: (id: string) => void;
  onChange: (ids: string[]) => void;
}) {
  const [includeSubs, setIncludeSubs] = useState(true);
  const rows = includeSubs ? all : direct;
  const directIds = new Set(direct.map((p) => p.id));
  return (
    <DetailSection
      title="Projects"
      badge={
        <span className="text-xs font-medium text-muted">{rows.length}</span>
      }
      actions={
        <>
          {all.length !== direct.length && (
            <label className="mr-1 inline-flex items-center gap-1.5 text-xs font-normal text-muted">
              <input
                type="checkbox"
                checked={includeSubs}
                onChange={(e) => setIncludeSubs(e.target.checked)}
              />
              Include sub-initiatives
            </label>
          )}
          {canEdit && (
            <ProjectPicker
              all={projects}
              linked={direct.map((p) => p.id)}
              onChange={onChange}
            />
          )}
        </>
      }
    >
      <ProjectTable
        rows={rows}
        removable={(pid) => canEdit && directIds.has(pid)}
        onOpen={onOpen}
        onRemove={(pid) =>
          onChange(direct.map((p) => p.id).filter((x) => x !== pid))
        }
      />
    </DetailSection>
  );
}

function ProjectTable({
  rows,
  removable,
  onOpen,
  onRemove,
}: {
  rows: InitiativeProject[];
  removable: (id: string) => boolean;
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
            {removable(p.id) && (
              <span className="ml-auto opacity-0 group-focus-within:opacity-100 group-hover:opacity-100">
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
            <Ring frac={(p.progress || 0) / 100} size={14} />
            {Math.round(p.progress || 0)}%
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
            ref={findRef}
            className="rounded-md border border-line bg-bg px-2 py-1.5 text-sm placeholder:text-muted"
            placeholder="Find a project…"
            aria-label="Find a project"
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
                {on && <span aria-hidden>✓</span>}
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

/** `/workspace/initiative/<id or I-n>[/updates]`: the page plus its tab, kept in the URL. */
export function parseInitiativePath(path: string): {
  id: string;
  tab: InitiativeTab;
} {
  const rest = path.split("/").filter(Boolean);
  const at = rest.indexOf("initiative");
  const sub = rest[at + 2];
  return {
    id: decodeURIComponent(rest[at + 1] || ""),
    tab:
      sub === "updates" || sub === "insights"
        ? sub
        : sub === "view" && rest[at + 3]
          ? `view:${rest[at + 3]}`
          : "overview",
  };
}

export function InitiativeRoute(
  props: Omit<
    React.ComponentProps<typeof InitiativeDetailPage>,
    "id" | "tab" | "onTab" | "onOpenInitiative"
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
  const go = (id: string, tab: InitiativeTab) => {
    const suffix =
      tab === "overview"
        ? ""
        : tab.startsWith("view:")
          ? `/view/${tab.slice(5)}`
          : `/${tab}`;
    history.pushState(
      {},
      "",
      `/workspace/initiative/${encodeURIComponent(id)}${suffix}`,
    );
    setRoute({ id, tab });
  };
  return (
    <InitiativeDetailPage
      key={route.id}
      {...props}
      id={route.id}
      tab={route.tab}
      onTab={(tab) => go(route.id, tab)}
      onOpenInitiative={(id) => go(id, "overview")}
    />
  );
}

/* ---------------- updates ---------------- */

type DraftResult = { body: string; health: string; source: string };

function UpdateComposer({
  initial,
  initialHealth,
  submitLabel,
  onSubmit,
  onCancel,
  onDraft,
}: {
  initial?: string;
  initialHealth?: string | null;
  submitLabel: string;
  onSubmit: (body: string, health: string) => Promise<boolean>;
  onCancel: () => void;
  onDraft?: () => Promise<DraftResult | undefined>;
}) {
  const [body, setBody] = useState(initial || "");
  const [health, setHealth] = useState<string>(initialHealth || "on track");
  const [busy, setBusy] = useState(false);
  const [drafting, setDrafting] = useState(false);
  const [note, setNote] = useState("");
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
      <div className="flex flex-wrap items-center gap-1.5">
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
        <span className="flex-1" />
        {onDraft && (
          <DetailButton
            disabled={drafting}
            onClick={() => {
              setDrafting(true);
              void onDraft().then((d) => {
                setDrafting(false);
                if (!d) return;
                setBody(d.body);
                setHealth(d.health);
                setNote(
                  d.source === "model"
                    ? "Drafted by your workspace's model from the latest changes. Review it before posting."
                    : "Drafted from what changed since the last update. No model is configured, so add risks and next steps yourself.",
                );
              });
            }}
          >
            <Sparkles size={14} aria-hidden />
            {drafting ? "Drafting…" : "Write with agent"}
          </DetailButton>
        )}
      </div>
      {note && (
        <p className="m-0 text-xs text-muted" role="status">
          {note}
        </p>
      )}
      <textarea
        ref={bodyRef}
        className="min-h-24 w-full resize-y rounded-md border border-line bg-bg p-2 text-sm text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent"
        aria-label="Update"
        placeholder="Write an update on progress, risks and what is next…"
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

function Reactions({
  post,
  onReact,
}: {
  post: InitiativeUpdatePost;
  onReact: (post: InitiativeUpdatePost, emoji: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="flex flex-wrap items-center gap-1.5"
      data-initiative-reactions=""
    >
      {(post.reactions || []).map((r) => (
        <button
          key={r.emoji}
          type="button"
          aria-pressed={r.viewerReacted}
          title={r.users.map((u) => u.name).join(", ")}
          aria-label={`${r.emoji} ${r.count}, ${r.viewerReacted ? "remove your reaction" : "react"}`}
          className="inline-flex h-6 items-center gap-1 rounded-full border border-line px-2 text-xs hover:bg-subtle aria-pressed:border-accent aria-pressed:bg-accent-soft"
          onClick={() => onReact(post, r.emoji)}
        >
          <span aria-hidden>{r.emoji}</span>
          <span className="tabular-nums">{r.count}</span>
        </button>
      ))}
      <span className="relative">
        <IconBtn
          label="Add reaction"
          title="Add reaction"
          onClick={() => setOpen((v) => !v)}
        >
          <SmilePlus size={14} />
        </IconBtn>
        {open && (
          <LinPopover
            onClose={() => setOpen(false)}
            className="absolute bottom-full left-0 z-50 mb-1 flex gap-0.5 rounded-lg border border-line bg-panel p-1 shadow-lg"
            role="dialog"
            aria-label="Pick a reaction"
          >
            {REACTIONS.map((e) => (
              <button
                key={e}
                type="button"
                aria-label={`React ${e}`}
                className="grid size-8 place-items-center rounded-md text-base hover:bg-subtle"
                onClick={() => {
                  setOpen(false);
                  onReact(post, e);
                }}
              >
                {e}
              </button>
            ))}
          </LinPopover>
        )}
      </span>
    </div>
  );
}

function Replies({
  post,
  onReply,
}: {
  post: InitiativeUpdatePost;
  onReply: (post: InitiativeUpdatePost, body: string) => Promise<boolean>;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const replies = post.comments?.nodes || [];
  return (
    <div
      className="grid gap-2 border-t border-line pt-3"
      data-initiative-replies=""
    >
      {replies.map((c) => (
        <div key={c.id} className="flex gap-2 text-sm">
          <Avatar name={c.user?.name} seed={c.user?.avatarSeed} size={18} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-xs">
              <span className="font-medium">{c.user?.name || "Someone"}</span>
              <time className="text-muted" dateTime={c.createdAt}>
                {relTime(c.createdAt)}
              </time>
            </div>
            <Markdown text={c.body} density="block" />
          </div>
        </div>
      ))}
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!text.trim() || busy) return;
          setBusy(true);
          void onReply(post, text.trim()).then((ok) => {
            setBusy(false);
            if (ok) setText("");
          });
        }}
      >
        <input
          aria-label="Reply to this update"
          placeholder="Leave a reply…"
          className="min-w-0 flex-1 rounded-md border border-line bg-bg px-2 py-1 text-sm placeholder:text-muted"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <DetailButton type="submit" disabled={!text.trim() || busy}>
          Reply
        </DetailButton>
      </form>
    </div>
  );
}

function UpdateCard({
  post,
  viewer,
  onEdit,
  onDelete,
  onReact,
  onReply,
}: {
  post: InitiativeUpdatePost;
  viewer?: Viewer;
  onEdit?: (
    post: InitiativeUpdatePost,
    body: string,
    health: string,
  ) => Promise<boolean>;
  onDelete?: (post: InitiativeUpdatePost) => void;
  onReact: (post: InitiativeUpdatePost, emoji: string) => void;
  onReply: (post: InitiativeUpdatePost, body: string) => Promise<boolean>;
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
      <header className="flex flex-wrap items-center gap-2 text-sm">
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
      <Reactions post={post} onReact={onReact} />
      <Replies post={post} onReply={onReply} />
    </article>
  );
}

function LatestUpdate({
  latest,
  count,
  viewer,
  composing,
  onCompose,
  onPost,
  onDraft,
  onReact,
  onReply,
  onSeeAll,
}: {
  latest?: InitiativeUpdatePost;
  count: number;
  viewer: Viewer;
  composing: boolean;
  onCompose: (open: boolean) => void;
  onPost: (body: string, health: string) => Promise<boolean>;
  onDraft: () => Promise<DraftResult | undefined>;
  onReact: (post: InitiativeUpdatePost, emoji: string) => void;
  onReply: (post: InitiativeUpdatePost, body: string) => Promise<boolean>;
  onSeeAll: () => void;
}) {
  if (composing)
    return (
      <UpdateComposer
        submitLabel="Post update"
        onSubmit={onPost}
        onDraft={onDraft}
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
      <UpdateCard
        post={latest}
        viewer={viewer}
        onReact={onReact}
        onReply={onReply}
      />
    </section>
  );
}

function UpdatesFeed({
  updates,
  viewer,
  composing,
  onCompose,
  onPost,
  onDraft,
  onEdit,
  onDelete,
  onReact,
  onReply,
}: {
  updates: InitiativeUpdatePost[];
  viewer: Viewer;
  composing: boolean;
  onCompose: (open: boolean) => void;
  onPost: (body: string, health: string) => Promise<boolean>;
  onDraft: () => Promise<DraftResult | undefined>;
  onEdit: (
    post: InitiativeUpdatePost,
    body: string,
    health: string,
  ) => Promise<boolean>;
  onDelete: (post: InitiativeUpdatePost) => void;
  onReact: (post: InitiativeUpdatePost, emoji: string) => void;
  onReply: (post: InitiativeUpdatePost, body: string) => Promise<boolean>;
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
          onDraft={onDraft}
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
          onReact={onReact}
          onReply={onReply}
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

/** Pick the Slack channel whose messages feed "Write with agent". Lists what the
 * viewer's connected Slack account can read. */
function SlackChannelMenu({
  value,
  onPick,
  close,
}: {
  value?: string | null;
  onPick: (channel: { id: string; name: string } | null) => void;
  close: () => void;
}) {
  const [channels, setChannels] = useState<SlackChannel[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    fetchSlackChannels()
      .then(setChannels)
      .catch((e: Error) => setError(e.message));
  }, []);
  if (error)
    return (
      <p className="m-0 p-2 text-sm text-danger" role="alert">
        {error}
      </p>
    );
  if (!channels)
    return (
      <p className="m-0 p-2 text-sm text-muted" role="status">
        Loading channels…
      </p>
    );
  if (!channels.length)
    return (
      <p className="m-0 p-2 text-sm text-muted">
        Connect Slack under Settings → Connections to link a channel. Drafts
        then read what was said there since the last update.
      </p>
    );
  return (
    <PickerMenu
      label="Slack channel"
      onClose={close}
      onPick={(id) =>
        onPick(id ? channels.find((c) => c.id === id) || null : null)
      }
      options={[
        { id: null, keywords: "none", selected: !value, label: "No channel" },
        ...channels.map((c) => ({
          id: c.id,
          keywords: c.name,
          selected: c.id === value,
          label: `${c.isPrivate ? "🔒 " : "#"}${c.name}`,
        })),
      ]}
    />
  );
}
