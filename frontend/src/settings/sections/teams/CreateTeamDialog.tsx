import * as React from "react";
import { Input, Textarea } from "../../../components/ui/input";
import { Select } from "../../../components/ui/select";
import { Checkbox } from "../../../components/ui/switch";
import { Field, FormDialog } from "../../ui";
import {
  createTeam,
  depthOf,
  personLabel,
  VISIBILITY_OPTIONS,
  type Person,
  type TeamRow,
  type Visibility,
} from "./api";

const NO_PARENT = "__none__";

export function suggestKey(name: string) {
  const words = name
    .toUpperCase()
    .replace(/[^A-Z0-9\s]/g, " ")
    .split(/\s+/)
    .filter(Boolean);
  if (!words.length) return "";
  if (words.length === 1) return words[0].slice(0, 3);
  return words
    .map((w) => w[0])
    .join("")
    .slice(0, 4);
}

export function validateKey(key: string): string | null {
  if (!key) return "Enter a key, for example ENG.";
  if (!/^[A-Za-z0-9]+$/.test(key)) return "Use letters and numbers only.";
  if (key.length > 8) return "Keep the key to 8 characters or fewer.";
  return null;
}

function browserTimeZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function timeZones(): string[] {
  const own = browserTimeZone();
  try {
    const all = (
      Intl as unknown as { supportedValuesOf?: (k: string) => string[] }
    ).supportedValuesOf?.("timeZone");
    if (all?.length) return Array.from(new Set(["UTC", own, ...all]));
  } catch {
    /* older browsers: fall through */
  }
  return Array.from(new Set(["UTC", own]));
}

export function CreateTeamDialog({
  open,
  onOpenChange,
  teams,
  people,
  viewerId,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  teams: TeamRow[];
  people: Person[];
  viewerId?: string;
  onCreated: (team: { id: string; key: string }) => void;
}) {
  const [name, setName] = React.useState("");
  const [key, setKey] = React.useState("");
  const [keyEdited, setKeyEdited] = React.useState(false);
  const [description, setDescription] = React.useState("");
  const [visibility, setVisibility] = React.useState<Visibility>("public");
  const [parent, setParent] = React.useState(NO_PARENT);
  const [timezone, setTimezone] = React.useState(browserTimeZone);
  const [memberIds, setMemberIds] = React.useState<string[]>([]);
  const [touched, setTouched] = React.useState(false);
  const zones = React.useMemo(timeZones, []);

  React.useEffect(() => {
    if (!open) return;
    setName("");
    setKey("");
    setKeyEdited(false);
    setDescription("");
    setVisibility("public");
    setParent(NO_PARENT);
    setTimezone(browserTimeZone());
    setMemberIds([]);
    setTouched(false);
  }, [open]);

  const effectiveKey = (keyEdited ? key : suggestKey(name)).trim();
  const keyTaken = teams.some(
    (t) => t.key.toUpperCase() === effectiveKey.toUpperCase(),
  );
  const keyError =
    validateKey(effectiveKey) ||
    (keyTaken
      ? `${effectiveKey.toUpperCase()} is already used by a team.`
      : null);
  const nameError = name.trim() ? null : "Enter a team name.";
  // Departments nest at most three levels deep (writer.py MAX_TEAM_DEPTH).
  const parentOptions = teams
    .filter((t) => depthOf(teams, t.id) < 3)
    .map((t) => ({ value: t.id, label: `${t.name} (${t.key})` }));
  const candidates = people.filter((p) => p.id !== viewerId);

  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Create team"
      description="Teams own tasks, cycles and workflow states. You join the new team as its owner."
      submitLabel="Create team"
      wide
      canSubmit={!nameError && !keyError}
      onSubmit={async () => {
        setTouched(true);
        if (nameError || keyError)
          throw new Error("Fix the highlighted fields.");
        const created = await createTeam({
          name: name.trim(),
          key: effectiveKey.toUpperCase(),
          description: description.trim() || undefined,
          visibility,
          parentTeamId: parent === NO_PARENT ? null : parent,
          timezone,
          memberIds,
        });
        onCreated(created);
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Name"
          htmlFor="team-create-name"
          error={touched ? nameError : null}
        >
          <Input
            id="team-create-name"
            autoFocus
            value={name}
            placeholder="Engineering"
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
        <Field
          label="Key"
          htmlFor="team-create-key"
          error={effectiveKey || touched ? keyError : null}
          help="Starts every task ID, like ENG-12."
        >
          <Input
            id="team-create-key"
            value={keyEdited ? key : suggestKey(name)}
            maxLength={8}
            placeholder="ENG"
            spellCheck={false}
            autoComplete="off"
            onChange={(e) => {
              setKeyEdited(true);
              setKey(e.target.value.toUpperCase());
            }}
          />
        </Field>
      </div>
      <Field label="Description" htmlFor="team-create-description" optional>
        <Textarea
          id="team-create-description"
          rows={2}
          value={description}
          placeholder="What this team works on"
          onChange={(e) => setDescription(e.target.value)}
        />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Visibility"
          htmlFor="team-create-visibility"
          help={
            VISIBILITY_OPTIONS.find((o) => o.value === visibility)?.description
          }
        >
          <Select
            id="team-create-visibility"
            className="sm:w-full"
            value={visibility}
            onValueChange={(v) => setVisibility(v as Visibility)}
            options={VISIBILITY_OPTIONS}
          />
        </Field>
        <Field
          label="Parent team"
          htmlFor="team-create-parent"
          help="Members of a sub-team also join the team above it."
        >
          <Select
            id="team-create-parent"
            className="sm:w-full"
            value={parent}
            onValueChange={setParent}
            options={[{ value: NO_PARENT, label: "None" }, ...parentOptions]}
          />
        </Field>
      </div>
      <Field
        label="Time zone"
        htmlFor="team-create-timezone"
        help="Cycles start and end at midnight in this time zone. It can't be changed after the team is created."
      >
        <Select
          id="team-create-timezone"
          className="sm:w-full"
          value={timezone}
          onValueChange={setTimezone}
          options={zones.map((z) => ({
            value: z,
            label: z.replace(/_/g, " "),
          }))}
        />
      </Field>
      {candidates.length > 0 && (
        <fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
          <legend className="mb-1.5 p-0 text-sm font-medium">
            Members{" "}
            <span className="font-normal text-muted-foreground">
              (optional)
            </span>
          </legend>
          <div className="grid max-h-48 gap-0.5 overflow-y-auto rounded-lg border border-line p-1">
            {candidates.map((p) => {
              const id = `team-create-member-${p.id}`;
              return (
                <label
                  key={p.id}
                  htmlFor={id}
                  className="flex min-w-0 cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 text-base hover:bg-subtle pointer-coarse:min-h-11"
                >
                  <Checkbox
                    id={id}
                    checked={memberIds.includes(p.id)}
                    onCheckedChange={(on) =>
                      setMemberIds((c) =>
                        on ? [...c, p.id] : c.filter((x) => x !== p.id),
                      )
                    }
                  />
                  <span className="truncate">{personLabel(p)}</span>
                  {p.name && p.email && (
                    <span className="truncate text-sm text-muted-foreground">
                      {p.email}
                    </span>
                  )}
                </label>
              );
            })}
          </div>
        </fieldset>
      )}
    </FormDialog>
  );
}
