import * as React from "react";
import { Toast as ToastPrimitive } from "radix-ui";
import { cn } from "cn";
import { CircleAlert, CircleCheck, X } from "lucide-react";
import { buttonClass } from "./button";

/* Imperative toasts for outcomes that outlive the control that caused them: Undo after an
 * instant change, a failure the row can no longer show. Inline status stays the default;
 * a toast is for "you might have missed this", never for routine success. */
export type ToastInput = {
  title: string;
  description?: string;
  tone?: "neutral" | "error";
  action?: { label: string; onClick: () => void };
  durationMs?: number;
};
type Item = ToastInput & { id: number };

let items: Item[] = [];
let seq = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

export function toast(input: ToastInput) {
  const id = ++seq;
  items = [...items.slice(-2), { ...input, id }];
  emit();
  return id;
}
function dismiss(id: number) {
  items = items.filter((t) => t.id !== id);
  emit();
}

export function ToastHost() {
  const [, force] = React.useReducer((n: number) => n + 1, 0);
  React.useEffect(() => {
    listeners.add(force);
    return () => {
      listeners.delete(force);
    };
  }, []);
  return (
    <ToastPrimitive.Provider swipeDirection="down" duration={6000}>
      {items.map((t) => (
        <ToastPrimitive.Root
          key={t.id}
          data-slot="toast"
          data-tone={t.tone || "neutral"}
          duration={t.durationMs ?? (t.action ? 8000 : 5000)}
          onOpenChange={(open) => !open && dismiss(t.id)}
          className={cn(
            "pointer-events-auto flex w-full items-start gap-2.5 rounded-xl border border-line bg-popover p-3 text-popover-foreground shadow-xl state-open:animate-toast-in state-closed:animate-fade-out data-[swipe=move]:translate-y-(--radix-toast-swipe-move-y) data-[swipe=end]:animate-fade-out",
          )}
        >
          {t.tone === "error" ? (
            <CircleAlert
              className="mt-0.5 size-4 shrink-0 text-destructive"
              aria-hidden="true"
            />
          ) : (
            <CircleCheck
              className="mt-0.5 size-4 shrink-0 text-muted-foreground"
              aria-hidden="true"
            />
          )}
          <div className="grid min-w-0 flex-1 gap-0.5">
            <ToastPrimitive.Title className="text-base font-medium">
              {t.title}
            </ToastPrimitive.Title>
            {t.description && (
              <ToastPrimitive.Description className="text-sm text-muted-foreground">
                {t.description}
              </ToastPrimitive.Description>
            )}
          </div>
          {t.action && (
            <ToastPrimitive.Action
              altText={t.action.label}
              className={buttonClass({ size: "sm" })}
              onClick={t.action.onClick}
            >
              {t.action.label}
            </ToastPrimitive.Action>
          )}
          <ToastPrimitive.Close
            aria-label="Dismiss"
            className={buttonClass({
              variant: "ghost",
              size: "icon-sm",
              className: "-mt-1 -mr-1 text-muted-foreground",
            })}
          >
            <X className="size-3.5" aria-hidden="true" />
          </ToastPrimitive.Close>
        </ToastPrimitive.Root>
      ))}
      <ToastPrimitive.Viewport className="fixed bottom-4 left-1/2 z-60 flex w-full max-w-sm -translate-x-1/2 flex-col gap-2 px-4 outline-none" />
    </ToastPrimitive.Provider>
  );
}
