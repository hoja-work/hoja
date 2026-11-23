import * as React from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpToLine,
  CircleDot,
  Pencil,
  Plus,
  Trash2,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "../../components/ui/menu";
import { Select } from "../../components/ui/select";
import {
  Badge,
  ConfirmAction,
  EmptyState,
  Field,
  FormDialog,
  Loadable,
  Notice,
  ResourceRow,
  SaveStatusLine,
  SettingsCard,
  SettingsPage,
  SettingsSection,
  gql,
  useResource,
  useSetting,
} from "../ui";
import { ColorDot, ColorPicker } from "./project-statuses/ColorPicker";

/* Project statuses (HOJA-489, HOJA-683). The ordered set a project's state comes from:
 * the first status is the default for new projects and the server refuses a transition
 * to any name that is not in the list. Rows are read-only summaries; editing happens in a
 * dialog, so reordering can never discard a half-typed edit in another row. */

type Status = {
  id: string;
  name: string;
  category: string;
  color: string | null;
  position: number;
};

const CATEGORIES = [
  {
    value: "backlog",
    label: "Backlog",
    description: "Not started and not yet planned.",
  },
  {
    value: "started",
    label: "In progress",
    description: "Planned, active or paused work.",
  },
  {
    value: "completed",
    label: "Completed",
    description: "Finished. Counts as done in progress.",
  },
  {
    value: "canceled",
    label: "Canceled",
    description: "Stopped without finishing.",
  },
];
const categoryLabel = (c: string) =>
  CATEGORIES.find((x) => x.value === c)?.label || c;

const STATUS_FIELDS = "id name category color position";

async function loadStatuses(): Promise<{
  statuses: Status[];
  usage: Record<string, number> | null;
}> {
  const d = await gql<{ projectStatuses: Status[] }>(
    `{projectStatuses{${STATUS_FIELDS}}}`,
  );
  // How many projects sit in each status. Best effort: the list still works without it.
  let usage: Record<string, number> | null = {};
  try {
    let after: string | null = null;
    for (let page = 0; page < 8; page++) {
      const p: {
        projects: {
          nodes: { state?: string | null }[];
          pageInfo: { hasNextPage: boolean; endCursor: string | null };
        };
      } = await gql(
        `query($after:String){projects(first:250,after:$after){nodes{state} pageInfo{hasNextPage endCursor}}}`,
        { after },
      );
      for (const n of p.projects.nodes) {
        const key = (n.state || "").trim().toLowerCase();
        if (key) usage[key] = (usage[key] || 0) + 1;
      }
      if (!p.projects.pageInfo.hasNextPage) break;
      after = p.projects.pageInfo.endCursor;
    }
  } catch {
    usage = null;
  }
  return { statuses: d.projectStatuses, usage };
}

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n} ${n === 1 ? one : many}`;

export default function ProjectStatusesSettings() {
  const resource = useResource(loadStatuses);
  const [dialog, setDialog] = React.useState<
    { mode: "create" } | { mode: "edit"; status: Status } | null
  >(null);
  const [deleting, setDeleting] = React.useState<Status | null>(null);

  const statuses = resource.data?.statuses;
  const usage = resource.data?.usage ?? null;
  const serverOrder = React.useMemo(
    () => (statuses || []).map((s) => s.id).join(","),
    [statuses],
  );
  // Order is one instant setting: moves apply optimistically and roll back on failure.
  const order = useSetting<string>({
    value: serverOrder,
    save: async (next) => {
      const d = await gql<{ projectStatusReorder: boolean }>(
        "mutation($ids:[String!]!){projectStatusReorder(ids:$ids)}",
        { ids: next.split(",") },
      );
      if (!d.projectStatusReorder)
        throw new Error("The new order was not saved. Try again.");
      resource.setData((cur) => {
        if (!cur) return cur;
        const byId = new Map(cur.statuses.map((s) => [s.id, s]));
        return {
          ...cur,
          statuses: next.split(",").map((id, i) => ({
            ...(byId.get(id) as Status),
            position: i,
          })),
        };
      });
    },
  });

  const ordered = React.useMemo(() => {
    if (!statuses) return [];
    const byId = new Map(statuses.map((s) => [s.id, s]));
    const ids = order.value ? order.value.split(",") : [];
    const rows = ids.map((id) => byId.get(id)).filter(Boolean) as Status[];
    return rows.length === statuses.length ? rows : statuses;
  }, [statuses, order.value]);

  const move = (index: number, to: number) => {
    const ids = ordered.map((s) => s.id);
    const [id] = ids.splice(index, 1);
    ids.splice(to, 0, id);
    void order.set(ids.join(","));
  };

  const countFor = (s: Status) =>
    usage ? usage[s.name.trim().toLowerCase()] || 0 : null;

  return (
    <SettingsPage
      title="Project statuses"
      description="The stages a project moves through. New projects start in the first status, and a project can only move to a status in this list."
      actions={
        <Button
          variant="primary"
          onClick={() => setDialog({ mode: "create" })}
          disabled={!statuses}
        >
          <Plus className="size-3.5" aria-hidden="true" />
          New status
        </Button>
      }
    >
      <SettingsSection
        title="Statuses"
        description="In order. Use the ⋯ menu on a status to move it, rename it or delete it."
        bare
      >
        <Loadable
          resource={resource}
          rows={6}
          isEmpty={(d) => d.statuses.length === 0}
          empty={
            <EmptyState
              icon={<CircleDot />}
              title="No project statuses yet"
              description="Add the first status; new projects will start in it."
              action={
                <Button
                  variant="primary"
                  onClick={() => setDialog({ mode: "create" })}
                >
                  New status
                </Button>
              }
            />
          }
        >
          {() => (
            <>
              <SettingsCard>
                <ol
                  aria-label="Project statuses, in order"
                  className="m-0 list-none divide-y divide-line p-0"
                >
                  {ordered.map((s, i) => {
                    const count = countFor(s);
                    return (
                      <li key={s.id} data-project-status-row="">
                        <ResourceRow
                          leading={<ColorDot color={s.color} />}
                          title={s.name}
                          badges={
                            i === 0 ? (
                              <Badge tone="strong">Default</Badge>
                            ) : undefined
                          }
                          meta={[
                            categoryLabel(s.category),
                            count === null
                              ? null
                              : count
                                ? plural(count, "project")
                                : "No projects",
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                          menuLabel={`Actions for status ${s.name}`}
                          actions={
                            <>
                              <DropdownMenuItem
                                onSelect={() =>
                                  setDialog({ mode: "edit", status: s })
                                }
                              >
                                <Pencil aria-hidden="true" />
                                Edit…
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                disabled={i === 0 || order.saving}
                                onSelect={() => move(i, 0)}
                              >
                                <ArrowUpToLine aria-hidden="true" />
                                Make default
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                disabled={i === 0 || order.saving}
                                onSelect={() => move(i, i - 1)}
                              >
                                <ArrowUp aria-hidden="true" />
                                Move up
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                disabled={
                                  i === ordered.length - 1 || order.saving
                                }
                                onSelect={() => move(i, i + 1)}
                              >
                                <ArrowDown aria-hidden="true" />
                                Move down
                              </DropdownMenuItem>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem
                                destructive
                                disabled={ordered.length <= 1}
                                onSelect={() => setDeleting(s)}
                              >
                                <Trash2 aria-hidden="true" />
                                Delete…
                              </DropdownMenuItem>
                            </>
                          }
                        />
                      </li>
                    );
                  })}
                </ol>
              </SettingsCard>
              <SaveStatusLine status={order.status} />
              {ordered.length === 1 && (
                <Notice>
                  A workspace always keeps at least one status, so the last one
                  cannot be deleted.
                </Notice>
              )}
            </>
          )}
        </Loadable>
      </SettingsSection>

      <Notice title="Where statuses apply">
        The server checks every project change against this list, through the
        app and the API. The status picker on a project still offers the six
        built-in names, so a status you add here is set through the API until
        the picker reads this list.
      </Notice>

      <StatusDialog
        state={dialog}
        onClose={() => setDialog(null)}
        count={dialog?.mode === "edit" ? countFor(dialog.status) : null}
        onSaved={(saved, created) =>
          resource.setData((cur) =>
            cur
              ? {
                  ...cur,
                  statuses: created
                    ? [...cur.statuses, saved]
                    : cur.statuses.map((s) => (s.id === saved.id ? saved : s)),
                }
              : cur,
          )
        }
      />

      <ConfirmAction
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete “${deleting?.name ?? ""}”?`}
        description={
          deleting ? <DeleteConsequence count={countFor(deleting)} /> : null
        }
        confirmLabel="Delete status"
        onConfirm={async () => {
          if (!deleting) return;
          const d = await gql<{ projectStatusDelete: boolean }>(
            "mutation($id:String!){projectStatusDelete(id:$id)}",
            { id: deleting.id },
          );
          if (!d.projectStatusDelete)
            throw new Error(
              "This status no longer exists. Reload the page to see the current list.",
            );
          const gone = deleting.id;
          resource.setData((cur) =>
            cur
              ? { ...cur, statuses: cur.statuses.filter((s) => s.id !== gone) }
              : cur,
          );
        }}
      />
    </SettingsPage>
  );
}

function DeleteConsequence({ count }: { count: number | null }) {
  const usage =
    count === null
      ? "Projects in this status keep it as their state"
      : count === 0
        ? "No project is in this status right now"
        : `${plural(count, "project is", "projects are")} in this status. They keep it as their state`;
  return (
    <>
      {usage}
      {count === 0
        ? ". "
        : ", but nothing can move into it again and their next change must pick another status. "}
      New projects and templates can no longer use it. This cannot be undone.
    </>
  );
}

function StatusDialog({
  state,
  onClose,
  onSaved,
  count,
}: {
  state: { mode: "create" } | { mode: "edit"; status: Status } | null;
  onClose: () => void;
  onSaved: (s: Status, created: boolean) => void;
  count: number | null;
}) {
  const editing = state?.mode === "edit" ? state.status : null;
  const [name, setName] = React.useState("");
  const [category, setCategory] = React.useState("started");
  const [color, setColor] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (!state) return;
    setName(editing?.name ?? "");
    setCategory(editing?.category ?? "started");
    setColor(editing?.color ?? null);
  }, [state, editing]);
  const renamed =
    !!editing && name.trim() !== "" && name.trim() !== editing.name;

  return (
    <FormDialog
      open={!!state}
      onOpenChange={(o) => !o && onClose()}
      title={editing ? `Edit “${editing.name}”` : "New project status"}
      description={
        editing
          ? undefined
          : "It is added at the end of the list. Move it up from the ⋯ menu."
      }
      submitLabel={editing ? "Save status" : "Add status"}
      canSubmit={!!name.trim()}
      onSubmit={async () => {
        const input = { name: name.trim(), category, color };
        if (editing) {
          const d = await gql<{
            projectStatusUpdate: { success: boolean; projectStatus: Status };
          }>(
            `mutation($id:String!,$input:ProjectStatusInput!){projectStatusUpdate(id:$id,input:$input){success projectStatus{${STATUS_FIELDS}}}}`,
            { id: editing.id, input },
          );
          if (!d.projectStatusUpdate?.success)
            throw new Error("The status was not saved. Try again.");
          onSaved(d.projectStatusUpdate.projectStatus, false);
        } else {
          const d = await gql<{
            projectStatusCreate: { success: boolean; projectStatus: Status };
          }>(
            `mutation($input:ProjectStatusInput!){projectStatusCreate(input:$input){success projectStatus{${STATUS_FIELDS}}}}`,
            { input },
          );
          if (!d.projectStatusCreate?.success)
            throw new Error("The status was not added. Try again.");
          onSaved(d.projectStatusCreate.projectStatus, true);
        }
      }}
    >
      <Field
        label="Name"
        htmlFor="project-status-name"
        help={
          renamed && count
            ? `${plural(count, "project")} in “${editing?.name}” will keep the old name until they move to another status.`
            : undefined
        }
      >
        <Input
          id="project-status-name"
          autoFocus
          maxLength={40}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <Field
        label="Category"
        htmlFor="project-status-category"
        help="The kind of stage this is, the same four kinds task states use."
      >
        <Select
          id="project-status-category"
          value={category}
          onValueChange={setCategory}
          options={CATEGORIES}
          className="sm:w-full"
        />
      </Field>
      <Field label="Colour" htmlFor="project-status-color">
        <ColorPicker
          id="project-status-color"
          label="Status colour"
          value={color}
          onChange={setColor}
        />
      </Field>
    </FormDialog>
  );
}
