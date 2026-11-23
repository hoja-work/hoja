import { useId, useMemo, useState } from "react";
import { Button } from "../../components/ui/button";
import { Input, Label, Textarea } from "../../components/ui/input";
import { Checkbox, Switch } from "../../components/ui/switch";
import {
  ConfirmAction,
  Loadable,
  Notice,
  SaveStatusLine,
  SettingRow,
  SettingsPage,
  SettingsSection,
  gql,
  useAction,
  useResource,
  useSetting,
  type SaveStatus,
} from "../ui";
import { MAX_ALLOWLIST, parseAllowlist } from "./security/cidr";

/* Security: the workspace's sign-in and session policy, plus the Hoja Connect kill switch.
 *
 * The policy is one form with one Save button on purpose: idle timeout, sign-in methods and the
 * IP allowlist interact, and a careless allowlist or method change can sign people out. So the
 * form validates every line before it sends anything and asks for confirmation when a save
 * would newly refuse someone. Connect is a single kill switch and saves instantly.
 *
 * The server clears any of the three policy fields a save omits, so every write here (the
 * Connect switch included) sends the full saved policy. */

type Policy = {
  idleTimeoutMinutes: number | null;
  allowedSigninMethods: string[];
  ipAllowlist: string[];
  connectEnabled: boolean;
  updatedAt: string | null;
};
type PolicyInput = {
  idleTimeoutMinutes: number | null;
  allowedSigninMethods: string[];
  ipAllowlist: string[];
  connectEnabled?: boolean;
};

const FIELDS =
  "idleTimeoutMinutes allowedSigninMethods ipAllowlist connectEnabled updatedAt";
const METHODS = [
  { id: "password", label: "Email and password" },
  { id: "google", label: "Google" },
] as const;
const IDLE_MAX = 10080;

async function savePolicy(input: PolicyInput): Promise<Policy> {
  const d = await gql<{ workspaceSecurityUpdate: Policy }>(
    `mutation($input:WorkspaceSecurityPolicyInput!){workspaceSecurityUpdate(input:$input){${FIELDS}}}`,
    { input },
  );
  return d.workspaceSecurityUpdate;
}

function savedInput(p: Policy): PolicyInput {
  return {
    idleTimeoutMinutes: p.idleTimeoutMinutes,
    allowedSigninMethods: p.allowedSigninMethods,
    ipAllowlist: p.ipAllowlist,
  };
}

/** [] on the server means "every method"; the form always shows the effective set. */
function effectiveMethods(methods: string[]) {
  return methods.length ? methods : METHODS.map((m) => m.id);
}

function idleProblem(text: string): string | null {
  if (!text.trim()) return null;
  const n = Number(text.trim());
  if (!Number.isInteger(n) || n < 1 || n > IDLE_MAX)
    return `Enter whole minutes from 1 to ${IDLE_MAX} (one week), or leave it empty for no timeout.`;
  return null;
}

function humanMinutes(n: number) {
  if (n % 1440 === 0) return `${n / 1440} day${n === 1440 ? "" : "s"}`;
  if (n % 60 === 0) return `${n / 60} hour${n === 60 ? "" : "s"}`;
  if (n > 60) return `${Math.floor(n / 60)} h ${n % 60} min`;
  return null;
}

export default function SecuritySettings() {
  const policy = useResource(() =>
    gql<{ workspaceSecurityPolicy: Policy }>(
      `query{workspaceSecurityPolicy{${FIELDS}}}`,
    ).then((d) => d.workspaceSecurityPolicy),
  );
  return (
    <SettingsPage
      title="Security"
      description="Who can sign in, from where and for how long. The server enforces this on every request."
    >
      <Loadable resource={policy} rows={4}>
        {(saved) => (
          <>
            <PolicyForm saved={saved} onSaved={(p) => policy.setData(p)} />
            <ConnectSection saved={saved} onSaved={(p) => policy.setData(p)} />
            <Notice title="Single sign-on and domain capture aren't available">
              Hoja doesn't run a SAML or OIDC sign-in flow yet, so there is
              nothing here to configure for them.
            </Notice>
          </>
        )}
      </Loadable>
    </SettingsPage>
  );
}

function PolicyForm({
  saved,
  onSaved,
}: {
  saved: Policy;
  onSaved: (p: Policy) => void;
}) {
  const idleId = useId();
  const listId = useId();
  const listHelpId = useId();
  const draftFrom = (p: Policy) => ({
    idle: p.idleTimeoutMinutes ? String(p.idleTimeoutMinutes) : "",
    methods: effectiveMethods(p.allowedSigninMethods),
    allowlist: p.ipAllowlist.join("\n"),
  });
  const [draft, setDraft] = useState(() => draftFrom(saved));
  const [confirming, setConfirming] = useState(false);
  const [savedStatus, setSavedStatus] = useState<SaveStatus>({
    state: "idle",
    error: null,
    retry: null,
  });

  const list = useMemo(
    () => parseAllowlist(draft.allowlist),
    [draft.allowlist],
  );
  const idleError = idleProblem(draft.idle);
  const listError = list.invalid.length
    ? `${list.invalid.length === 1 ? "This line isn't" : "These lines aren't"} an IP address or CIDR range: ${list.invalid.join(", ")}`
    : list.tooMany
      ? `An allowlist can have at most ${MAX_ALLOWLIST} entries. This one has ${list.entries.length}.`
      : null;

  const next: PolicyInput = {
    idleTimeoutMinutes: draft.idle.trim() ? Number(draft.idle.trim()) : null,
    allowedSigninMethods:
      draft.methods.length === METHODS.length ? [] : [...draft.methods],
    ipAllowlist: list.entries,
  };
  const savedMethods = effectiveMethods(saved.allowedSigninMethods);
  const dirty =
    next.idleTimeoutMinutes !== saved.idleTimeoutMinutes ||
    savedMethods.slice().sort().join() !==
      draft.methods.slice().sort().join() ||
    next.ipAllowlist.join("\n") !== saved.ipAllowlist.join("\n");
  const blocked = !!idleError || !!listError;

  const newlyOff = savedMethods.filter((m) => !draft.methods.includes(m));
  const listChanged =
    next.ipAllowlist.join("\n") !== saved.ipAllowlist.join("\n");
  const restricts =
    newlyOff.length > 0 || (listChanged && list.entries.length > 0);

  const commit = async () => {
    const p = await savePolicy(next);
    onSaved(p);
    setDraft(draftFrom(p));
    setSavedStatus({ state: "saved", error: null, retry: null });
    window.setTimeout(
      () => setSavedStatus({ state: "idle", error: null, retry: null }),
      2400,
    );
    return p;
  };
  const save = useAction(commit);

  const toggleMethod = (id: string, on: boolean) =>
    setDraft((d) => ({
      ...d,
      methods: on
        ? [...new Set([...d.methods, id])]
        : d.methods.filter((m) => m !== id),
    }));

  const idleHuman =
    !idleError && next.idleTimeoutMinutes
      ? humanMinutes(next.idleTimeoutMinutes)
      : null;
  const lastChanged = saved.updatedAt
    ? `Last changed ${new Date(saved.updatedAt).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}.`
    : "Never changed: no idle timeout, every sign-in method, any network.";

  return (
    <form
      noValidate
      aria-label="Security policy"
      onSubmit={(e) => {
        e.preventDefault();
        if (!dirty || blocked || save.busy) return;
        if (restricts) setConfirming(true);
        else void save.run();
      }}
    >
      <SettingsSection
        title="Sign-in and sessions"
        description={`These settings work together, so they save together. ${lastChanged}`}
      >
        <SettingRow
          label="Idle timeout"
          htmlFor={idleId}
          description="Sign people out after this long without activity. Leave empty for no idle timeout; sessions still end after 14 days."
          status={
            idleError
              ? { state: "error", error: idleError, retry: null }
              : undefined
          }
        >
          <div className="flex items-center gap-2">
            <Input
              id={idleId}
              inputMode="numeric"
              autoComplete="off"
              placeholder="None"
              className="w-24 text-right tabular-nums"
              aria-invalid={!!idleError || undefined}
              value={draft.idle}
              onChange={(e) =>
                setDraft((d) => ({ ...d, idle: e.target.value }))
              }
            />
            <span className="text-sm text-muted-foreground">
              minutes{idleHuman ? ` (${idleHuman})` : ""}
            </span>
          </div>
        </SettingRow>

        <SettingRow
          label="Sign-in methods"
          description="People can only sign in with the methods you allow. At least one stays on."
          stacked
        >
          <div
            role="group"
            aria-label="Allowed sign-in methods"
            className="grid w-full gap-3"
          >
            <div className="flex flex-wrap gap-x-6 gap-y-3">
              {METHODS.map((m) => {
                const checked = draft.methods.includes(m.id);
                const last = checked && draft.methods.length === 1;
                const id = `${idleId}-${m.id}`;
                return (
                  <div key={m.id} className="flex items-center gap-2">
                    <Checkbox
                      id={id}
                      checked={checked}
                      disabled={last}
                      onCheckedChange={(v) => toggleMethod(m.id, v === true)}
                    />
                    <Label htmlFor={id} className="font-normal">
                      {m.label}
                    </Label>
                  </div>
                );
              })}
            </div>
            {!draft.methods.includes("password") && (
              <Notice tone="warning" title="Password sign-in will be refused">
                This applies to everyone, admins and the workspace owner
                included. Make sure you can sign in with Google before you save.
                API keys keep working.
              </Notice>
            )}
            {!draft.methods.includes("google") && (
              <Notice tone="warning" title="Google sign-in will be refused">
                Everyone except the workspace owner will need their email and
                password.
              </Notice>
            )}
          </div>
        </SettingRow>

        <SettingRow
          label="IP allowlist"
          htmlFor={listId}
          description={
            <span id={listHelpId}>
              One IP address or CIDR range per line, up to {MAX_ALLOWLIST}.
              Leave empty to allow any network. Members and their API keys
              outside these ranges are refused on their next request. Admins are
              exempt, so a wrong list can't lock you out of this page.
            </span>
          }
          status={
            listError
              ? { state: "error", error: listError, retry: null }
              : undefined
          }
          stacked
        >
          <div className="grid w-full gap-1.5">
            <Textarea
              id={listId}
              rows={4}
              spellCheck={false}
              autoComplete="off"
              aria-describedby={listHelpId}
              aria-invalid={!!listError || undefined}
              placeholder={"203.0.113.0/24\n198.51.100.7"}
              className="font-mono text-sm"
              value={draft.allowlist}
              onChange={(e) =>
                setDraft((d) => ({ ...d, allowlist: e.target.value }))
              }
            />
            {list.entries.length > 0 && !listError && (
              <p className="m-0 text-sm text-muted-foreground">
                {list.entries.length}{" "}
                {list.entries.length === 1 ? "range" : "ranges"}. Hoja can't see
                the address you're on from here, so check that the list covers
                your office and VPN.
              </p>
            )}
          </div>
        </SettingRow>

        <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-2 p-(--ds-settings-row-padding)">
          <div className="mr-auto min-w-0">
            {save.error ? (
              <SaveStatusLine
                status={{ state: "error", error: save.error, retry: null }}
              />
            ) : savedStatus.state === "saved" ? (
              <SaveStatusLine status={savedStatus} />
            ) : (
              dirty && (
                <p className="m-0 text-sm text-muted-foreground">
                  You have unsaved changes.
                </p>
              )
            )}
          </div>
          {dirty && (
            <Button
              disabled={save.busy}
              onClick={() => {
                setDraft(draftFrom(saved));
                save.clearError();
              }}
            >
              Discard changes
            </Button>
          )}
          <Button
            type="submit"
            variant="primary"
            loading={save.busy}
            disabled={!dirty || blocked}
          >
            Save security policy
          </Button>
        </div>
      </SettingsSection>

      <ConfirmAction
        open={confirming}
        onOpenChange={setConfirming}
        destructive={false}
        title="Save a stricter security policy?"
        description={[
          ...newlyOff.map((m) =>
            m === "password"
              ? "Password sign-in will be refused for everyone, admins and the owner included."
              : "Google sign-in will be refused for everyone except the workspace owner.",
          ),
          listChanged && list.entries.length > 0
            ? `Members will only be let in from ${list.entries.length} ${list.entries.length === 1 ? "range" : "ranges"} (${list.entries.slice(0, 3).join(", ")}${list.entries.length > 3 ? ", …" : ""}).`
            : "",
          "Anyone this refuses is signed out on their next request.",
        ]
          .filter(Boolean)
          .join(" ")}
        confirmLabel="Save security policy"
        onConfirm={async () => {
          save.clearError();
          await commit();
        }}
      />
    </form>
  );
}

function ConnectSection({
  saved,
  onSaved,
}: {
  saved: Policy;
  onSaved: (p: Policy) => void;
}) {
  const id = useId();
  const [confirmOff, setConfirmOff] = useState(false);
  const connect = useSetting({
    value: saved.connectEnabled,
    save: async (next) =>
      onSaved(await savePolicy({ ...savedInput(saved), connectEnabled: next })),
  });
  return (
    <SettingsSection title="Hoja Connect">
      <SettingRow
        label="Allow Hoja Connect"
        htmlFor={id}
        description="Lets agent runners share a port from their machine, such as a running app preview, with this workspace. Turning it off stops runners from opening new ones."
        status={connect.status}
      >
        <Switch
          id={id}
          checked={connect.value}
          onCheckedChange={(on) => {
            if (on) void connect.set(true);
            else setConfirmOff(true);
          }}
        />
      </SettingRow>
      <ConfirmAction
        open={confirmOff}
        onOpenChange={setConfirmOff}
        title="Turn off Hoja Connect?"
        description="Agent runners in this workspace can't open new Connect exposures until an admin turns it back on. Work that needs a shared preview will fail."
        confirmLabel="Turn off Connect"
        onConfirm={() => connect.set(false)}
      />
    </SettingsSection>
  );
}
