import * as React from "react";
import { cn } from "cn";
import {
  AlertTriangle,
  Check,
  Copy,
  Info,
  Loader2,
  Lock,
  MoreHorizontal,
  RotateCw,
} from "lucide-react";
import { Button } from "../../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "../../components/ui/menu";
import type { SaveStatus } from "./useSetting";

/* Settings composites. Every settings screen is built from these and the primitives in
 * components/ui — no screen writes its own header, row, card, empty or error markup.
 * Geometry comes from the measured --ds-settings-* tokens (design/tokens.css). */

export function SettingsPage({
  title,
  description,
  actions,
  children,
  wide,
}: {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  /** Tables (members) get a wider column; forms keep the measured 640px. */
  wide?: boolean;
}) {
  return (
    <section
      data-settings-page=""
      aria-labelledby="settings-page-title"
      className={cn(
        "mx-auto w-full min-w-0 px-(--ds-settings-column-padding) pt-6 pb-16 text-foreground shell-desktop:pt-16",
        wide ? "max-w-5xl" : "max-w-(--ds-settings-column-width)",
      )}
    >
      <header
        data-settings-page-heading=""
        className="mb-8 flex flex-wrap items-end justify-between gap-x-4 gap-y-3"
      >
        <div className="grid min-w-0 flex-1 basis-64 gap-1.5">
          <h1
            id="settings-page-title"
            className="m-0 text-(length:--ds-settings-title-size) leading-(--ds-settings-title-line) font-(--ds-settings-title-weight) tracking-tight text-foreground"
          >
            {title}
          </h1>
          {description && (
            <p className="m-0 text-base leading-relaxed text-muted-foreground">
              {description}
            </p>
          )}
        </div>
        {actions && <div className="flex flex-none gap-2">{actions}</div>}
      </header>
      <div className="grid gap-10">{children}</div>
    </section>
  );
}

export function SettingsSection({
  title,
  description,
  action,
  children,
  bare,
  id,
}: {
  title?: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
  children: React.ReactNode;
  /** Render children without the card (for lists that bring their own surface). */
  bare?: boolean;
  id?: string;
}) {
  const headingId = React.useId();
  return (
    <section
      data-settings-section=""
      id={id}
      aria-labelledby={title ? headingId : undefined}
      className="grid gap-3"
    >
      {(title || action) && (
        <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-2">
          <div className="grid min-w-0 flex-1 basis-56 gap-0.5">
            {title && (
              <h2
                id={headingId}
                className="m-0 text-(length:--ds-settings-section-size) leading-(--ds-settings-section-line) font-(--ds-settings-section-weight) text-foreground"
              >
                {title}
              </h2>
            )}
            {description && (
              <p className="m-0 text-sm leading-relaxed text-muted-foreground">
                {description}
              </p>
            )}
          </div>
          {action && <div className="flex flex-none gap-2">{action}</div>}
        </div>
      )}
      {bare ? children : <SettingsCard>{children}</SettingsCard>}
    </section>
  );
}

export function SettingsCard({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      data-settings-card=""
      className={cn(
        "min-w-0 divide-y divide-line overflow-hidden rounded-(--ds-settings-row-radius) border border-line bg-card shadow-xs",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** One setting: label + help on the left, control on the right; stacks on narrow screens. */
export function SettingRow({
  label,
  description,
  htmlFor,
  children,
  status,
  stacked,
  locked,
}: {
  label: React.ReactNode;
  description?: React.ReactNode;
  /** id of the control, so clicking the label focuses it. */
  htmlFor?: string;
  children?: React.ReactNode;
  status?: SaveStatus;
  /** Control goes under the label at every width (wide controls: textareas, pickers). */
  stacked?: boolean;
  /** Why this row is read-only for the viewer (e.g. "Only admins can change this"). */
  locked?: string;
}) {
  const descId = React.useId();
  const Label = htmlFor ? "label" : "div";
  return (
    <div
      data-setting-row=""
      className={cn(
        "flex min-h-(--ds-settings-row-height) min-w-0 p-(--ds-settings-row-padding)",
        // Compact controls (a switch, a short select) stay beside the label even on a phone;
        // a control that needs the width wraps under it instead of squeezing the text.
        stacked
          ? "flex-col gap-3"
          : "flex-wrap items-center justify-between gap-x-6 gap-y-3",
      )}
    >
      {/* basis-44 is a width only in a row; in the stacked column it would become a 176px height. */}
      <div className={cn("grid min-w-0 gap-0.5", !stacked && "flex-1 basis-44")}>
        <Label
          htmlFor={htmlFor}
          className="text-base font-medium text-foreground"
        >
          {label}
        </Label>
        {description && (
          <p
            id={descId}
            className="m-0 text-sm leading-relaxed text-muted-foreground"
          >
            {description}
          </p>
        )}
        {locked && (
          <p className="m-0 flex items-center gap-1 text-sm text-muted-foreground">
            <Lock className="size-3" aria-hidden="true" />
            {locked}
          </p>
        )}
        {status && stacked && <SaveStatusLine status={status} />}
      </div>
      {children && (
        <div
          data-setting-control=""
          className={cn(
            "flex min-w-0 items-center gap-2",
            stacked ? "w-full" : "relative max-w-full flex-none justify-end",
          )}
        >
          {/* Saving…/Saved hang under the control, in the row's padding: they take no layout
              space, so the row never moves under the pointer at any width. */}
          {status && !stacked && status.state !== "error" && (
            <span className="pointer-events-none absolute top-full right-0 mt-0.5 whitespace-nowrap text-xs">
              <SaveStatusLine status={status} />
            </span>
          )}
          {children}
        </div>
      )}
      {status && !stacked && status.state === "error" && (
        <div className="basis-full">
          <SaveStatusLine status={status} />
        </div>
      )}
    </div>
  );
}

/** Inline, polite, never-silent save feedback. Errors are an alert with Retry. */
export function SaveStatusLine({ status }: { status: SaveStatus }) {
  return (
    <div aria-live="polite" className="min-h-0 text-sm">
      {status.state === "saving" && (
        <span className="inline-flex items-center gap-1 text-muted-foreground">
          <Loader2
            className="size-3 animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
          Saving…
        </span>
      )}
      {status.state === "saved" && (
        <span
          data-save-status="saved"
          className="inline-flex items-center gap-1 text-muted-foreground"
        >
          <Check className="size-3" aria-hidden="true" />
          Saved
        </span>
      )}
      {status.state === "error" && status.error && (
        <span
          role="alert"
          data-save-status="error"
          className="inline-flex flex-wrap items-center gap-x-2 text-destructive"
        >
          {status.error}
          {status.retry && (
            <button
              type="button"
              onClick={status.retry}
              className="inline-flex items-center gap-1 font-medium underline underline-offset-2 hover:no-underline"
            >
              <RotateCw className="size-3" aria-hidden="true" />
              Retry
            </button>
          )}
        </span>
      )}
    </div>
  );
}

export function Notice({
  tone = "info",
  title,
  children,
  action,
}: {
  tone?: "info" | "warning" | "locked" | "error";
  title?: string;
  children?: React.ReactNode;
  action?: React.ReactNode;
}) {
  const Icon =
    tone === "warning" || tone === "error"
      ? AlertTriangle
      : tone === "locked"
        ? Lock
        : Info;
  return (
    <div
      role={tone === "error" ? "alert" : "note"}
      data-settings-notice={tone}
      className={cn(
        "flex items-start gap-2.5 rounded-(--ds-settings-row-radius) border p-3 text-base",
        tone === "error"
          ? "border-destructive/40 bg-destructive/5"
          : "border-line bg-subtle",
      )}
    >
      <Icon
        className={cn(
          "mt-0.5 size-4 flex-none",
          tone === "error" ? "text-destructive" : "text-muted-foreground",
        )}
        aria-hidden="true"
      />
      <div className="grid min-w-0 flex-1 gap-0.5">
        {title && <p className="m-0 font-medium">{title}</p>}
        {children && (
          <div className="text-sm leading-relaxed text-muted-foreground">
            {children}
          </div>
        )}
      </div>
      {action && <div className="flex-none">{action}</div>}
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: React.ReactNode;
  title: string;
  description?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div
      data-settings-empty=""
      className="grid justify-items-center gap-2 px-6 py-10 text-center"
    >
      {icon && (
        <div className="mb-1 grid size-10 place-items-center rounded-xl border border-line bg-subtle text-muted-foreground [&_svg]:size-5">
          {icon}
        </div>
      )}
      <p className="m-0 text-base font-medium text-foreground">{title}</p>
      {description && (
        <p className="m-0 max-w-sm text-sm leading-relaxed text-muted-foreground">
          {description}
        </p>
      )}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({
  error,
  onRetry,
  title = "Couldn't load this page",
}: {
  error: string;
  onRetry?: () => void;
  title?: string;
}) {
  return (
    <div
      role="alert"
      data-settings-error=""
      className="grid justify-items-center gap-2 rounded-(--ds-settings-row-radius) border border-line bg-card px-6 py-10 text-center"
    >
      <AlertTriangle
        className="size-5 text-muted-foreground"
        aria-hidden="true"
      />
      <p className="m-0 text-base font-medium">{title}</p>
      <p className="m-0 max-w-sm text-sm text-muted-foreground">{error}</p>
      {onRetry && (
        <Button size="sm" className="mt-2" onClick={onRetry}>
          <RotateCw className="size-3.5" aria-hidden="true" />
          Try again
        </Button>
      )}
    </div>
  );
}

export function LoadingRows({ rows = 3 }: { rows?: number }) {
  return (
    <SettingsCard>
      <div role="status" aria-label="Loading" data-settings-loading="">
        {Array.from({ length: rows }, (_, i) => (
          <div
            key={i}
            className="flex items-center gap-3 border-line p-(--ds-settings-row-padding) not-first:border-t"
          >
            <div className="h-3 w-1/3 animate-pulse rounded bg-subtle motion-reduce:animate-none" />
            <div className="ml-auto h-6 w-20 animate-pulse rounded-md bg-subtle motion-reduce:animate-none" />
          </div>
        ))}
      </div>
    </SettingsCard>
  );
}

/** Loading → error → empty → content, the same way on every screen. */
export function Loadable<T>({
  resource,
  empty,
  isEmpty,
  rows,
  title,
  children,
}: {
  /** What failed to load, e.g. "Couldn't load API keys" (defaults to the page). */
  title?: string;
  resource: {
    data: T | undefined;
    loading: boolean;
    error: string | null;
    reload: () => void;
  };
  empty?: React.ReactNode;
  isEmpty?: (data: T) => boolean;
  rows?: number;
  children: (data: T) => React.ReactNode;
}) {
  if (resource.error && resource.data === undefined)
    return (
      <ErrorState
        error={resource.error}
        onRetry={resource.reload}
        title={title}
      />
    );
  if (resource.data === undefined) return <LoadingRows rows={rows} />;
  if (empty && isEmpty?.(resource.data))
    return <SettingsCard>{empty}</SettingsCard>;
  return <>{children(resource.data)}</>;
}

/** A row in a managed list (labels, keys, members, teams). Actions live in one ⋯ menu. */
export function ResourceRow({
  leading,
  title,
  meta,
  badges,
  actions,
  menuLabel,
  children,
  onOpen,
}: {
  leading?: React.ReactNode;
  title: React.ReactNode;
  meta?: React.ReactNode;
  badges?: React.ReactNode;
  /** DropdownMenuItem elements. */
  actions?: React.ReactNode;
  menuLabel?: string;
  children?: React.ReactNode;
  onOpen?: () => void;
}) {
  const Main = onOpen ? "button" : "div";
  return (
    <div
      data-resource-row=""
      className="group flex min-h-14 min-w-0 items-center gap-3 px-(--ds-settings-row-padding) py-2.5"
    >
      <Main
        {...(onOpen ? { type: "button" as const, onClick: onOpen } : {})}
        className={cn(
          "flex min-w-0 flex-1 items-center gap-3 bg-transparent text-left",
          onOpen &&
            "-my-1 -ml-2 rounded-md py-1 pl-2 hover:bg-subtle focus-visible:outline-2 focus-visible:outline-ring",
        )}
      >
        {leading && (
          <span className="flex flex-none items-center">{leading}</span>
        )}
        <span className="grid min-w-0 flex-1 gap-0.5">
          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <span className="max-w-full truncate text-base font-medium text-foreground">
              {title}
            </span>
            {badges}
          </span>
          {meta && (
            <span className="truncate text-sm text-muted-foreground">
              {meta}
            </span>
          )}
        </span>
      </Main>
      {children}
      {actions && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={
                menuLabel ||
                `Actions for ${typeof title === "string" ? title : "item"}`
              }
              className="flex-none text-muted-foreground"
            >
              <MoreHorizontal className="size-4" aria-hidden="true" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent>{actions}</DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "strong" | "danger";
}) {
  return (
    <span
      className={cn(
        "inline-flex h-5 flex-none items-center rounded-md border px-1.5 text-xs font-medium whitespace-nowrap",
        tone === "strong" &&
          "border-transparent bg-primary text-primary-foreground",
        tone === "neutral" && "border-line bg-subtle text-muted-foreground",
        tone === "danger" &&
          "border-destructive/30 bg-destructive/5 text-destructive",
      )}
    >
      {children}
    </span>
  );
}

/** Read-only value with copy (URLs, IDs, a secret shown once). */
export function CopyField({
  value,
  label,
  secret,
}: {
  value: string;
  label: string;
  secret?: boolean;
}) {
  const [copied, setCopied] = React.useState(false);
  return (
    <div className="flex w-full min-w-0 items-center gap-2">
      <input
        readOnly
        aria-label={label}
        value={value}
        onFocus={(e) => e.currentTarget.select()}
        className={cn(
          "h-8 w-full min-w-0 rounded-lg border border-input bg-subtle px-2.5 text-base text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/25 pointer-coarse:min-h-11",
          secret && "font-mono text-sm",
        )}
      />
      <Button
        size="icon"
        aria-label={copied ? "Copied" : `Copy ${label}`}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          } catch {
            setCopied(false);
          }
        }}
      >
        {copied ? (
          <Check className="size-3.5" aria-hidden="true" />
        ) : (
          <Copy className="size-3.5" aria-hidden="true" />
        )}
      </Button>
    </div>
  );
}
