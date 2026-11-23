import * as React from "react";
import { Plus, SearchX, Users } from "lucide-react";
import { Button } from "../../components/ui/button";
import { DropdownMenuItem } from "../../components/ui/menu";
import { toast } from "../../components/ui/toast";
import { useSettings } from "../context";
import {
  Badge,
  EmptyState,
  Loadable,
  ResourceRow,
  SettingsCard,
  SettingsPage,
  SettingsSection,
  useResource,
} from "../ui";
import {
  loadPeople,
  loadTeams,
  refreshShellTeams,
  type TeamRow,
} from "./teams/api";
import { CreateTeamDialog } from "./teams/CreateTeamDialog";
import { TeamMark } from "./teams/PersonAvatar";
import { TeamDetail } from "./teams/TeamDetail";

/* Teams: a list of every team the viewer can see; opening one shows its settings on the
 * same route (/workspace/settings/teams?team=ENG), so the back button and links work. */
function readTeamParam() {
  try {
    return new URLSearchParams(window.location.search).get("team");
  } catch {
    return null;
  }
}

function writeTeamParam(key: string | null, replace = false) {
  const url = new URL(window.location.href);
  if (key) url.searchParams.set("team", key);
  else url.searchParams.delete("team");
  if (url.href === window.location.href) return;
  if (replace) window.history.replaceState(window.history.state, "", url);
  else window.history.pushState(window.history.state, "", url);
}

export default function TeamsSettings() {
  const { can, isAdmin, viewer, onTeamsChanged, openTeamCycleSettings } =
    useSettings();
  const teams = useResource(loadTeams);
  const people = useResource(loadPeople);
  const [selected, setSelected] = React.useState<string | null>(readTeamParam);
  const [creating, setCreating] = React.useState(false);

  React.useEffect(() => {
    const sync = () => setSelected(readTeamParam());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  const open = (key: string | null) => {
    writeTeamParam(key);
    setSelected(key);
    window.scrollTo?.({ top: 0 });
  };
  const changed = async () => {
    await teams.reload();
    void refreshShellTeams(onTeamsChanged);
  };

  const list = teams.data;
  const current =
    selected && list
      ? list.find((t) => t.key.toUpperCase() === selected.toUpperCase())
      : undefined;

  if (selected && list && current) {
    return (
      <TeamDetail
        key={current.id}
        team={current}
        teams={list}
        people={people.data || []}
        onBack={() => open(null)}
        onChanged={changed}
        onKeyChanged={(key) => {
          writeTeamParam(key, true);
          setSelected(key);
        }}
        onRemoved={(message) => {
          toast({ title: message });
          open(null);
          void changed();
        }}
      />
    );
  }

  const canCreate = can("team.create");
  const parentName = (t: TeamRow) =>
    list?.find((p) => p.id === t.parentTeamId)?.name;

  return (
    <SettingsPage
      title="Teams"
      description={
        isAdmin
          ? "Teams own tasks, cycles and workflows. Open a team to rename it, change who is on it, or archive it."
          : "Teams own tasks, cycles and workflows. Open a team to see its settings; only workspace admins can change them."
      }
      actions={
        canCreate ? (
          <Button variant="primary" onClick={() => setCreating(true)}>
            <Plus className="size-3.5" aria-hidden="true" />
            Create team
          </Button>
        ) : undefined
      }
    >
      {selected && list && !current && (
        <SettingsCard>
          <EmptyState
            icon={<SearchX />}
            title={`There is no team “${selected}”`}
            description="It may have been archived, renamed or made private."
            action={<Button onClick={() => open(null)}>Show all teams</Button>}
          />
        </SettingsCard>
      )}
      <SettingsSection bare>
        <Loadable
          resource={teams}
          rows={3}
          isEmpty={(d) => d.length === 0}
          empty={
            <EmptyState
              icon={<Users />}
              title="No teams yet"
              description={
                canCreate
                  ? "Create a team to start tracking work."
                  : "Ask a workspace admin to create a team."
              }
              action={
                canCreate ? (
                  <Button variant="primary" onClick={() => setCreating(true)}>
                    Create team
                  </Button>
                ) : undefined
              }
            />
          }
        >
          {(rows) => (
            <SettingsCard>
              {rows.map((t) => {
                const count = t.members.length;
                const parent = parentName(t);
                return (
                  <ResourceRow
                    key={t.id}
                    leading={<TeamMark teamKey={t.key} />}
                    title={t.name}
                    meta={[
                      t.key,
                      `${count} ${count === 1 ? "member" : "members"}`,
                      parent ? `in ${parent}` : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                    badges={
                      t.visibility !== "public" ? (
                        <Badge>
                          {t.visibility === "private"
                            ? "Private"
                            : "Restricted"}
                        </Badge>
                      ) : undefined
                    }
                    onOpen={() => open(t.key)}
                    menuLabel={`Actions for ${t.name}`}
                    actions={
                      <>
                        <DropdownMenuItem onSelect={() => open(t.key)}>
                          Open team settings
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onSelect={() =>
                            openTeamCycleSettings({
                              id: t.id,
                              name: t.name,
                              key: t.key,
                            })
                          }
                        >
                          Cycle settings
                        </DropdownMenuItem>
                      </>
                    }
                  />
                );
              })}
            </SettingsCard>
          )}
        </Loadable>
      </SettingsSection>
      <CreateTeamDialog
        open={creating}
        onOpenChange={setCreating}
        teams={list || []}
        people={people.data || []}
        viewerId={viewer.id}
        onCreated={(team) => {
          toast({ title: "Team created" });
          void changed().then(() => open(team.key));
        }}
      />
    </SettingsPage>
  );
}
