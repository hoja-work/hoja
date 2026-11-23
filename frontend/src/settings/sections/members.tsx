import * as React from "react";
import { SearchX, UserPlus, Users } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import {
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "../../components/ui/menu";
import { Select } from "../../components/ui/select";
import { toast } from "../../components/ui/toast";
import { useSettings } from "../context";
import {
  Badge,
  ConfirmAction,
  EmptyState,
  Loadable,
  Notice,
  ResourceRow,
  SettingsCard,
  SettingsPage,
  SettingsSection,
  useResource,
} from "../ui";
import {
  archiveMember,
  createInviteLink,
  loadMembers,
  memberLabel,
  restoreMember,
  revokeInvite,
  setActive,
  type Member,
} from "./members/api";
import {
  InviteDialog,
  InviteLinkDialog,
  type InviteOutcome,
} from "./members/InviteDialogs";
import { RoleDialog, TeamsDialog } from "./members/MemberDialogs";
import { loadTeams, refreshShellTeams } from "./teams/api";
import { PersonAvatar } from "./teams/PersonAvatar";

type Status = "active" | "pending" | "deactivated";
type Filter = "all" | "admins" | "pending" | "deactivated";
type Pending = {
  kind: "deactivate" | "remove" | "revoke" | "relink" | "restore";
  member: Member;
} | null;

/* Members: everyone in the workspace, with role, status and teams. Invites, deactivation,
 * removal and team changes need `member.invite` (admins hold it; a workspace can grant it);
 * role changes, pending invites and the removed list are admin-only on the server. */
export default function MembersSettings() {
  const { isAdmin, can, viewer, workspace, onTeamsChanged } =
    useSettings();
  const canInvite = can("member.invite");
  const data = useResource(() => loadMembers(isAdmin), [isAdmin]);
  const teams = useResource(loadTeams);
  const [query, setQuery] = React.useState("");
  const [filter, setFilter] = React.useState<Filter>("all");
  const [inviting, setInviting] = React.useState(false);
  const [outcome, setOutcome] = React.useState<InviteOutcome | null>(null);
  const [roleFor, setRoleFor] = React.useState<Member | null>(null);
  const [teamsFor, setTeamsFor] = React.useState<Member | null>(null);
  const [pending, setPending] = React.useState<Pending>(null);

  const refresh = async () => {
    await Promise.all([data.reload(), teams.reload()]);
  };
  const teamsChanged = async () => {
    await refresh();
    void refreshShellTeams(onTeamsChanged);
  };

  const pendingIds = new Set((data.data?.pending || []).map((p) => p.userId));
  const statusOf = (m: Member): Status =>
    m.active === false
      ? "deactivated"
      : pendingIds.has(m.id)
        ? "pending"
        : "active";
  const members = data.data?.members || [];
  const activeAdmins = members.filter((m) => m.admin && m.active !== false);
  const teamKeys = (id: string) =>
    (teams.data || [])
      .filter((t) => t.members.some((m) => m.id === id))
      .map((t) => t.key);

  const needle = query.trim().toLowerCase();
  const visible = members
    .filter((m) => {
      const s = statusOf(m);
      if (filter === "admins" && !m.admin) return false;
      if (filter === "pending" && s !== "pending") return false;
      if (filter === "deactivated" && s !== "deactivated") return false;
      if (!needle) return true;
      return `${m.name || ""} ${m.email || ""} ${teamKeys(m.id).join(" ")}`
        .toLowerCase()
        .includes(needle);
    })
    .sort((a, b) => memberLabel(a).localeCompare(memberLabel(b)));

  const count = (f: Filter) =>
    members.filter((m) =>
      f === "admins" ? m.admin : f === "all" ? true : statusOf(m) === f,
    ).length;
  const filterOptions = [
    { value: "all", label: `Everyone (${count("all")})` },
    { value: "admins", label: `Admins (${count("admins")})` },
    ...(isAdmin
      ? [{ value: "pending", label: `Invite pending (${count("pending")})` }]
      : []),
    { value: "deactivated", label: `Deactivated (${count("deactivated")})` },
  ];

  const confirm = pending && confirmCopy(pending, workspace.name);

  return (
    <SettingsPage
      wide
      title="Members"
      description={`Everyone who can sign in to ${workspace.name}. Invite people, choose their teams and decide who can manage the workspace.`}
      actions={
        canInvite ? (
          <Button variant="primary" onClick={() => setInviting(true)}>
            <UserPlus className="size-3.5" aria-hidden="true" />
            Invite people
          </Button>
        ) : undefined
      }
    >
      <SettingsSection bare>
        {!canInvite && (
          <Notice tone="locked">
            You can see who is here. Only admins, or people an admin allows to
            invite members, can invite, deactivate or remove people.
          </Notice>
        )}
        {canInvite && !isAdmin && (
          <Notice tone="locked">
            Only admins can change roles or see pending invites and removed
            people.
          </Notice>
        )}
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Input
            type="search"
            aria-label="Search members"
            placeholder="Search by name, email or team"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="sm:max-w-80"
          />
          <Select
            aria-label="Show"
            value={filter}
            onValueChange={(v) => setFilter(v as Filter)}
            options={filterOptions}
            className="sm:w-52"
          />
        </div>
        <Loadable resource={data} rows={4}>
          {() =>
            visible.length ? (
              <SettingsCard>
                {visible.map((m) => {
                  const status = statusOf(m);
                  const self = m.id === viewer.id;
                  // member.invite manages members only; an admin's access is changed by admins (HOJA-735).
                  const manageable = canInvite && (isAdmin || !m.admin);
                  const keys = teamKeys(m.id);
                  const label = memberLabel(m);
                  return (
                    <ResourceRow
                      key={m.id}
                      leading={<PersonAvatar name={m.name} email={m.email} />}
                      title={label}
                      meta={[
                        m.name ? m.email : null,
                        keys.length ? keys.join(", ") : "No team",
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                      badges={
                        <>
                          {m.admin && <Badge tone="strong">Admin</Badge>}
                          {self && <Badge>You</Badge>}
                          {status === "pending" && (
                            <Badge>Invite pending</Badge>
                          )}
                          {status === "deactivated" && (
                            <Badge tone="danger">Deactivated</Badge>
                          )}
                        </>
                      }
                      menuLabel={`Actions for ${label}`}
                      actions={
                        <>
                          <DropdownMenuItem
                            disabled={!isAdmin || status === "deactivated"}
                            onSelect={() => setRoleFor(m)}
                          >
                            {isAdmin
                              ? "Change role…"
                              : "Change role (admins only)"}
                          </DropdownMenuItem>
                          {canInvite && (
                            <DropdownMenuItem onSelect={() => setTeamsFor(m)}>
                              Teams…
                            </DropdownMenuItem>
                          )}
                          {manageable && status === "pending" && (
                            <DropdownMenuItem
                              onSelect={() =>
                                setPending({ kind: "relink", member: m })
                              }
                            >
                              New invite link…
                            </DropdownMenuItem>
                          )}
                          {manageable && status === "deactivated" && (
                            <DropdownMenuItem
                              onSelect={async () => {
                                try {
                                  await setActive(m.id, true);
                                  toast({ title: `${label} reactivated` });
                                  await data.reload();
                                } catch (e) {
                                  toast({
                                    tone: "error",
                                    title: "Not reactivated",
                                    description:
                                      e instanceof Error
                                        ? e.message
                                        : String(e),
                                  });
                                }
                              }}
                            >
                              Reactivate
                            </DropdownMenuItem>
                          )}
                          {manageable && !self && (
                            <>
                              <DropdownMenuSeparator />
                              {status === "pending" && (
                                <DropdownMenuItem
                                  destructive
                                  onSelect={() =>
                                    setPending({ kind: "revoke", member: m })
                                  }
                                >
                                  Revoke invite…
                                </DropdownMenuItem>
                              )}
                              {status !== "deactivated" && (
                                <DropdownMenuItem
                                  destructive
                                  onSelect={() =>
                                    setPending({
                                      kind: "deactivate",
                                      member: m,
                                    })
                                  }
                                >
                                  Deactivate…
                                </DropdownMenuItem>
                              )}
                              <DropdownMenuItem
                                destructive
                                onSelect={() =>
                                  setPending({ kind: "remove", member: m })
                                }
                              >
                                Remove from workspace…
                              </DropdownMenuItem>
                            </>
                          )}
                        </>
                      }
                    />
                  );
                })}
              </SettingsCard>
            ) : (
              <SettingsCard>
                {members.length ? (
                  <EmptyState
                    icon={<SearchX />}
                    title="No one matches"
                    description="Try a different name or email, or show everyone."
                    action={
                      <Button
                        onClick={() => {
                          setQuery("");
                          setFilter("all");
                        }}
                      >
                        Show everyone
                      </Button>
                    }
                  />
                ) : (
                  <EmptyState
                    icon={<Users />}
                    title="No members yet"
                    description="Invite people to start working together."
                    action={
                      canInvite ? (
                        <Button
                          variant="primary"
                          onClick={() => setInviting(true)}
                        >
                          Invite people
                        </Button>
                      ) : undefined
                    }
                  />
                )}
              </SettingsCard>
            )
          }
        </Loadable>
      </SettingsSection>

      {isAdmin && (data.data?.removed.length || 0) > 0 && (
        <SettingsSection
          title="Removed"
          description="Removed people keep their task history and can be restored."
        >
          {data.data!.removed.map((m) => (
            <ResourceRow
              key={m.id}
              leading={<PersonAvatar name={m.name} email={m.email} />}
              title={memberLabel(m)}
              meta={m.name ? m.email : undefined}
              badges={<Badge>Removed</Badge>}
              menuLabel={`Actions for removed ${memberLabel(m)}`}
              actions={
                <DropdownMenuItem
                  disabled={!canInvite}
                  onSelect={() => setPending({ kind: "restore", member: m })}
                >
                  Restore…
                </DropdownMenuItem>
              }
            />
          ))}
        </SettingsSection>
      )}

      <InviteDialog
        open={inviting}
        onOpenChange={setInviting}
        teams={teams.data || []}
        canGrantAdmin={isAdmin}
        onInvited={(o) => {
          setOutcome(o);
          void teamsChanged();
        }}
      />
      <InviteLinkDialog
        outcome={outcome}
        onClose={() => setOutcome(null)}
        onRetry={async (member) => {
          try {
            const link = await createInviteLink(member.id, member.email || "");
            setOutcome({ member, link, linkError: null });
            void data.reload();
          } catch (e) {
            setOutcome({
              member,
              link: null,
              linkError: e instanceof Error ? e.message : String(e),
            });
          }
        }}
      />
      <RoleDialog
        member={roleFor}
        onOpenChange={(o) => !o && setRoleFor(null)}
        isSelf={roleFor?.id === viewer.id}
        lastActiveAdmin={
          !!roleFor?.admin &&
          activeAdmins.length === 1 &&
          activeAdmins[0].id === roleFor.id
        }
        onSaved={() => data.reload()}
      />
      <TeamsDialog
        member={teamsFor}
        onOpenChange={(o) => !o && setTeamsFor(null)}
        teams={teams.data || []}
        onSaved={teamsChanged}
      />
      <ConfirmAction
        open={!!pending}
        onOpenChange={(o) => !o && setPending(null)}
        title={confirm?.title || ""}
        description={confirm?.description}
        confirmLabel={confirm?.label || "Confirm"}
        destructive={confirm?.destructive ?? true}
        onConfirm={async () => {
          if (!pending) return;
          const { kind, member } = pending;
          const label = memberLabel(member);
          if (kind === "deactivate") {
            await setActive(member.id, false);
            toast({ title: `${label} deactivated` });
          } else if (kind === "remove") {
            await archiveMember(member.id);
            toast({ title: `${label} removed` });
          } else if (kind === "revoke") {
            await revokeInvite(member.id);
            toast({ title: `Invite for ${label} revoked` });
          } else if (kind === "restore") {
            await restoreMember(member.id);
            toast({ title: `${label} restored` });
          } else if (kind === "relink") {
            const link = await createInviteLink(member.id, member.email || "");
            setOutcome({ member, link, linkError: null });
          }
          await data.reload();
        }}
      />
    </SettingsPage>
  );
}

function confirmCopy(p: NonNullable<Pending>, workspaceName: string) {
  const who = memberLabel(p.member);
  switch (p.kind) {
    case "deactivate":
      return {
        title: `Deactivate ${who}?`,
        description: `They are signed out of ${workspaceName} and their API keys stop working until you reactivate them. Their tasks, comments and team memberships stay.`,
        label: "Deactivate",
        destructive: true,
      };
    case "remove":
      return {
        title: `Remove ${who} from ${workspaceName}?`,
        description:
          "They lose access now: their sessions end, their API keys are revoked and their sign-in password is deleted. Their task history stays, and an admin can restore them later, but restoring does not bring back keys, sessions or the password.",
        label: "Remove from workspace",
        destructive: true,
      };
    case "revoke":
      return {
        title: `Revoke the invite for ${who}?`,
        description:
          "Their invite link stops working. Their profile stays in the member list; remove it too if they should not join.",
        label: "Revoke invite",
        destructive: true,
      };
    case "relink":
      return {
        title: `Create a new invite link for ${who}?`,
        description:
          "Their current link stops working and a new one, valid for 72 hours, replaces it. If email is set up, it is sent to them again.",
        label: "Create new link",
        destructive: false,
      };
    case "restore":
      return {
        title: `Restore ${who}?`,
        description:
          "Their profile, task history and access come back. API keys, browser sessions and their password do not: they sign in again, reset their password if they used one, and create new API keys.",
        label: "Restore",
        destructive: false,
      };
  }
}
