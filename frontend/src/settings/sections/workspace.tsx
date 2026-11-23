import { useEffect, useId } from "react";
import { ArrowRight } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { useSettings, type SettingsWorkspace } from "../context";
import {
  ConfirmAction,
  CopyField,
  SettingRow,
  SettingsPage,
  SettingsSection,
  gql,
  useResource,
  useSettingText,
} from "../ui";
import { useConfirmedSettingText } from "./workspace/useConfirmedSettingText";
import {
  URL_KEY_MAX,
  nameProblem,
  normalizeUrlKey,
  urlKeyProblem,
} from "./workspace/urlKey";

/* General: the workspace's identity (name, URL key, ID). Creating teams lives on the Teams page; this page links there instead
 * of keeping a second, smaller create form. There is no workspace delete or leave operation
 * on the server, so this page has no danger zone. */

type UpdatePayload = {
  workspaceUpdate: {
    success: boolean;
    organization: SettingsWorkspace | null;
  } | null;
};

async function updateWorkspace(input: { name?: string; urlKey?: string }) {
  const d = await gql<UpdatePayload>(
    `mutation($input:WorkspaceUpdateInput!){workspaceUpdate(input:$input){success organization{id name urlKey}}}`,
    { input },
  );
  const org = d.workspaceUpdate?.organization;
  if (!d.workspaceUpdate?.success || !org)
    throw new Error("The workspace wasn't saved. Try again.");
  return org;
}

function friendlyKeyError(e: unknown): Error {
  const raw = e instanceof Error ? e.message : String(e);
  if (/already exists/i.test(raw))
    return new Error(
      "Another workspace already uses this URL. Pick a different one.",
    );
  if (/letters, numbers, and hyphens/i.test(raw))
    return new Error(
      `Use 1–${URL_KEY_MAX} letters, numbers and single hyphens, not at the start or end.`,
    );
  return e instanceof Error ? e : new Error(raw);
}

function sampleLink(key: string, teamKey: string) {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  return `${origin}/${key || "…"}/issue/${teamKey}-12`;
}

export default function WorkspaceSettings() {
  const { workspace, teams, onWorkspaceSaved, openSection } = useSettings();
  const nameId = useId();
  const keyId = useId();
  const keyHelpId = useId();
  const teamKey = teams[0]?.key || "ENG";

  // Read the workspace from the server, not only from the shell: the shell's copy comes from
  // the sync cache, which a workspaceUpdate does not invalidate, so it can be stale after reload.
  const live = useResource(() =>
    gql<{ workspace: SettingsWorkspace }>(
      `query{workspace{id name urlKey}}`,
    ).then((d) => d.workspace),
  );
  const current = live.data ?? workspace;
  useEffect(() => {
    const w = live.data;
    if (w && (w.name !== workspace.name || w.urlKey !== workspace.urlKey))
      onWorkspaceSaved(w);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only when the server copy arrives
  }, [live.data]);
  const applySaved = (org: SettingsWorkspace) => {
    live.setData(org);
    onWorkspaceSaved(org);
  };

  // The shell (sidebar) updates once the server accepted the change. It is not updated
  // optimistically: the save hook treats a new `value` as committed.
  const name = useSettingText({
    value: current.name,
    validate: nameProblem,
    undoLabel: "Workspace renamed",
    save: async (next) => applySaved(await updateWorkspace({ name: next })),
  });

  const urlKey = useConfirmedSettingText({
    value: current.urlKey,
    normalize: normalizeUrlKey,
    validate: urlKeyProblem,
    save: async (next) => {
      try {
        applySaved(await updateWorkspace({ urlKey: next }));
      } catch (e) {
        throw friendlyKeyError(e);
      }
    },
  });
  const previewKey = normalizeUrlKey(urlKey.draft);

  return (
    <SettingsPage
      title="General"
      description="Your workspace's name and address."
    >
      <SettingsSection title="Workspace">
        <SettingRow
          label="Name"
          htmlFor={nameId}
          description="Shown in the sidebar, in invitations and in emails."
          status={name.status}
        >
          <Input
            id={nameId}
            autoComplete="off"
            className="sm:w-64"
            {...name.inputProps}
            onKeyDown={(e) => {
              // Revert in place: blurring here would commit the draft being discarded.
              if (e.key === "Escape") {
                e.preventDefault();
                name.inputProps.onChange({
                  target: { value: current.name },
                } as React.ChangeEvent<HTMLInputElement>);
                return;
              }
              name.inputProps.onKeyDown(e);
            }}
          />
        </SettingRow>
        <SettingRow
          label="URL"
          htmlFor={keyId}
          description={
            <span id={keyHelpId}>
              Every link to a task, project or document starts with this.
              Changing it breaks links people have already shared.
            </span>
          }
          status={urlKey.status}
          stacked
        >
          <div className="grid w-full min-w-0 gap-1.5">
            <Input
              id={keyId}
              autoComplete="off"
              spellCheck={false}
              autoCapitalize="none"
              maxLength={URL_KEY_MAX + 8}
              aria-describedby={keyHelpId}
              className="sm:w-64"
              {...urlKey.inputProps}
              onChange={(e) =>
                urlKey.inputProps.onChange({
                  target: { value: e.target.value.toLowerCase() },
                } as React.ChangeEvent<HTMLInputElement>)
              }
            />
            <p
              className="m-0 truncate text-sm text-muted-foreground"
              data-url-preview=""
            >
              Links look like{" "}
              <span className="font-mono text-foreground">
                {sampleLink(previewKey, teamKey)}
              </span>
            </p>
          </div>
        </SettingRow>
        <SettingRow
          label="Workspace ID"
          description="Use this when you call the API or contact support."
          stacked
        >
          <CopyField label="Workspace ID" value={current.id} />
        </SettingRow>
      </SettingsSection>

      <SettingsSection title="Teams">
        <SettingRow
          label="Create and manage teams"
          description="Each team has its own key, members, workflow and cycles."
        >
          <Button onClick={() => openSection("teams")}>
            Open Teams
            <ArrowRight className="size-3.5" aria-hidden="true" />
          </Button>
        </SettingRow>
      </SettingsSection>

      <ConfirmAction
        open={urlKey.pending !== null}
        onOpenChange={(open) => {
          if (!open && urlKey.pending !== null) urlKey.cancel();
        }}
        title="Change the workspace URL?"
        description={
          <>
            Links that start with{" "}
            <span className="font-mono text-foreground">
              /{urlKey.committed}/
            </span>{" "}
            stop working, including ones in emails, chat and bookmarks. New
            links use{" "}
            <span className="font-mono text-foreground">
              /{urlKey.pending ?? ""}/
            </span>
            .
          </>
        }
        confirmLabel="Change URL"
        onConfirm={urlKey.confirm}
      />
    </SettingsPage>
  );
}
