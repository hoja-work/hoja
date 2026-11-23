import * as React from "react";
import { cn } from "cn";
import {
  Dialog as DialogPrimitive,
  AlertDialog as AlertPrimitive,
} from "radix-ui";
import { X } from "lucide-react";
import { buttonClass } from "./button";

const overlay =
  "fixed inset-0 z-50 bg-black/40 state-open:animate-fade-in state-closed:animate-fade-out";
const panel =
  "fixed top-1/2 left-1/2 z-50 grid max-h-[calc(100dvh-32px)] w-[calc(100vw-32px)] max-w-lg -translate-x-1/2 -translate-y-1/2 gap-4 overflow-y-auto rounded-xl border border-line bg-popover p-5 text-popover-foreground shadow-2xl outline-none state-open:animate-pop-in state-closed:animate-pop-out";

export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export const DialogClose = DialogPrimitive.Close;

export function DialogContent({
  className,
  children,
  hideClose,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & {
  hideClose?: boolean;
}) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className={overlay} />
      <DialogPrimitive.Content
        data-slot="dialog-content"
        className={cn(panel, className)}
        {...props}
      >
        {children}
        {!hideClose && (
          <DialogPrimitive.Close
            className={buttonClass({
              variant: "ghost",
              size: "icon-sm",
              className: "absolute top-3 right-3 text-muted-foreground",
            })}
            aria-label="Close"
          >
            <X className="size-4" aria-hidden="true" />
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

export function DialogHeader({
  title,
  description,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
}) {
  return (
    <div className="grid gap-1 pr-8">
      <DialogPrimitive.Title className="text-lg font-semibold text-foreground">
        {title}
      </DialogPrimitive.Title>
      {description ? (
        <DialogPrimitive.Description className="text-base text-muted-foreground">
          {description}
        </DialogPrimitive.Description>
      ) : (
        <DialogPrimitive.Description className="sr-only">
          {typeof title === "string" ? title : "Dialog"}
        </DialogPrimitive.Description>
      )}
    </div>
  );
}

export function DialogFooter({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      className={cn(
        "flex flex-col-reverse gap-2 sm:flex-row sm:justify-end",
        className,
      )}
      {...props}
    />
  );
}

/* Confirmation for consequential actions. Focus lands on Cancel, Escape cancels,
 * and the overlay does not dismiss (a stray click must never confirm or lose context). */
export const AlertDialog = AlertPrimitive.Root;
export const AlertDialogTrigger = AlertPrimitive.Trigger;

export function AlertDialogContent({
  title,
  description,
  children,
  confirmLabel,
  onConfirm,
  destructive,
  busy,
  confirmDisabled,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  children?: React.ReactNode;
  confirmLabel: string;
  onConfirm: (e: React.MouseEvent) => void;
  destructive?: boolean;
  busy?: boolean;
  confirmDisabled?: boolean;
  className?: string;
}) {
  return (
    <AlertPrimitive.Portal>
      <AlertPrimitive.Overlay className={overlay} />
      <AlertPrimitive.Content
        data-slot="alert-dialog-content"
        className={cn(panel, "max-w-md", className)}
      >
        <div className="grid gap-1">
          <AlertPrimitive.Title className="text-lg font-semibold text-foreground">
            {title}
          </AlertPrimitive.Title>
          <AlertPrimitive.Description
            className={
              description ? "text-base text-muted-foreground" : "sr-only"
            }
          >
            {description || (typeof title === "string" ? title : "Confirm")}
          </AlertPrimitive.Description>
        </div>
        {children}
        <DialogFooter>
          <AlertPrimitive.Cancel className={buttonClass({})}>
            Cancel
          </AlertPrimitive.Cancel>
          <AlertPrimitive.Action
            className={buttonClass({
              variant: destructive ? "destructive" : "primary",
            })}
            disabled={busy || confirmDisabled}
            aria-busy={busy || undefined}
            onClick={onConfirm}
          >
            {confirmLabel}
          </AlertPrimitive.Action>
        </DialogFooter>
      </AlertPrimitive.Content>
    </AlertPrimitive.Portal>
  );
}
