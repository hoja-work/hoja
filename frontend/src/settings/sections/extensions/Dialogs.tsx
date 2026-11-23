import * as React from "react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
} from "../../../components/ui/dialog";
import { Button } from "../../../components/ui/button";
import { Badge, CopyField, Notice } from "../../ui";
import type { Extension } from "./api";

function when(iso?: string | null) {
  if (!iso) return "Unknown";
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? "Unknown"
    : d.toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      });
}

const HOOK_LABEL: Record<string, string> = {
  "issue.beforeCreate": "Before a task is created",
  "issue.beforeUpdate": "Before a task is changed",
};

/* Credentials are returned once by extensionInstall and never again; this dialog is the
 * only place they appear, and closing it forgets them. */
export function CredentialsDialog({
  credentials,
  onDone,
}: {
  credentials: {
    name: string;
    apiKey: string | null;
    signingSecret: string | null;
  } | null;
  onDone: () => void;
}) {
  return (
    <Dialog open={!!credentials} onOpenChange={(o) => !o && onDone()}>
      <DialogContent
        hideClose
        onInteractOutside={(e) => e.preventDefault()}
        className="max-w-xl"
      >
        <div className="grid gap-4">
          <DialogHeader
            title={`${credentials?.name ?? "Extension"} is installed`}
            description="Copy these into the extension's secret store now. They are shown once and can't be viewed again. Hoja keeps only a hash of the API key."
          />
          {credentials?.apiKey ? (
            <div className="grid gap-1.5">
              <span className="text-sm font-medium">API key</span>
              <CopyField
                label="Extension API key"
                value={credentials.apiKey}
                secret
              />
            </div>
          ) : (
            <Notice tone="warning" title="No API key was returned">
              Uninstall and install again to get one.
            </Notice>
          )}
          {credentials?.signingSecret ? (
            <div className="grid gap-1.5">
              <span className="text-sm font-medium">Signing secret</span>
              <CopyField
                label="Extension signing secret"
                value={credentials.signingSecret}
                secret
              />
              <p className="m-0 text-sm text-muted-foreground">
                Use it to verify that requests really come from Hoja.
              </p>
            </div>
          ) : (
            <Notice tone="warning" title="No signing secret was returned">
              Uninstall and install again to get one.
            </Notice>
          )}
          <DialogFooter>
            <Button variant="primary" onClick={onDone}>
              I've saved them
            </Button>
          </DialogFooter>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Block({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="grid gap-2">
      <h3 className="m-0 text-sm font-medium text-foreground">{title}</h3>
      {children}
    </section>
  );
}

function List({ children }: { children: React.ReactNode }) {
  return (
    <ul className="m-0 grid list-none divide-y divide-line rounded-lg border border-line p-0">
      {children}
    </ul>
  );
}

export function DetailsDialog({
  extension: ext,
  onOpenChange,
}: {
  extension: Extension | null;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={!!ext} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-dvh max-w-2xl overflow-y-auto">
        {ext && (
          <div className="grid gap-5">
            <DialogHeader
              title={ext.name}
              description={`${ext.key} · v${ext.version} · ${ext.access === "read_write" ? "Read and write" : "Read only"}`}
            />
            <Block title="Endpoint">
              <CopyField label="Extension URL" value={ext.url} />
            </Block>
            <dl className="m-0 grid grid-cols-2 gap-4 text-sm">
              <div className="grid gap-0.5">
                <dt className="text-muted-foreground">Acts as</dt>
                <dd className="m-0 truncate">
                  {ext.botUser?.name || ext.botUser?.id || "No bot"}
                </dd>
              </div>
              <div className="grid gap-0.5">
                <dt className="text-muted-foreground">Last changed</dt>
                <dd className="m-0">{when(ext.updatedAt)}</dd>
              </div>
            </dl>
            <Block title="Hooks">
              {ext.hooks.length ? (
                <List>
                  {ext.hooks.map((h) => (
                    <li
                      key={h.key}
                      className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm"
                    >
                      <span className="font-medium">{h.key}</span>
                      <span className="text-muted-foreground">
                        {HOOK_LABEL[h.on] ?? h.on}
                      </span>
                      <span className="ml-auto">
                        {h.failurePolicy === "block" ? (
                          <Badge tone="danger">
                            Blocks the change if it fails
                          </Badge>
                        ) : (
                          <Badge>Lets the change through if it fails</Badge>
                        )}
                      </span>
                    </li>
                  ))}
                </List>
              ) : (
                <p className="m-0 text-sm text-muted-foreground">
                  No hooks. Hoja never waits on this extension before saving a
                  task.
                </p>
              )}
            </Block>
            <Block title="Actions">
              {ext.actions.length ? (
                <List>
                  {ext.actions.map((a) => (
                    <li
                      key={a.id}
                      className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm"
                    >
                      <span className="font-medium">{a.title}</span>
                      <span className="text-muted-foreground">
                        In the task menu
                      </span>
                    </li>
                  ))}
                </List>
              ) : (
                <p className="m-0 text-sm text-muted-foreground">
                  No actions. People can't run this extension from a task.
                </p>
              )}
            </Block>
            {ext.failures.length > 0 && (
              <Block title="Recent failures">
                <List>
                  {ext.failures.slice(0, 5).map((f, i) => (
                    <li
                      key={`${f.at}-${i}`}
                      className="grid gap-0.5 px-3 py-2 text-sm"
                    >
                      <span className="flex flex-wrap gap-x-3">
                        <span className="font-medium">
                          {f.key ? `${f.kind} · ${f.key}` : f.kind}
                        </span>
                        {typeof f.httpStatus === "number" && (
                          <span className="text-muted-foreground">
                            HTTP {f.httpStatus}
                          </span>
                        )}
                        <span className="ml-auto text-muted-foreground">
                          {when(f.at)}
                        </span>
                      </span>
                      {f.message && (
                        <span className="text-muted-foreground wrap-anywhere">
                          {f.message}
                        </span>
                      )}
                    </li>
                  ))}
                </List>
              </Block>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
