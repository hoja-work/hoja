/* Shared primitives compose standard Tailwind utilities with caller styling. */
import React from "react";

type DataAttributes = {
  [name: `data-${string}`]: string | number | boolean | null | undefined;
};

const cx = (...parts: Array<string | false | null | undefined>) =>
  parts.filter(Boolean).join(" ");

/** Two-pane split: a capped ledger column and the detail pane that takes the rest. */
export function SplitPane({
  as: Root = "div",
  className,
  ledgerClassName,
  detailClassName,
  ledger,
  detail,
  detailLabel,
  ...data
}: DataAttributes & {
  /** Element for the outer split. A screen that used a landmark (<section>/<main>) keeps it. */
  as?: "div" | "section" | "main";
  className?: string;
  ledgerClassName?: string;
  detailClassName?: string;
  ledger: React.ReactNode;
  detail: React.ReactNode;
  detailLabel?: string;
}) {
  return (
    <Root
      {...data}
      className={cx(
        "[:where(&)]:flex [:where(&)]:items-stretch [:where(&)]:flex-auto [:where(&)]:min-h-0 [:where(&)]:min-w-0 [:where(&)]:overflow-hidden",
        className,
      )}
    >
      <div
        data-split-ledger
        className={cx(
          "[:where(&)]:flex [:where(&)]:flex-col [:where(&)]:min-h-0 [:where(&)]:min-w-0 [:where(&)]:flex-[1_1_var(--ds-ledger-width)] [:where(&)]:w-(--ds-ledger-width) [:where(&)]:max-w-(--ds-ledger-width)",
          ledgerClassName,
        )}
      >
        {ledger}
      </div>
      <aside
        data-split-detail
        className={cx(
          "[:where(&)]:flex [:where(&)]:flex-col [:where(&)]:items-stretch [:where(&)]:justify-start [:where(&)]:min-w-0 [:where(&)]:flex-auto",
          detailClassName,
        )}
        aria-label={detailLabel}
      >
        {detail}
      </aside>
    </Root>
  );
}

/** Scroll region that owns its own overflow, so the page never scrolls sideways. */
export function ScrollArea({
  className,
  variant = "list",
  role,
  id,
  labelledBy,
  tabIndex,
  children,
  ...data
}: DataAttributes & {
  className?: string;
  variant?: "list" | "detail" | "diff";
  role?: string;
  id?: string;
  labelledBy?: string;
  tabIndex?: number;
  children: React.ReactNode;
}) {
  return (
    <div
      {...data}
      className={cx(
        "[:where(&)]:flex-auto [:where(&)]:min-h-0 [:where(&)]:overflow-y-auto [:where(&)]:scrollbar-thin",
        variant === "detail"
          ? "[:where(&)]:p-(--ds-scroll-detail-padding) [@media_(max-width:600px)]:[:where(&)]:p-(--ds-scroll-detail-padding-narrow)"
          : variant === "diff"
            ? "[:where(&)]:min-w-0 [:where(&)]:overflow-x-hidden"
            : "[:where(&)]:p-[0_0_var(--ds-scroll-pad-bottom)]",
        className,
      )}
      role={role}
      id={id}
      aria-labelledby={labelledBy}
      tabIndex={tabIndex}
    >
      {children}
    </div>
  );
}

/** A strip of tabs. The pane row is a real tablist; the ledger pills are just a strip. */
export function TabStrip({
  className,
  role,
  label,
  children,
  ...data
}: DataAttributes & {
  className?: string;
  role?: "tablist";
  label?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      {...data}
      className={cx(
        "[:where(&)]:flex [:where(&)]:items-center [:where(&)]:flex-none [:where(&)]:gap-(--ds-tabstrip-gap) [:where(&)]:h-(--ds-tabstrip-height) [:where(&)]:p-(--ds-tabstrip-padding)",
        className,
      )}
      role={role}
      aria-label={role ? label : undefined}
    >
      {children}
    </div>
  );
}

export function Tab(
  props: React.ButtonHTMLAttributes<HTMLButtonElement> & DataAttributes,
) {
  const { className, children, ...rest } = props;
  return (
    <button
      type="button"
      className={cx(
        "[:where(&)]:inline-flex [:where(&)]:items-center [:where(&)]:gap-(--ds-tabstrip-gap) [:where(&)]:h-(--ds-tab-height) [:where(&)]:p-(--ds-tab-padding) [:where(&)]:rounded-(--ds-tab-radius) [:where(&)]:bg-transparent [:where(&)]:text-(length:--ds-tab-font-size) [:where(&)]:font-medium",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

/** A collapsible group band (Ready to merge / Open / Draft, and any grouped list). */
export function GroupBar({
  as: Heading = "h3",
  className,
  label,
  count,
  caret,
  controls,
  ...data
}: DataAttributes & {
  /** Heading level. A feed's era band sits above post titles, so Pulse asks for h2. */
  as?: "h2" | "h3" | "h4";
  className?: string;
  label: React.ReactNode;
  count?: number;
  caret?: React.ReactNode;
  controls?: string;
}) {
  return (
    <Heading
      {...data}
      className={cx(
        "[:where(&)]:flex [:where(&)]:items-center [:where(&)]:gap-(--ds-group-bar-gap) [:where(&)]:h-(--ds-group-bar-height) [:where(&)]:m-(--ds-group-bar-margin) [:where(&)]:p-(--ds-group-bar-padding) [:where(&)]:rounded-(--ds-group-bar-radius)",
        className,
      )}
      aria-controls={controls}
    >
      {label}
      {count != null ? <b>{count}</b> : null}
      {caret}
    </Heading>
  );
}

/** A flat list row: 40px, no border, with a full-height click target passed in. */
export function ListRow({
  className,
  main,
  aside,
  selected,
  ...data
}: DataAttributes & {
  className?: string;
  main: React.ReactNode;
  aside?: React.ReactNode;
  selected?: boolean;
}) {
  return (
    <li
      {...data}
      className={cx(
        "[:where(&)]:flex [:where(&)]:items-center [:where(&)]:gap-0 [:where(&)]:h-(--ds-row-height) [:where(&)]:m-0 [:where(&)]:p-(--ds-row-padding) [:where(&)]:border-0 [:where(&)]:rounded-(--ds-row-radius) [:where(&)]:[background:none] [:where(&)]:relative",
        className,
      )}
      data-selected={selected ? "true" : undefined}
    >
      {main}
      {aside}
    </li>
  );
}

/** A property row: fixed label column, value column that takes the remaining width. */
export function PropertyRow({
  className,
  label,
  children,
  ...data
}: DataAttributes & {
  className?: string;
  label: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div
      {...data}
      className={cx(
        "[:where(&)]:flex [:where(&)]:items-start [:where(&)]:gap-(--ds-prop-gap) [:where(&)]:min-h-(--ds-prop-min-height) [:where(&)]:m-0",
        className,
      )}
    >
      <dt
        className={
          "[:where(&)]:flex-[0_0_var(--ds-prop-label-width)] [:where(&)]:w-(--ds-prop-label-width) [:where(&)]:leading-(--ds-prop-line-height) [:where(&)]:text-[12px] [:where(&)]:font-[450]"
        }
      >
        {label}
      </dt>
      <dd
        className={
          "[:where(&)]:flex [:where(&)]:items-center [:where(&)]:gap-1.5 [:where(&)]:flex-auto [:where(&)]:min-w-0 [:where(&)]:m-0 [:where(&)]:leading-(--ds-prop-line-height)"
        }
      >
        {children}
      </dd>
    </div>
  );
}

/** What a pane shows when it has nothing to show — never a fake row. */
export function EmptyState({
  className,
  children,
  ...data
}: DataAttributes & { className?: string; children: React.ReactNode }) {
  return (
    <div
      {...data}
      className={cx(
        "[:where(&)]:flex [:where(&)]:flex-col [:where(&)]:items-center [:where(&)]:justify-center [:where(&)]:gap-(--ds-empty-gap)",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** A composer: one input, optional leading icon, one send action. */
export function Composer({
  className,
  value,
  placeholder,
  disabled,
  icon,
  sendIcon,
  sendLabel = "Send",
  onChange,
  onSend,
  note,
  ...data
}: DataAttributes & {
  className?: string;
  value: string;
  placeholder?: string;
  disabled?: boolean;
  icon?: React.ReactNode;
  sendIcon: React.ReactNode;
  sendLabel?: string;
  onChange: (next: string) => void;
  onSend: () => void;
  note?: React.ReactNode;
}) {
  return (
    <form
      {...data}
      className={cx(
        "[:where(&)]:flex [:where(&)]:items-center [:where(&)]:gap-(--ds-composer-gap) [:where(&)]:max-w-(--ds-composer-max-width) [:where(&)]:h-(--ds-composer-height) [:where(&)]:mt-4.5 [:where(&)]:mx-0 [:where(&)]:mb-0 [:where(&)]:p-(--ds-composer-padding) [:where(&)]:rounded-(--ds-composer-radius) [@media_(max-width:600px)]:[:where(&)]:h-(--ds-composer-height-narrow)",
        className,
      )}
      onSubmit={(e) => {
        e.preventDefault();
        onSend();
      }}
    >
      {icon ? (
        <span
          className={
            "[:where(&)]:grid [:where(&)]:place-items-center [:where(&)]:w-(--ds-composer-action) [:where(&)]:h-(--ds-composer-action) [:where(&)]:flex-none [:where(&)]:rounded-[5px] [:where(&)]:bg-transparent"
          }
          aria-hidden="true"
        >
          {icon}
        </span>
      ) : null}
      <input
        className={
          "[:where(&)]:flex-auto [:where(&)]:min-w-0 [:where(&)]:h-full [:where(&)]:border-0 [:where(&)]:bg-transparent [:where(&)]:text-foreground [:where(&)]:text-[13px]"
        }
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        type="submit"
        className={
          "[:where(&)]:grid [:where(&)]:place-items-center [:where(&)]:w-(--ds-composer-action) [:where(&)]:h-(--ds-composer-action) [:where(&)]:flex-none [:where(&)]:rounded-[5px] [:where(&)]:bg-transparent"
        }
        aria-label={sendLabel}
        disabled={disabled}
      >
        {sendIcon}
      </button>
      {note}
    </form>
  );
}
