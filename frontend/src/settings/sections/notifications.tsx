import { useId } from "react";
import { Switch } from "../../components/ui/switch";
import {
  Loadable,
  SettingRow,
  SettingsPage,
  SettingsSection,
  gql,
  useResource,
  useSetting,
} from "../ui";

/* Every kind notifications.KINDS accepts, grouped by what the notification is about. Inside a
 * group, things addressed to you come first. ``issueCreated`` is not its own row: it only exists
 * on inbox rows written before assignments had their own kind, so "Assigned to me" writes both. */
type Kind =
  | "issueAssigned"
  | "issueMention"
  | "issueDecision"
  | "issueComment"
  | "issueStatusChanged"
  | "issueUpdated"
  | "issueCreated"
  | "projectLeadAssigned"
  | "projectMemberAdded"
  | "projectMention"
  | "projectUpdatePosted"
  | "projectUpdateReply"
  | "projectUpdated"
  | "initiativeOwnerAssigned"
  | "initiativeUpdatePosted"
  | "initiativeUpdated"
  | "cycleStarted"
  | "cycleCompleted"
  | "viewIssueAdded"
  | "viewIssueCompleted";

type Row = {
  kind: Exclude<Kind, "issueCreated">;
  label: string;
  hint: string;
  /** Kinds saved together with this one. */
  also?: Kind[];
};

const GROUPS: { title: string; description: string; rows: Row[] }[] = [
  {
    title: "Tasks",
    description:
      "You follow tasks you create, are assigned, comment on or are mentioned in.",
    rows: [
      {
        kind: "issueAssigned",
        label: "Assigned to me",
        hint: "Someone makes you the assignee of a task.",
        also: ["issueCreated"],
      },
      {
        kind: "issueMention",
        label: "Mentions",
        hint: "Someone @mentions you in a task comment.",
      },
      {
        kind: "issueDecision",
        label: "Decision requests",
        hint: "A task you own or follow asks for a decision.",
      },
      {
        kind: "issueComment",
        label: "New comments",
        hint: "Someone comments on a task you follow.",
      },
      {
        kind: "issueStatusChanged",
        label: "Status changes",
        hint: "A task you follow moves to another status.",
      },
      {
        kind: "issueUpdated",
        label: "Priority and due date changes",
        hint: "A task you follow changes priority or due date.",
      },
    ],
  },
  {
    title: "Projects",
    description: "You follow projects you lead, create or post updates to.",
    rows: [
      {
        kind: "projectLeadAssigned",
        label: "Made project lead",
        hint: "Someone makes you the lead of a project.",
      },
      {
        kind: "projectMemberAdded",
        label: "Added to a project",
        hint: "Someone adds you as a member of a project.",
      },
      {
        kind: "projectMention",
        label: "Mentions",
        hint: "Someone @mentions you in a project update or a reply.",
      },
      {
        kind: "projectUpdatePosted",
        label: "Project updates",
        hint: "A project you follow posts an update.",
      },
      {
        kind: "projectUpdateReply",
        label: "Replies",
        hint: "Someone replies to an update you posted or replied to.",
      },
      {
        kind: "projectUpdated",
        label: "Status, target date and lead changes",
        hint: "A project you follow changes status, target date or lead.",
      },
    ],
  },
  {
    title: "Initiatives",
    description: "You follow initiatives you own or create.",
    rows: [
      {
        kind: "initiativeOwnerAssigned",
        label: "Made initiative owner",
        hint: "Someone makes you the owner of an initiative.",
      },
      {
        kind: "initiativeUpdatePosted",
        label: "Initiative updates",
        hint: "An initiative you follow posts an update.",
      },
      {
        kind: "initiativeUpdated",
        label: "Status, target date and owner changes",
        hint: "An initiative you follow changes status, target date or owner.",
      },
    ],
  },
  {
    title: "Cycles",
    description: "You follow cycles you subscribe to from a team's cycle page.",
    rows: [
      {
        kind: "cycleStarted",
        label: "Cycle started",
        hint: "A cycle you subscribed to starts.",
      },
      {
        kind: "cycleCompleted",
        label: "Cycle ended",
        hint: "A cycle you subscribed to ends, with a summary of what got done.",
      },
    ],
  },
  {
    title: "Views",
    description: "You follow views you subscribe to from the view's menu.",
    rows: [
      {
        kind: "viewIssueAdded",
        label: "Tasks added to a view",
        hint: "A task starts matching a view you follow.",
      },
      {
        kind: "viewIssueCompleted",
        label: "Tasks finished in a view",
        hint: "A task in a view you follow is completed or canceled.",
      },
    ],
  },
];

const ALL_KINDS: Kind[] = [
  ...GROUPS.flatMap((g) => g.rows.flatMap((r) => [r.kind, ...(r.also || [])])),
];
const FIELDS = ALL_KINDS.join(" ");
type Prefs = Record<Kind, boolean>;

function readPrefs(d: Record<string, unknown> | null | undefined): Prefs {
  return Object.fromEntries(
    ALL_KINDS.map((k) => [k, typeof d?.[k] === "boolean" ? d[k] : true]),
  ) as Prefs;
}

async function setKinds(kinds: Kind[], enabled: boolean): Promise<void> {
  // One request: a row that owns a legacy kind saves both together.
  const fields = kinds
    .map(
      (k, i) =>
        `k${i}:notificationPreferenceSet(kind:"${k}",enabled:$enabled){success ${FIELDS}}`,
    )
    .join(" ");
  const d = await gql<
    Record<string, ({ success: boolean } & Record<string, unknown>) | null>
  >(`mutation($enabled:Boolean!){${fields}}`, { enabled });
  if (kinds.some((_, i) => !d[`k${i}`]?.success))
    throw new Error("Your notification setting wasn't saved. Try again.");
}

function KindRow({
  row,
  enabled,
  onSaved,
}: {
  row: Row;
  enabled: boolean;
  onSaved: (kinds: Kind[], enabled: boolean) => void;
}) {
  const id = useId();
  const setting = useSetting({
    value: enabled,
    save: async (next: boolean) => {
      const kinds = [row.kind, ...(row.also || [])];
      await setKinds(kinds, next);
      onSaved(kinds, next);
    },
  });
  return (
    <SettingRow
      label={row.label}
      htmlFor={id}
      description={row.hint}
      status={setting.status}
    >
      <Switch
        id={id}
        data-notification-kind={row.kind}
        checked={setting.value}
        onCheckedChange={(v) => void setting.set(v)}
      />
    </SettingRow>
  );
}

export default function NotificationSettings() {
  const prefs = useResource(async () => {
    const d = await gql<{
      myNotificationPreferences: Record<string, unknown> | null;
    }>(`{myNotificationPreferences{success ${FIELDS}}}`);
    return readPrefs(d.myNotificationPreferences);
  });

  return (
    <SettingsPage
      title="Notifications"
      description="Choose what reaches your Hoja inbox. Turning something off also hides those notifications already in your inbox; turn it back on to see them again. Email and push delivery aren't available yet."
    >
      <Loadable resource={prefs} rows={6}>
        {(data) =>
          GROUPS.map((g) => (
            <SettingsSection
              key={g.title}
              title={g.title}
              description={g.description}
            >
              {g.rows.map((row) => (
                <KindRow
                  key={row.kind}
                  row={row}
                  enabled={data[row.kind]}
                  onSaved={(kinds, on) =>
                    // Merge only this row's kinds: another row may still be saving.
                    prefs.setData((p) =>
                      p
                        ? {
                            ...p,
                            ...Object.fromEntries(kinds.map((k) => [k, on])),
                          }
                        : p,
                    )
                  }
                />
              ))}
            </SettingsSection>
          ))
        }
      </Loadable>
    </SettingsPage>
  );
}
