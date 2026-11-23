import { useId, useMemo, useState } from "react";
import { Bot, KeyRound, Plus, Search, Trash2, User } from "lucide-react";
import { Button } from "../../../components/ui/button";
import { Input } from "../../../components/ui/input";
import { DropdownMenuItem } from "../../../components/ui/menu";
import { Select } from "../../../components/ui/select";
import { Segmented } from "../../../components/ui/switch";
import {
  Badge,
  ConfirmAction,
  CopyField,
  EmptyState,
  Field,
  FormDialog,
  Loadable,
  ResourceRow,
  SettingsCard,
  SettingsSection,
  gql,
  useResource,
} from "../../ui";
import {
  API_KEY_FIELDS,
  formatDay,
  formatWhen,
  keyStatus,
  type ApiKey,
  type KeyStatus,
} from "./shared";

const NAME_MAX = 80;
const EXPIRY = [
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "365", label: "1 year" },
  { value: "never", label: "Never" },
];
/* The key this browser is signed in with, if any (the token's last four characters are the
 * key's hint). Revoking it signs this browser out, so it is marked and the confirm says so. */
function browserKeyHint(): string | null {
  try {
    const t = localStorage.getItem("hoja_token");
    return t && t !== "session" ? t.slice(-4) : null;
  } catch {
    return null;
  }
}
const isBrowserKey = (k: { hint?: string | null; revokedAt?: string | null }) =>
  !!k.hint && !k.revokedAt && k.hint === browserKeyHint();

const STATUS_LABEL: Record<KeyStatus, string> = {
  active: "Active",
  expired: "Expired",
  revoked: "Revoked",
};

function CreateKeyDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (label: string, secret: string) => void;
}) {
  const nameId = useId();
  const actorId = useId();
  const accessId = useId();
  const expiryId = useId();
  const [label, setLabel] = useState("");
  const [actorKind, setActorKind] = useState("agent");
  const [access, setAccess] = useState("read_write");
  const [expiry, setExpiry] = useState("90");
  const trimmed = label.trim();
  const tooLong = trimmed.length > NAME_MAX;
  return (
    <FormDialog
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) {
          setLabel("");
          setActorKind("agent");
          setAccess("read_write");
          setExpiry("90");
        }
      }}
      title="Create API key"
      description="Choose whether this key is used by an agent on your behalf or directly by you."
      submitLabel="Create key"
      canSubmit={!!trimmed && !tooLong}
      onSubmit={async () => {
        const expiresAt =
          expiry === "never"
            ? null
            : new Date(Date.now() + Number(expiry) * 86400000).toISOString();
        const d = await gql<{
          apiKeyCreate: { success: boolean; secret: string | null } | null;
        }>(
          "mutation($label:String!,$access:String!,$expiresAt:DateTime,$actorKind:String!){apiKeyCreate(label:$label,access:$access,expiresAt:$expiresAt,actorKind:$actorKind){success apiKey{id} secret}}",
          { label: trimmed, access, expiresAt, actorKind },
        );
        if (!d.apiKeyCreate?.success || !d.apiKeyCreate.secret)
          throw new Error(
            `The key wasn't created. Use a name of 1–${NAME_MAX} characters and try again.`,
          );
        onCreated(trimmed, d.apiKeyCreate.secret);
        setLabel("");
        setActorKind("agent");
        setAccess("read_write");
        setExpiry("90");
      }}
    >
      <Field
        label="Name"
        htmlFor={nameId}
        help="Name it after where it's used, so you know what breaks if you revoke it."
        error={tooLong ? `Use ${NAME_MAX} characters or fewer.` : null}
      >
        <Input
          id={nameId}
          value={label}
          autoComplete="off"
          placeholder="e.g. Claude Code or CI deploys"

          autoFocus
          onChange={(e) => setLabel(e.target.value)}
        />
      </Field>
      <Field
        label="Who will use this key?"
        htmlFor={actorId}
        help="Agent keys mark writes as an agent acting on your behalf, so SLAs and activity logs stay accurate."
      >
        <Select
          id={actorId}
          value={actorKind}
          onValueChange={setActorKind}
          className="sm:w-full"
          options={[
            {
              value: "agent",
              label: "An agent (on my behalf)",
              description:
                "Writes record actor_kind = agent with you as sponsor.",
            },
            {
              value: "human",
              label: "Just me (scripts or CLI)",
              description: "Writes record as coming directly from you.",
            },
          ]}
        />
      </Field>
      <Field
        label="Access"
        htmlFor={accessId}
        help="Access can't be changed later. Create a new key instead."
      >
        <Select
          id={accessId}
          value={access}
          onValueChange={setAccess}
          className="sm:w-full"
          options={[
            {
              value: "read_write",
              label: "Read and write",
              description: "View and change workspace data as you.",
            },
            {
              value: "read",
              label: "Read only",
              description: "View data. Every change is refused.",
            },
          ]}
        />
      </Field>
      <Field
        label="Expires after"
        htmlFor={expiryId}
        help={
          expiry === "never"
            ? "Stays valid until you revoke it."
            : `Stops working on ${formatDay(new Date(Date.now() + Number(expiry) * 86400000).toISOString())}.`
        }
      >
        <Select
          id={expiryId}
          value={expiry}
          onValueChange={setExpiry}
          className="sm:w-full"
          options={EXPIRY}
        />
      </Field>
    </FormDialog>
  );
}

export function ApiKeysSection() {
  const keys = useResource(() =>
    gql<{ myApiKeys: ApiKey[] }>(`{myApiKeys{${API_KEY_FIELDS}}}`).then(
      (d) => d.myApiKeys,
    ),
  );
  const [creating, setCreating] = useState(false);
  const [secret, setSecret] = useState<{ label: string; value: string } | null>(
    null,
  );
  const [revoking, setRevoking] = useState<ApiKey | null>(null);
  const [filter, setFilter] = useState<"active" | "inactive">("active");
  const [query, setQuery] = useState("");
  const searchId = useId();

  const all = useMemo(() => keys.data || [], [keys.data]);
  const counts = useMemo(() => {
    const now = Date.now();
    const active = all.filter((k) => keyStatus(k, now) === "active").length;
    return { active, inactive: all.length - active };
  }, [all]);

  const setKeyActorKind = async (k: ApiKey, nextKind: "agent" | "human") => {
    const d = await gql<{
      apiKeyUpdate: { success: boolean; apiKey: ApiKey | null } | null;
    }>(
      `mutation($id:String!,$actorKind:String!){apiKeyUpdate(id:$id,actorKind:$actorKind){success apiKey{${API_KEY_FIELDS}}}}`,
      { id: k.id, actorKind: nextKind },
    );
    if (d.apiKeyUpdate?.success && d.apiKeyUpdate.apiKey) {
      const updated = d.apiKeyUpdate.apiKey;
      keys.setData((list) => list?.map((x) => (x.id === k.id ? updated : x)));
    }
  };

  return (
    <SettingsSection
      title="API keys"
      description="For scripts and automation that can't sign in through a browser. A key acts as you."
      bare
      action={
        <Button onClick={() => setCreating(true)}>
          <Plus className="size-3.5" aria-hidden="true" />
          Create API key
        </Button>
      }
    >
      {secret && (
        <section
          aria-label="Your new API key"
          data-api-key-secret=""
          className="grid gap-3 rounded-(--ds-settings-row-radius) border border-primary/40 bg-card p-(--ds-settings-row-padding) shadow-xs"
        >
          <div className="grid gap-0.5">
            <p className="m-0 text-base font-medium">
              Copy “{secret.label}” now
            </p>
            <p className="m-0 text-sm text-muted-foreground">
              This is the only time the full key is shown. Store it in a
              password manager or your secret store; it won't be shown again.
            </p>
          </div>
          <CopyField label="New API key" value={secret.value} secret />
          <div>
            <Button variant="primary" onClick={() => setSecret(null)}>
              I've saved it
            </Button>
          </div>
        </section>
      )}
      <Loadable resource={keys} rows={2}>
        {(data) => {
          const now = Date.now();
          const q = query.trim().toLowerCase();
          const visible = data.filter((k) => {
            const active = keyStatus(k, now) === "active";
            if ((filter === "active") !== active) return false;
            const setKeyActorKind = async (
              k: ApiKey,
              nextKind: "agent" | "human",
            ) => {
              const d = await gql<{
                apiKeyUpdate: {
                  success: boolean;
                  apiKey: ApiKey | null;
                } | null;
              }>(
                `mutation($id:String!,$actorKind:String!){apiKeyUpdate(id:$id,actorKind:$actorKind){success apiKey{${API_KEY_FIELDS}}}}`,
                { id: k.id, actorKind: nextKind },
              );
              if (d.apiKeyUpdate?.success && d.apiKeyUpdate.apiKey) {
                const updated = d.apiKeyUpdate.apiKey;
                keys.setData((list) =>
                  list?.map((x) => (x.id === k.id ? updated : x)),
                );
              }
            };

            return (
              !q ||
              (k.label || "").toLowerCase().includes(q) ||
              (k.hint || "").toLowerCase().includes(q)
            );
          });
          const setKeyActorKind = async (
            k: ApiKey,
            nextKind: "agent" | "human",
          ) => {
            const d = await gql<{
              apiKeyUpdate: { success: boolean; apiKey: ApiKey | null } | null;
            }>(
              `mutation($id:String!,$actorKind:String!){apiKeyUpdate(id:$id,actorKind:$actorKind){success apiKey{${API_KEY_FIELDS}}}}`,
              { id: k.id, actorKind: nextKind },
            );
            if (d.apiKeyUpdate?.success && d.apiKeyUpdate.apiKey) {
              const updated = d.apiKeyUpdate.apiKey;
              keys.setData((list) =>
                list?.map((x) => (x.id === k.id ? updated : x)),
              );
            }
          };

          return (
            <SettingsCard>
              {data.length > 0 && (
                <div className="flex flex-wrap items-center gap-2 p-(--ds-settings-row-padding) py-2.5">
                  <Segmented
                    label="Show keys"
                    className="flex-none"
                    value={filter}
                    onValueChange={(v) => setFilter(v as typeof filter)}
                    options={[
                      { value: "active", label: `Active (${counts.active})` },
                      {
                        value: "inactive",
                        label: `Inactive (${counts.inactive})`,
                      },
                    ]}
                  />
                  {data.length > 5 && (
                    <div className="relative min-w-40 flex-1">
                      <Search
                        className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
                        aria-hidden="true"
                      />
                      <Input
                        id={searchId}
                        type="search"
                        aria-label="Search API keys"
                        placeholder="Search by name or ending"
                        className="pl-8"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                      />
                    </div>
                  )}
                </div>
              )}
              {data.length === 0 ? (
                <EmptyState
                  icon={<KeyRound />}
                  title="No API keys yet"
                  description="Create one to connect a script or CI job. Interactive tools like the Hoja CLI sign in through your browser instead."
                  action={
                    <Button onClick={() => setCreating(true)}>
                      Create API key
                    </Button>
                  }
                />
              ) : visible.length === 0 ? (
                <p className="m-0 border-t border-line p-(--ds-settings-row-padding) text-sm text-muted-foreground">
                  {q
                    ? "No keys match this search."
                    : filter === "active"
                      ? "No active keys. Create one when a script needs access."
                      : "No revoked or expired keys."}
                </p>
              ) : (
                <ul aria-label="API keys" className="m-0 p-0">
                  {visible.map((k) => {
                    const status = keyStatus(k, now);
                    const name = k.label || "API key";
                    const setKeyActorKind = async (
                      k: ApiKey,
                      nextKind: "agent" | "human",
                    ) => {
                      const d = await gql<{
                        apiKeyUpdate: {
                          success: boolean;
                          apiKey: ApiKey | null;
                        } | null;
                      }>(
                        `mutation($id:String!,$actorKind:String!){apiKeyUpdate(id:$id,actorKind:$actorKind){success apiKey{${API_KEY_FIELDS}}}}`,
                        { id: k.id, actorKind: nextKind },
                      );
                      if (d.apiKeyUpdate?.success && d.apiKeyUpdate.apiKey) {
                        const updated = d.apiKeyUpdate.apiKey;
                        keys.setData((list) =>
                          list?.map((x) => (x.id === k.id ? updated : x)),
                        );
                      }
                    };

                    return (
                      <li key={k.id} className="list-none border-t border-line">
                        <ResourceRow
                          title={name}
                          badges={
                            <>
                              {k.actorKind === "agent" ? (
                                <Badge tone="strong">
                                  {k.agentName
                                    ? `Agent · ${k.agentName}`
                                    : "Agent"}
                                </Badge>
                              ) : k.actorKind === "human" ? (
                                <Badge>Personal</Badge>
                              ) : (
                                <Badge tone="neutral">Used by an agent?</Badge>
                              )}
                              <Badge>
                                {k.access === "read"
                                  ? "Read only"
                                  : "Read and write"}
                              </Badge>
                              {isBrowserKey(k) && (
                                <Badge tone="strong">This browser</Badge>
                              )}
                              {status !== "active" && (
                                <Badge
                                  tone={
                                    status === "revoked" ? "danger" : "neutral"
                                  }
                                >
                                  {STATUS_LABEL[status]}
                                </Badge>
                              )}
                            </>
                          }
                          meta={[
                            k.hint ? `Ends in …${k.hint}` : null,
                            `Last used ${formatWhen(k.lastUsedAt, "never")}`,
                            status === "revoked"
                              ? `Revoked ${formatWhen(k.revokedAt)}`
                              : k.expiresAt
                                ? `${status === "expired" ? "Expired" : "Expires"} ${formatWhen(k.expiresAt)}`
                                : "Never expires",
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                          menuLabel={`Actions for ${name}`}
                          actions={
                            status === "revoked" ? undefined : (
                              <>
                                {k.actorKind !== "agent" && (
                                  <DropdownMenuItem
                                    onSelect={() =>
                                      void setKeyActorKind(k, "agent")
                                    }
                                  >
                                    <Bot aria-hidden="true" />
                                    Mark as used by an agent
                                  </DropdownMenuItem>
                                )}
                                {k.actorKind !== "human" && (
                                  <DropdownMenuItem
                                    onSelect={() =>
                                      void setKeyActorKind(k, "human")
                                    }
                                  >
                                    <User aria-hidden="true" />
                                    Mark as personal
                                  </DropdownMenuItem>
                                )}
                                <DropdownMenuItem
                                  destructive
                                  onSelect={() => setRevoking(k)}
                                >
                                  <Trash2 aria-hidden="true" />
                                  Revoke…
                                </DropdownMenuItem>
                              </>
                            )
                          }
                        />
                      </li>
                    );
                  })}
                </ul>
              )}
            </SettingsCard>
          );
        }}
      </Loadable>

      <CreateKeyDialog
        open={creating}
        onOpenChange={setCreating}
        onCreated={(label, value) => {
          setSecret({ label, value });
          setFilter("active");
          void keys.reload();
        }}
      />
      <ConfirmAction
        open={!!revoking}
        onOpenChange={(o) => !o && setRevoking(null)}
        title={`Revoke “${revoking?.label || "API key"}”?`}
        description={
          revoking && isBrowserKey(revoking)
            ? "This browser is signed in with this key, so you will be signed out as soon as it is revoked. Scripts and jobs using it stop too. This can't be undone."
            : "Scripts and jobs using this key stop working immediately. This can't be undone; you can create a new key."
        }
        typeToConfirm={
          revoking && isBrowserKey(revoking)
            ? revoking.label || undefined
            : undefined
        }
        confirmLabel="Revoke key"
        onConfirm={async () => {
          if (!revoking) return;
          const d = await gql<{ apiKeyRevoke: boolean }>(
            "mutation($id:String!){apiKeyRevoke(id:$id)}",
            { id: revoking.id },
          );
          if (!d.apiKeyRevoke)
            throw new Error(
              "This key wasn't revoked. It may already be revoked; reload to check.",
            );
          const revokedAt = new Date().toISOString();
          const id = revoking.id;
          keys.setData((list) =>
            list?.map((x) => (x.id === id ? { ...x, revokedAt } : x)),
          );
        }}
      />
    </SettingsSection>
  );
}
