import * as React from "react";
import { MailCheck, Link2 } from "lucide-react";
import { Button } from "../../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
} from "../../../components/ui/dialog";
import { Input } from "../../../components/ui/input";
import {
  Checkbox,
  RadioGroup,
  RadioGroupItem,
} from "../../../components/ui/switch";
import { CopyField, Field, FormDialog, Notice, errorMessage } from "../../ui";
import type { TeamRow } from "../teams/api";
import {
  createInviteLink,
  inviteMember,
  type InviteLink,
  type Member,
} from "./api";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export type InviteOutcome = {
  member: Member;
  link: InviteLink | null;
  linkError: string | null;
};

export function InviteDialog({
  open,
  onOpenChange,
  teams,
  canGrantAdmin,
  onInvited,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  teams: TeamRow[];
  /** Only admins may invite someone straight in as an admin. */
  canGrantAdmin: boolean;
  onInvited: (outcome: InviteOutcome) => void;
}) {
  const [name, setName] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [role, setRole] = React.useState<"member" | "admin">("member");
  const [teamIds, setTeamIds] = React.useState<string[]>([]);
  const [touched, setTouched] = React.useState(false);
  React.useEffect(() => {
    if (!open) return;
    setName("");
    setEmail("");
    setRole("member");
    setTeamIds([]);
    setTouched(false);
  }, [open]);
  const nameError = name.trim() ? null : "Enter their name.";
  const emailError = EMAIL.test(email.trim())
    ? null
    : "Enter an email address, like sam@example.com.";

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Invite people"
      description="They get a one-time link that works for 72 hours. If email is set up on this server, we send it for you."
      submitLabel="Send invite"
      onSubmit={async () => {
        setTouched(true);
        if (nameError || emailError)
          throw new Error("Fix the highlighted fields.");
        const member = await inviteMember({
          name: name.trim(),
          email: email.trim(),
          admin: canGrantAdmin && role === "admin",
          teamIds,
        });
        let link: InviteLink | null = null;
        let linkError: string | null = null;
        try {
          link = await createInviteLink(
            member.id,
            member.email || email.trim(),
          );
        } catch (e) {
          linkError = errorMessage(e);
        }
        onInvited({ member, link, linkError });
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Name"
          htmlFor="invite-name"
          error={touched ? nameError : null}
        >
          <Input
            id="invite-name"
            autoFocus
            autoComplete="off"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field
          label="Email"
          htmlFor="invite-email"
          error={touched ? emailError : null}
          help="They must sign in with this exact address."
        >
          <Input
            id="invite-email"
            inputMode="email"
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
      </div>
      {canGrantAdmin && (
        <fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
          <legend className="mb-1.5 p-0 text-sm font-medium">Role</legend>
          <RadioGroup
            aria-label="Role"
            value={role}
            onValueChange={(v) => setRole(v as "member" | "admin")}
            className="grid gap-2 sm:grid-cols-2"
          >
            {(
              [
                ["member", "Member", "Works in the teams they belong to."],
                [
                  "admin",
                  "Admin",
                  "Full access: settings, billing, members and every team.",
                ],
              ] as const
            ).map(([value, label, help]) => (
              <label
                key={value}
                htmlFor={`invite-role-${value}`}
                className="flex cursor-pointer items-start gap-2.5 rounded-lg border border-line p-3 has-data-[state=checked]:border-primary"
              >
                <RadioGroupItem
                  id={`invite-role-${value}`}
                  value={value}
                  className="mt-0.5"
                />
                <span className="grid gap-0.5">
                  <span className="text-base font-medium">{label}</span>
                  <span className="text-sm text-muted-foreground">{help}</span>
                </span>
              </label>
            ))}
          </RadioGroup>
        </fieldset>
      )}
      {teams.length > 0 && (
        <fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
          <legend className="mb-1.5 p-0 text-sm font-medium">
            Teams{" "}
            <span className="font-normal text-muted-foreground">
              (optional)
            </span>
          </legend>
          <div className="grid max-h-44 gap-0.5 overflow-y-auto rounded-lg border border-line p-1">
            {teams.map((t) => {
              const id = `invite-team-${t.id}`;
              return (
                <label
                  key={t.id}
                  htmlFor={id}
                  className="flex min-w-0 cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-base hover:bg-subtle pointer-coarse:min-h-11"
                >
                  <Checkbox
                    id={id}
                    checked={teamIds.includes(t.id)}
                    onCheckedChange={(on) =>
                      setTeamIds((c) =>
                        on ? [...c, t.id] : c.filter((x) => x !== t.id),
                      )
                    }
                  />
                  <span className="truncate">{t.name}</span>
                  <span className="text-sm text-muted-foreground">{t.key}</span>
                </label>
              );
            })}
          </div>
          <p className="m-0 text-sm text-muted-foreground">
            Only team members can be assigned a team's tasks.
          </p>
        </fieldset>
      )}
    </FormDialog>
  );
}

/** Shows a freshly issued invite link once. It is never shown again after closing. */
export function InviteLinkDialog({
  outcome,
  onClose,
  onRetry,
}: {
  outcome: InviteOutcome | null;
  onClose: () => void;
  onRetry: (member: Member) => Promise<void>;
}) {
  const [busy, setBusy] = React.useState(false);
  const who = outcome?.member.name || outcome?.member.email || "them";
  const link = outcome?.link;
  return (
    <Dialog open={!!outcome} onOpenChange={(o) => !o && onClose()}>
      <DialogContent>
        <DialogHeader
          title={
            link
              ? link.emailed
                ? `Invite sent to ${link.email}`
                : `Invite ready for ${who}`
              : `${who} was added, but has no invite link yet`
          }
          description={
            link
              ? link.emailed
                ? "They can join from that email. You can also send them this link yourself."
                : "Email isn't set up on this server, so send this link yourself."
              : "Their profile exists, but the link could not be created."
          }
        />
        {link ? (
          <div className="grid gap-2">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              {link.emailed ? (
                <MailCheck className="size-3.5" aria-hidden="true" />
              ) : (
                <Link2 className="size-3.5" aria-hidden="true" />
              )}
              Works once, expires in 72 hours, and won't be shown again.
            </div>
            <CopyField label="Invite link" value={link.url} secret />
          </div>
        ) : (
          <Notice tone="error">
            {outcome?.linkError || "Something went wrong."}
          </Notice>
        )}
        <DialogFooter>
          {!link && outcome && (
            <Button
              loading={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await onRetry(outcome.member);
                } finally {
                  setBusy(false);
                }
              }}
            >
              Try again
            </Button>
          )}
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
