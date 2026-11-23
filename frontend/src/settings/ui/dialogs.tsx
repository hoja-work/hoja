import * as React from "react";
import {
  AlertDialog,
  AlertDialogContent,
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
} from "../../components/ui/dialog";
import { Button } from "../../components/ui/button";
import { Input, Label } from "../../components/ui/input";
import { errorMessage } from "./useSetting";
import { Notice } from "./layout";

/* Confirm a consequential action. With `typeToConfirm`, the person must type the exact
 * name first (deleting a workspace, a team, revoking every key). The dialog stays open on
 * failure and shows why; it closes only once the action actually succeeded. */
export function ConfirmAction({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  destructive = true,
  typeToConfirm,
  onConfirm,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: React.ReactNode;
  confirmLabel: string;
  destructive?: boolean;
  typeToConfirm?: string;
  onConfirm: () => Promise<unknown>;
  /** Extra content above the confirmation (a list of consequences, an option). */
  children?: React.ReactNode;
}) {
  const [typed, setTyped] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const inputId = React.useId();
  React.useEffect(() => {
    if (open) {
      setTyped("");
      setError(null);
    }
  }, [open]);
  const blocked = !!typeToConfirm && typed.trim() !== typeToConfirm;
  return (
    <AlertDialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <AlertDialogContent
        title={title}
        description={description}
        confirmLabel={busy ? "Working…" : confirmLabel}
        destructive={destructive}
        busy={busy}
        confirmDisabled={blocked}
        onConfirm={async (e) => {
          e.preventDefault();
          if (blocked) return;
          setBusy(true);
          setError(null);
          try {
            await onConfirm();
            onOpenChange(false);
          } catch (err) {
            setError(errorMessage(err));
          } finally {
            setBusy(false);
          }
        }}
      >
        {children}
        {typeToConfirm && (
          <div className="grid gap-1.5">
            <Label
              htmlFor={inputId}
              className="text-sm font-normal text-muted-foreground"
            >
              Type{" "}
              <span className="font-medium text-foreground">
                {typeToConfirm}
              </span>{" "}
              to confirm
            </Label>
            <Input
              id={inputId}
              autoComplete="off"
              spellCheck={false}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
            />
          </div>
        )}
        {error && <Notice tone="error">{error}</Notice>}
      </AlertDialogContent>
    </AlertDialog>
  );
}

/* Create / edit a resource. Submit runs `onSubmit`; the dialog closes only on success and
 * keeps the typed values on failure. Enter submits; Escape cancels. */
export function FormDialog({
  open,
  onOpenChange,
  title,
  description,
  submitLabel,
  onSubmit,
  children,
  canSubmit = true,
  wide,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: React.ReactNode;
  submitLabel: string;
  onSubmit: () => Promise<unknown>;
  children: React.ReactNode;
  canSubmit?: boolean;
  wide?: boolean;
}) {
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  React.useEffect(() => {
    if (open) setError(null);
  }, [open]);
  return (
    <Dialog open={open} onOpenChange={(o) => !busy && onOpenChange(o)}>
      <DialogContent className={wide ? "max-w-2xl" : undefined}>
        <form
          className="grid gap-4"
          onSubmit={async (e) => {
            e.preventDefault();
            if (!canSubmit || busy) return;
            setBusy(true);
            setError(null);
            try {
              await onSubmit();
              onOpenChange(false);
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        >
          <DialogHeader title={title} description={description} />
          <div className="grid gap-4">{children}</div>
          {error && <Notice tone="error">{error}</Notice>}
          <DialogFooter>
            <Button onClick={() => onOpenChange(false)} disabled={busy}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              loading={busy}
              disabled={!canSubmit}
            >
              {submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Label + control + help/error, stacked, for forms inside dialogs. */
export function Field({
  label,
  htmlFor,
  help,
  error,
  children,
  optional,
}: {
  label: string;
  htmlFor: string;
  help?: React.ReactNode;
  error?: string | null;
  children: React.ReactNode;
  optional?: boolean;
}) {
  return (
    <div className="grid gap-1.5" data-settings-field="">
      <Label htmlFor={htmlFor} className="text-sm">
        {label}
        {optional && (
          <span className="ml-1 font-normal text-muted-foreground">
            (optional)
          </span>
        )}
      </Label>
      {children}
      {error ? (
        <p role="alert" className="m-0 text-sm text-destructive">
          {error}
        </p>
      ) : (
        help && <p className="m-0 text-sm text-muted-foreground">{help}</p>
      )}
    </div>
  );
}

/** A destructive-actions card at the bottom of a page. */
export function DangerZone({ children }: { children: React.ReactNode }) {
  return (
    <section
      data-settings-danger=""
      aria-labelledby="danger-zone"
      className="grid gap-3"
    >
      <h2
        id="danger-zone"
        className="m-0 text-(length:--ds-settings-section-size) leading-(--ds-settings-section-line) font-(--ds-settings-section-weight) text-foreground"
      >
        Danger zone
      </h2>
      <div className="divide-y divide-destructive/20 overflow-hidden rounded-(--ds-settings-row-radius) border border-destructive/30 bg-card">
        {children}
      </div>
    </section>
  );
}
