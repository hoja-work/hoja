import * as React from "react";
import { cn } from "cn";
import { Building2, ExternalLink, Lock, Plus } from "lucide-react";
import { Button } from "../../../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
} from "../../../components/ui/dialog";
import { Input } from "../../../components/ui/input";
import {
  Badge,
  ConfirmAction,
  Field,
  FormDialog,
  Notice,
  errorMessage,
} from "../../ui";
import {
  grafanaRequest,
  revokeAccount,
  type ConnectorAccount,
  type Provider,
  type RevokeResult,
} from "./providers";

/* Local building blocks for the Connectors gallery. Kept out of settings/ui because only this
 * screen draws provider rows; see the "Proposals" note in the HOJA-734 report. */

export type RowState =
  | { kind: "connected"; label: string }
  | { kind: "attention"; label: string }
  | { kind: "idle"; label: string }
  | { kind: "unavailable"; label: string };

export function ProviderIcon({
  icon: Icon,
  tint,
  size = "md",
}: {
  icon: React.ComponentType<{ className?: string }>;
  tint: string;
  size?: "md" | "sm";
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "grid flex-none place-items-center rounded-lg border border-line",
        size === "md" ? "size-10" : "size-8",
        tint,
      )}
    >
      <Icon className={size === "md" ? "size-5" : "size-4"} />
    </span>
  );
}

const DOT: Record<RowState["kind"], string> = {
  connected: "bg-emerald-500",
  attention: "bg-amber-500",
  idle: "border border-muted-foreground/60 bg-transparent",
  unavailable: "bg-muted-foreground/40",
};

export function StateLine({ state }: { state: RowState }) {
  return (
    <span
      data-connector-state={state.kind}
      className={cn(
        "flex min-w-0 items-center gap-1.5 text-sm",
        state.kind === "attention"
          ? "text-foreground"
          : state.kind === "connected"
            ? "text-foreground"
            : "text-muted-foreground",
      )}
    >
      <span
        aria-hidden="true"
        className={cn("size-2 flex-none rounded-full", DOT[state.kind])}
      />
      <span className="truncate">{state.label}</span>
    </span>
  );
}

/** One provider in the gallery: icon, name, state, what it reads, one primary action. */
export function ProviderRow({
  provider,
  name,
  icon,
  state,
  summary,
  action,
  locked,
}: {
  provider: string;
  name: string;
  icon: React.ReactNode;
  state: RowState;
  summary: React.ReactNode;
  action?: React.ReactNode;
  locked?: string;
}) {
  const id = React.useId();
  return (
    <div
      role="group"
      aria-labelledby={id}
      data-provider={provider}
      className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-3 px-(--ds-settings-row-padding) py-3.5"
    >
      {icon}
      <div className="grid min-w-0 flex-1 basis-48 gap-0.5">
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
          <span id={id} className="text-base font-medium text-foreground">
            {name}
          </span>
          {state.kind === "attention" && (
            <Badge tone="danger">Needs attention</Badge>
          )}
        </span>
        <StateLine state={state} />
        <p className="m-0 text-sm leading-relaxed text-muted-foreground">
          {summary}
        </p>
        {locked && (
          <p className="m-0 flex items-center gap-1 text-sm text-muted-foreground">
            <Lock className="size-3 flex-none" aria-hidden="true" />
            {locked}
          </p>
        )}
      </div>
      {action && <div className="ml-auto flex flex-none gap-2">{action}</div>}
    </div>
  );
}

/** Full-page OAuth redirect, styled as a button. */
export function ConnectLink({
  href,
  children,
  variant = "secondary",
  label,
}: {
  href: string;
  children: React.ReactNode;
  variant?: "primary" | "secondary" | "ghost";
  label?: string;
}) {
  return (
    <Button asChild variant={variant}>
      <a href={href} aria-label={label}>
        {children}
      </a>
    </Button>
  );
}

function accountMeta(a: ConnectorAccount): string {
  const parts = [a.scope === "org" ? "Shared with the workspace" : "Personal"];
  if (a.externalUrl) parts.push(a.externalUrl);
  if (a.capabilities?.meet) parts.push("Meet");
  if (a.capabilities?.chat) parts.push("Chat");
  return parts.join(" · ");
}

/** A connected account with its Disconnect confirmation. */
export function AccountItem({
  account,
  provider,
  canDisconnect,
  lockedReason,
  onDisconnected,
}: {
  account: ConnectorAccount;
  provider: Provider;
  canDisconnect: boolean;
  lockedReason?: string;
  onDisconnected: (r: RevokeResult, a: ConnectorAccount) => void;
}) {
  const [confirm, setConfirm] = React.useState(false);
  const shared = account.scope === "org";
  return (
    <li
      data-account={account.externalAccount}
      className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 py-3"
    >
      <span
        aria-hidden="true"
        className="grid size-8 flex-none place-items-center rounded-full bg-subtle text-sm font-medium text-muted-foreground"
      >
        {account.externalAccount.slice(0, 1).toUpperCase()}
      </span>
      <span className="grid min-w-0 flex-1 basis-40 gap-0.5">
        <span className="truncate text-base font-medium">
          {account.externalAccount}
        </span>
        <span className="truncate text-sm text-muted-foreground">
          {accountMeta(account)}
        </span>
      </span>
      {canDisconnect ? (
        <Button
          variant="destructive-ghost"
          size="sm"
          className="ml-auto"
          onClick={() => setConfirm(true)}
          aria-label={`Disconnect ${account.externalAccount}`}
        >
          Disconnect
        </Button>
      ) : (
        lockedReason && (
          <span className="ml-auto flex items-center gap-1 text-sm text-muted-foreground">
            <Lock className="size-3" aria-hidden="true" />
            {lockedReason}
          </span>
        )
      )}
      <ConfirmAction
        open={confirm}
        onOpenChange={setConfirm}
        title={`Disconnect ${account.externalAccount}?`}
        description={
          <>
            {shared
              ? `Agents across the workspace stop reading ${provider.name} through this shared account. `
              : `Your agents stop reading ${provider.name} through this ${provider.noun}. `}
            {provider.disconnectNote}
          </>
        }
        confirmLabel="Disconnect"
        onConfirm={async () => {
          const r = await revokeAccount(account.id);
          onDisconnected(r, account);
        }}
      />
    </li>
  );
}

/** Manage one provider: its accounts, add-ons and how to add another. */
export function ManageDialog({
  open,
  onOpenChange,
  provider,
  title,
  description,
  accounts,
  canDisconnect,
  lockedReason,
  onDisconnected,
  extra,
  addAnother,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  provider: Provider;
  title: string;
  description: string;
  accounts: ConnectorAccount[];
  canDisconnect: boolean;
  lockedReason?: string;
  onDisconnected: (r: RevokeResult, a: ConnectorAccount) => void;
  extra?: React.ReactNode;
  addAnother?: React.ReactNode;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <div className="flex items-start gap-3 pr-8">
          <ProviderIcon icon={provider.icon} tint={provider.tint} size="sm" />
          <DialogHeader title={title} description={description} />
        </div>
        <section aria-label="Connected accounts" className="grid gap-1">
          <h3 className="m-0 text-sm font-medium text-muted-foreground">
            Connected
          </h3>
          {accounts.length ? (
            <ul className="m-0 grid list-none divide-y divide-line p-0">
              {accounts.map((a) => (
                <AccountItem
                  key={a.id}
                  account={a}
                  provider={provider}
                  canDisconnect={canDisconnect}
                  lockedReason={lockedReason}
                  onDisconnected={onDisconnected}
                />
              ))}
            </ul>
          ) : (
            <p className="m-0 py-3 text-sm text-muted-foreground">
              Nothing connected.
            </p>
          )}
        </section>
        {extra}
        <DialogFooter>
          {addAnother}
          <Button variant="primary" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** An add-on grant (Google Meet, Chat, GitHub private repositories) inside a manage dialog. */
export function AddonRow({
  title,
  description,
  granted,
  href,
  actionLabel,
  external,
}: {
  title: string;
  description: string;
  granted?: boolean;
  href?: string | null;
  actionLabel: string;
  external?: boolean;
}) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-line p-3">
      <span className="grid min-w-0 flex-1 basis-48 gap-0.5">
        <span className="text-base font-medium">{title}</span>
        <span className="text-sm leading-relaxed text-muted-foreground">
          {description}
        </span>
      </span>
      {granted ? (
        <Badge>Granted</Badge>
      ) : (
        href && (
          <Button asChild size="sm">
            <a
              href={href}
              {...(external ? { target: "_blank", rel: "noreferrer" } : {})}
            >
              {external ? (
                <ExternalLink className="size-3.5" aria-hidden="true" />
              ) : (
                <Plus className="size-3.5" aria-hidden="true" />
              )}
              {actionLabel}
            </a>
          </Button>
        )
      )}
    </div>
  );
}

/** Grafana has no OAuth: the instance address and a Viewer service-account token. */
export function GrafanaDialog({
  open,
  onOpenChange,
  onConnected,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onConnected: (instance: string) => void;
}) {
  const [url, setUrl] = React.useState("");
  const [token, setToken] = React.useState("");
  const [testing, setTesting] = React.useState(false);
  const [test, setTest] = React.useState<{
    ok: boolean;
    text: string;
  } | null>(null);
  const [urlError, setUrlError] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (open) {
      setUrl("");
      setToken("");
      setTest(null);
      setUrlError(null);
    }
  }, [open]);
  const check = () => {
    const u = url.trim();
    if (!/^https?:\/\/[^\s/]+/i.test(u)) {
      setUrlError("Enter the full address, starting with https://");
      return null;
    }
    setUrlError(null);
    return { url: u, token: token.trim() };
  };
  return (
    <FormDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Connect Grafana"
      description="Agents get read-only access to one Grafana instance. Queries are bounded and nothing is changed."
      submitLabel="Connect"
      canSubmit={!!url.trim() && !!token.trim()}
      onSubmit={async () => {
        const body = check();
        if (!body) throw new Error("Check the instance address.");
        const r = await grafanaRequest("connect", body);
        setToken("");
        onConnected(r.instance);
      }}
    >
      <Field
        label="Instance address"
        htmlFor="grafana-url"
        help="For example https://example.grafana.net"
        error={urlError}
      >
        <Input
          id="grafana-url"
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="https://example.grafana.net"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setTest(null);
          }}
          aria-invalid={!!urlError || undefined}
        />
      </Field>
      <Field
        label="Service account token"
        htmlFor="grafana-token"
        help="Create it in Grafana under Administration > Users and access > Service accounts, with the Viewer role. It is stored encrypted and never shown again."
      >
        <Input
          id="grafana-token"
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder="glsa_…"
          value={token}
          onChange={(e) => {
            setToken(e.target.value);
            setTest(null);
          }}
        />
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        <Button
          size="sm"
          loading={testing}
          disabled={!url.trim() || !token.trim()}
          onClick={async () => {
            const body = check();
            if (!body) return;
            setTesting(true);
            setTest(null);
            try {
              const r = await grafanaRequest("validate", body);
              setTest({
                ok: true,
                text: `Reached ${r.instance} as ${r.account}.`,
              });
            } catch (e) {
              setTest({ ok: false, text: errorMessage(e) });
            } finally {
              setTesting(false);
            }
          }}
        >
          Test connection
        </Button>
        {test?.ok && (
          <span role="status" className="text-sm text-muted-foreground">
            {test.text}
          </span>
        )}
      </div>
      {test && !test.ok && <Notice tone="error">{test.text}</Notice>}
    </FormDialog>
  );
}

export const WorkspaceIcon = Building2;
