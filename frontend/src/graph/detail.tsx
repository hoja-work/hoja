/* Detail-page primitives shared by every entity page that follows the task detail
 * layout: a breadcrumb header, a scrolling main column (title, Markdown body,
 * collapsible sections) and a properties sidebar. The task detail and the
 * initiative detail both render through these, so a change here lands on both.
 * The class strings and `data-style-hooks` tokens are the task detail's own; the
 * hooks keep the selectors in shared.tsx and the layout probes working. */
import React, { useEffect, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";
import {
  Accordion,
  AccordionContent,
  AccordionTrigger,
} from "../components/motion-primitives/accordion";
import { Markdown } from "../markdown";
import { MobileNavTrigger } from "../mobileNav";
import { cx, LinPopover } from "./shared";

type DataAttributes = {
  [name: `data-${string}`]: string | number | boolean | null | undefined;
};

/** The page surface: one bordered panel holding the header and the columns. */
export function DetailPage({
  label,
  surface,
  className,
  children,
  ...data
}: DataAttributes & {
  label: string;
  surface: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      {...data}
      className={cx(
        "min-w-0 flex flex-col flex-1 min-h-0 bg-panel rounded-xl",
        className,
      )}
      data-style-hooks="lin-page"
      data-surface={surface}
      aria-label={label}
    >
      {children}
    </section>
  );
}

/** The 49px header row: breadcrumb on the left, page actions on the right. */
export function DetailHeader({
  crumbs,
  actions,
}: {
  crumbs: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <header
      className="flex items-center gap-2 min-h-12.25 py-0 px-5 border-b border-b-line flex-none shell-phone:gap-1 shell-phone:px-2 shell-phone:py-0.5"
      data-style-hooks="lin-head"
    >
      <MobileNavTrigger />
      <nav
        className="flex items-center gap-1.75 text-[13px] text-muted min-w-0 flex-1 shell-phone:overflow-hidden shell-phone:p-px shell-phone:[&>button]:flex-none [&_a]:[background:none] [&_a]:border-0 [&_a]:p-0 [&_a]:text-muted [&_a]:[font:inherit] [&_a]:cursor-pointer [&_a]:inline-flex [&_a]:items-center [&_a]:gap-1.5 [&_a]:whitespace-nowrap [&_a:hover]:text-foreground"
        data-style-hooks="lin-crumb"
        aria-label="Breadcrumb"
      >
        {crumbs}
      </nav>
      {actions && (
        <div
          className="flex items-center gap-0.5 ml-auto flex-none pointer-coarse:[&_[data-style-hooks~='lin-icon-btn']]:size-11"
          data-style-hooks="lin-head-actions"
        >
          {actions}
        </div>
      )}
    </header>
  );
}

/** A breadcrumb step that navigates. */
export function Crumb({
  onClick,
  title,
  className,
  children,
  ...data
}: DataAttributes & {
  onClick: () => void;
  title?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      {...data}
      type="button"
      className={cx(
        "[[data-style-hooks~='lin-crumb']_button&]:[background:none] [[data-style-hooks~='lin-crumb']_button&]:border-0 [[data-style-hooks~='lin-crumb']_button&]:p-0 [[data-style-hooks~='lin-crumb']_button&]:text-muted [[data-style-hooks~='lin-crumb']_button&]:[font:inherit] [[data-style-hooks~='lin-crumb']_button&]:cursor-pointer [[data-style-hooks~='lin-crumb']_button&]:inline-flex [[data-style-hooks~='lin-crumb']_button&]:items-center [[data-style-hooks~='lin-crumb']_button&]:gap-1.5 [[data-style-hooks~='lin-crumb']_button&]:whitespace-nowrap [[data-style-hooks~='lin-crumb']_button&:hover]:text-foreground",
        "pointer-coarse:min-h-11 pointer-coarse:min-w-11 pointer-coarse:justify-center",
        className,
      )}
      data-style-hooks="crumb-link"
      title={title}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export function CrumbSep({ className }: { className?: string } = {}) {
  return (
    <span
      className={cx(
        "in-data-[style-hooks~='lin-crumb']:text-muted in-data-[style-hooks~='lin-crumb']:opacity-70",
        className,
      )}
      data-style-hooks="sep"
      aria-hidden="true"
    >
      ›
    </span>
  );
}

/** The last breadcrumb step: the page itself, truncated rather than wrapped. */
export function CrumbCurrent({
  title,
  children,
}: {
  title?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className="min-w-0 truncate in-data-[style-hooks~='lin-crumb']:font-semibold in-data-[style-hooks~='lin-crumb']:text-muted"
      data-style-hooks="crumb-id"
      title={title}
      aria-current="page"
    >
      {children}
    </span>
  );
}

/** Main column plus properties sidebar. Below 900px they stack and the page scrolls
 * as one, so the sidebar never squeezes the main column into a sliver. On a touch
 * screen every icon button in the columns grows to a 44px target. */
export function DetailColumns({
  toolbar,
  toolbarOnPhone = true,
  main,
  aside,
  mainLabel,
  ...data
}: DataAttributes & {
  toolbar?: React.ReactNode;
  /** false drops the toolbar row on a phone, for a page whose header menu repeats it. */
  toolbarOnPhone?: boolean;
  main: React.ReactNode;
  aside?: React.ReactNode;
  mainLabel?: string;
}) {
  return (
    <div
      className="flex min-h-0 flex-1 [@media_(max-width:900px)]:flex-col [@media_(max-width:900px)]:overflow-y-auto [@media_(max-width:900px)]:overscroll-contain pointer-coarse:[&_[data-style-hooks~='lin-icon-btn']]:size-11"
      data-style-hooks="lin-detail"
    >
      <div
        className="flex min-h-0 min-w-0 flex-1 flex-col [@media_(max-width:900px)]:flex-none"
        {...data}
      >
        {toolbar && (
          <div
            className={cx(
              "flex items-center justify-end gap-1.5 py-2 px-5 border-b border-b-line flex-none shell-phone:px-4",
              !toolbarOnPhone && "shell-phone:hidden",
            )}
            data-style-hooks="lin-toolbar"
          >
            {toolbar}
          </div>
        )}
        <main
          className="flex-1 min-w-0 overflow-y-auto pt-6.5 px-10 pb-20 max-w-none [@media_(max-width:900px)]:py-5 [@media_(max-width:900px)]:px-4 [@media_(max-width:900px)]:flex-none [@media_(max-width:900px)]:overflow-visible"
          data-style-hooks="lin-detail-main"
          aria-label={mainLabel}
        >
          {main}
        </main>
      </div>
      {aside}
    </div>
  );
}

/** The properties sidebar. Children are PropDropdown/PropStatic rows and h3 groups. */
export function PropertiesPanel({
  label = "Properties",
  title = "Properties",
  inline = false,
  children,
}: {
  label?: string;
  title?: React.ReactNode;
  /** Rendered inside the main column (a phone's core properties under the title):
   * a bordered card instead of a sidebar. */
  inline?: boolean;
  children: React.ReactNode;
}) {
  if (inline)
    return (
      <aside
        className="mt-1 mb-5 rounded-xl border border-line px-3 py-1 [&_h3]:text-[12px] [&_h3]:font-semibold [&_h3]:text-muted [&_h3]:mt-2 [&_h3]:mb-1 pointer-coarse:[&_[data-style-hooks~='lin-prop']]:min-h-11 pointer-coarse:[&_[data-style-hooks~='plain']]:min-h-11 [&_[data-style-hooks~='plain']]:flex-1"
        data-style-hooks="lin-props lin-props-inline"
        aria-label={label}
      >
        {title && <h3>{title}</h3>}
        {children}
      </aside>
    );
  return (
    <aside
      className="w-72.5 flex-none border-l border-l-line overflow-y-auto pt-5.5 px-5 pb-15 [&_h3]:text-[12px] [&_h3]:font-semibold [&_h3]:text-muted [&_h3]:mt-0 [&_h3]:mx-0 [&_h3]:mb-2.5 [@media_(max-width:1100px)]:w-62.5 [@media_(max-width:900px)]:w-auto [@media_(max-width:900px)]:overflow-visible [@media_(max-width:900px)]:border-l-0 [@media_(max-width:900px)]:border-t [@media_(max-width:900px)]:border-t-line [@media_(max-width:900px)]:px-4 pointer-coarse:[&_[data-style-hooks~='lin-prop']]:min-h-11 pointer-coarse:[&_[data-style-hooks~='plain']]:min-h-11 shell-phone:[&_[data-style-hooks~='plain']]:flex-1 pointer-coarse:[&_[data-style-hooks~='lin-addrow']]:min-h-11"
      data-style-hooks="lin-props"
      aria-label={label}
    >
      {title && <h3>{title}</h3>}
      {children}
    </aside>
  );
}

/** A heading that starts a later group inside PropertiesPanel (Parent, Health, …). */
export function PropertiesGroup({ children }: { children: React.ReactNode }) {
  return (
    <div
      className="in-data-[style-hooks~='lin-props']:mt-4.5 in-data-[style-hooks~='lin-props']:mx-0 in-data-[style-hooks~='lin-props']:mb-1 in-data-[style-hooks~='lin-props']:text-[12px] in-data-[style-hooks~='lin-props']:font-semibold in-data-[style-hooks~='lin-props']:text-muted"
      data-style-hooks="sec"
    >
      {children}
    </div>
  );
}

/** Pill tabs under the header (Overview / Updates). Each tab is a real link. */
export function DetailTabs<T extends string>({
  label,
  tabs,
  current,
  onSelect,
}: {
  label: string;
  tabs: { id: T; label: React.ReactNode; href: string }[];
  current: T;
  onSelect: (id: T) => void;
}) {
  return (
    <nav
      className="mr-auto flex gap-1.5 overflow-x-auto scrollbar-none"
      aria-label={label}
    >
      {tabs.map((t) => (
        <a
          key={t.id}
          href={t.href}
          aria-current={t.id === current ? "page" : undefined}
          className="inline-flex h-7 pointer-coarse:h-11 flex-none items-center rounded-full border border-line bg-panel px-3 text-xs font-medium text-muted no-underline transition-colors duration-150 ease-out hover:bg-subtle hover:text-foreground hover:transition-none aria-[current=page]:bg-subtle aria-[current=page]:text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
          onClick={(e) => {
            if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
            e.preventDefault();
            onSelect(t.id);
          }}
        >
          {t.label}
        </a>
      ))}
    </nav>
  );
}

/** The one-line summary under a title: a plain field that saves on blur or Enter. */
export function DetailSummary({
  value,
  label = "Summary",
  placeholder = "Add a short summary…",
  disabled,
  onCommit,
}: {
  value?: string | null;
  label?: string;
  placeholder?: string;
  disabled?: boolean;
  onCommit: (next: string | null) => void;
}) {
  const [draft, setDraft] = useState(value || "");
  const focused = useRef(false);
  useEffect(() => {
    if (!focused.current) setDraft(value || "");
  }, [value]);
  return (
    <input
      className="mb-4 w-full border-0 bg-transparent p-0 text-base text-muted placeholder:text-muted focus:outline-none"
      aria-label={label}
      placeholder={disabled ? undefined : placeholder}
      value={draft}
      readOnly={disabled}
      onFocus={() => {
        focused.current = true;
      }}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        focused.current = false;
        const next = draft.trim() || null;
        if (next !== (value || null)) onCommit(next);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") {
          setDraft(value || "");
          e.currentTarget.blur();
        }
      }}
    />
  );
}

/** The page's editable title: grows with its text, saves on blur or Enter. */
export function DetailTitle({
  value,
  label = "Title",
  disabled,
  onCommit,
  onFocusChange,
}: {
  value: string;
  label?: string;
  disabled?: boolean;
  onCommit: (next: string) => void;
  /** The task detail keeps a live title from being overwritten while it is focused. */
  onFocusChange?: (focused: boolean) => void;
}) {
  const [draft, setDraft] = useState(value);
  const focused = useRef(false);
  const ref = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    if (!focused.current) setDraft(value);
  }, [value]);
  /* Grow only when the text wraps: a one-line title keeps the textarea's natural
   * height, and a long title is not clipped to one row when the page opens. */
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "";
    if (el.scrollHeight > el.clientHeight)
      el.style.height = el.scrollHeight + "px";
  }, [draft]);
  return (
    <textarea
      ref={ref}
      className="w-full border-0 bg-transparent text-foreground text-[21px] font-[650] tracking-[-.015em] leading-[1.3] p-0 mt-0 mx-0 mb-3.5 resize-none overflow-hidden font-[inherit] focus:[outline:none]"
      data-style-hooks="lin-detail-title"
      aria-label={label}
      rows={1}
      value={draft}
      readOnly={disabled}
      onFocus={() => {
        focused.current = true;
        onFocusChange?.(true);
      }}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        focused.current = false;
        onFocusChange?.(false);
        const t = draft.trim();
        if (t && t !== value) onCommit(t);
        else setDraft(value);
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          e.currentTarget.blur();
        }
      }}
    />
  );
}

/** A danger-toned line for a failed load or save, with an optional action. */
export function DetailNotice({
  children,
  action,
}: {
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <p
      className="in-data-[style-hooks~='lin-loadmore']:flex in-data-[style-hooks~='lin-loadmore']:items-center in-data-[style-hooks~='lin-loadmore']:gap-2.5 my-4 mx-6 py-3 px-4 border border-solid border-[color-mix(in_srgb,var(--danger)_40%,var(--line))] rounded-lg text-danger text-[13px] bg-[color-mix(in_srgb,var(--danger)_6%,var(--panel))]"
      data-style-hooks="lin-error"
      role="alert"
    >
      {children}
      {action && <> {action}</>}
    </p>
  );
}

/** The detail page's button. `primary` is the filled one. */
export function DetailButton({
  primary,
  className,
  type = "button",
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { primary?: boolean }) {
  return (
    <button
      {...rest}
      type={type}
      className={cx(
        "inline-flex items-center gap-2 border border-solid border-line bg-panel text-foreground rounded-[7px] py-2 px-3.5 text-[13px] font-[550] [&:hover]:bg-subtle disabled:opacity-50 disabled:cursor-default pointer-coarse:min-h-11",
        primary &&
          "data-[style-hooks~='lin-btn']:bg-[#1b1b1f] data-[style-hooks~='lin-btn']:border-[#1b1b1f] data-[style-hooks~='lin-btn']:text-white [[data-style-hooks~='lin-btn']&:hover]:bg-[#333]",
        className,
      )}
      data-style-hooks={primary ? "lin-btn primary" : "lin-btn"}
    />
  );
}

/** Markdown body that turns into an editor on click: the task description, an
 * initiative description. `onSave` resolves false to reopen the editor with the text. */
export function MarkdownField({
  value,
  ready = true,
  label = "Description",
  placeholder = "Add description…",
  loadingText = "Loading description…",
  disabled,
  onSave,
  ...data
}: DataAttributes & {
  value?: string | null;
  ready?: boolean;
  label?: string;
  placeholder?: string;
  loadingText?: string;
  disabled?: boolean;
  onSave: (next: string | null) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const ref = useRef<HTMLTextAreaElement | null>(null);
  const editing = draft !== null;
  /* Entering edit is an explicit request to type, so the caret belongs in the editor. */
  useEffect(() => {
    if (editing) ref.current?.focus();
  }, [editing]);
  const open = () => {
    if (disabled || (!ready && !value)) return;
    setDraft(value || "");
  };
  if (editing)
    return (
      <div>
        <textarea
          ref={ref}
          aria-label={label}
          value={draft}
          rows={6}
          onChange={(e) => setDraft(e.target.value)}
          style={{
            width: "100%",
            border: "1px solid var(--line)",
            borderRadius: 8,
            padding: 10,
            font: "inherit",
            fontSize: 14,
          }}
        />
        <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <DetailButton
            primary
            onClick={() => {
              const text = draft.trim() || null;
              setDraft(null);
              void onSave(text).then((ok) => {
                if (!ok) setDraft(text || "");
              });
            }}
          >
            Save
          </DetailButton>
          <DetailButton onClick={() => setDraft(null)}>Cancel</DetailButton>
        </div>
      </div>
    );
  return (
    <div
      {...data}
      className={cx(
        "relative text-[14px] leading-[1.6] text-foreground [word-break:break-word] mt-0 mx-0 mb-2 min-h-6",
        !value &&
          "data-[style-hooks~='lin-detail-desc']:text-muted data-[style-hooks~='lin-card-due']:text-muted data-[style-hooks~='lin-card-due']:border-dashed data-[style-hooks~='lin-card-due']:border-line",
      )}
      data-style-hooks={value ? "lin-detail-desc" : "lin-detail-desc empty"}
      aria-busy={!ready && !value}
      aria-label={disabled ? label : undefined}
      /* A read-only body is plain text; an editable one is a button into the editor. */
      {...(disabled
        ? {}
        : {
            role: "button",
            tabIndex: 0,
            onClick: open,
            onKeyDown: (e: React.KeyboardEvent) => {
              if (e.key === "Enter") open();
            },
            title: `Click to edit ${label.toLowerCase()}`,
            style: { cursor: "text" },
          })}
    >
      {value ? (
        <Markdown text={value} density="block" />
      ) : ready ? (
        placeholder
      ) : (
        loadingText
      )}
    </div>
  );
}

/** A collapsible section of the main column: Sub-tasks, Attachments, Projects,
 * Activity. `variant="activity"` is the bolder, last section of the page. */
export function DetailSection({
  title,
  label,
  badge,
  actions,
  variant = "sub",
  children,
}: {
  title: React.ReactNode;
  /** Accessible name; defaults to the title when it is plain text. */
  label?: string;
  badge?: React.ReactNode;
  actions?: React.ReactNode;
  variant?: "sub" | "activity";
  children: React.ReactNode;
}) {
  const activity = variant === "activity";
  return (
    <Accordion>
      <section
        className={
          activity
            ? "border-t border-t-line pt-4 mt-1.5 [&_>_header]:flex [&_>_header]:items-center [&_>_header]:mb-3 [&_>_header_b]:text-[14px] pointer-coarse:[&>header>button]:min-h-11"
            : "border-t border-t-line py-3.5 px-0 [&_>_header]:flex [&_>_header]:items-center [&_>_header]:gap-2 [&_>_header]:text-[13px] [&_>_header]:font-semibold [&_>_header]:mb-1.5 pointer-coarse:[&>header>button]:min-h-11"
        }
        data-style-hooks={activity ? "lin-activity" : "lin-subsec"}
        aria-label={label ?? (typeof title === "string" ? title : undefined)}
      >
        <header>
          <AccordionTrigger>
            <ChevronRight
              size={14}
              className="shrink-0 text-muted transition-transform group-data-expanded:rotate-90 motion-reduce:transition-none"
            />
            {activity ? <b>{title}</b> : <span>{title}</span>}
            {badge}
          </AccordionTrigger>
          <span
            className="in-data-[style-hooks~='lin-group-head']:flex-1 in-data-[style-hooks~='lin-hidden-col']:flex-1 in-data-[style-hooks~='lin-hidden-col']:overflow-hidden in-data-[style-hooks~='lin-hidden-col']:text-ellipsis in-data-[style-hooks~='lin-hidden-col']:whitespace-nowrap [[data-style-hooks~='lin-subsec']_>_header_&]:flex-1 [[data-style-hooks~='lin-activity']_>_header_&]:flex-1 in-data-[style-hooks~='lin-dd-opt']:flex-1 in-data-[style-hooks~='lin-dd-opt']:min-w-0 in-data-[style-hooks~='lin-dd-opt']:overflow-hidden in-data-[style-hooks~='lin-dd-opt']:[display:-webkit-box] in-data-[style-hooks~='lin-dd-opt']:[-webkit-line-clamp:2] in-data-[style-hooks~='lin-dd-opt']:[-webkit-box-orient:vertical] in-data-[style-hooks~='lin-dd-opt']:wrap-anywhere"
            data-style-hooks="grow"
          />
          {actions}
        </header>
        <AccordionContent>{children}</AccordionContent>
      </section>
    </Accordion>
  );
}

/** One line of an activity feed: avatar, who did what, when. */
export function ActivityLine({
  avatar,
  children,
  time,
}: {
  avatar: React.ReactNode;
  children: React.ReactNode;
  time?: React.ReactNode;
}) {
  return (
    <div
      className="flex gap-2.5 items-start text-[13px] text-muted py-1.75 px-0 [&_b]:text-foreground [&_b]:font-semibold [&_time]:ml-auto [&_time]:flex-none [&_time]:text-[12px]"
      data-style-hooks="lin-event"
    >
      {avatar}
      <span>{children}</span>
      {time}
    </div>
  );
}

export type PickerOption = {
  id: string | null;
  label: React.ReactNode;
  /** Text matched by the search box; defaults to the label when it is a string. */
  keywords?: string;
  selected?: boolean;
};

/** A searchable option list for a popover: single pick closes, multi pick toggles,
 * and `onCreate` offers the typed text as a new option (labels). */
export function PickerMenu({
  label,
  options,
  multi,
  onPick,
  onCreate,
  onClose,
  placeholder = "Search…",
  footer,
}: {
  label: string;
  options: PickerOption[];
  multi?: boolean;
  onPick: (id: string | null) => void;
  onCreate?: (text: string) => void;
  onClose: () => void;
  placeholder?: string;
  footer?: React.ReactNode;
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  useEffect(() => inputRef.current?.focus(), []);
  const q = query.trim().toLowerCase();
  const shown = options.filter((o) =>
    (o.keywords ?? (typeof o.label === "string" ? o.label : ""))
      .toLowerCase()
      .includes(q),
  );
  const canCreate =
    onCreate &&
    q &&
    !options.some((o) => (o.keywords ?? String(o.label)).toLowerCase() === q);
  const choose = (o: PickerOption) => {
    onPick(o.id);
    if (!multi) onClose();
  };
  return (
    <div className="grid gap-1" role="presentation">
      <input
        ref={inputRef}
        className="rounded-md border border-line bg-bg px-2 py-1.5 text-sm text-foreground placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent"
        placeholder={placeholder}
        aria-label={`Search ${label.toLowerCase()}`}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setActive((a) => Math.min(a + 1, shown.length - 1));
          } else if (e.key === "ArrowUp") {
            e.preventDefault();
            setActive((a) => Math.max(a - 1, 0));
          } else if (e.key === "Enter") {
            e.preventDefault();
            if (shown[active]) choose(shown[active]);
            else if (canCreate) onCreate!(query.trim());
          } else if (e.key === "Escape") {
            e.preventDefault();
            onClose();
          }
        }}
      />
      <div
        role="listbox"
        aria-label={label}
        aria-multiselectable={multi || undefined}
        className="grid max-h-64 gap-0.5 overflow-y-auto"
      >
        {shown.map((o, i) => (
          <button
            key={String(o.id)}
            type="button"
            role="option"
            aria-selected={Boolean(o.selected)}
            data-active={i === active || undefined}
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground hover:bg-subtle data-active:bg-subtle aria-selected:font-semibold pointer-coarse:min-h-11"
            onMouseEnter={() => setActive(i)}
            onClick={() => choose(o)}
          >
            <span className="min-w-0 flex-1 truncate">{o.label}</span>
            {o.selected && <span aria-hidden>✓</span>}
          </button>
        ))}
        {canCreate && (
          <button
            type="button"
            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-muted hover:bg-subtle pointer-coarse:min-h-11"
            onClick={() => onCreate!(query.trim())}
          >
            Create “{query.trim()}”
          </button>
        )}
        {!shown.length && !canCreate && (
          <p className="m-0 p-2 text-sm text-muted">No matches.</p>
        )}
      </div>
      {footer}
    </div>
  );
}

/** A property shown as a chip (Linear's row under the summary and its create dialog).
 * The chip opens whatever `menu` renders, anchored below it. */
export function PropertyChip({
  icon,
  children,
  label,
  menu,
  disabled,
  ...data
}: DataAttributes & {
  icon?: React.ReactNode;
  children: React.ReactNode;
  /** Accessible name, for example "Status: Active". */
  label: string;
  menu?: (close: () => void) => React.ReactNode;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <span className="relative inline-flex" {...data}>
      <button
        type="button"
        aria-label={label}
        aria-haspopup={menu ? "dialog" : undefined}
        aria-expanded={menu ? open : undefined}
        disabled={disabled || !menu}
        className="inline-flex h-7 max-w-full items-center gap-1.5 rounded-md border border-line bg-panel px-2 text-xs font-medium pointer-coarse:h-11 pointer-coarse:px-3 text-foreground transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none disabled:cursor-default disabled:hover:bg-panel focus-visible:outline-2 focus-visible:outline-accent"
        onClick={() => setOpen((v) => !v)}
      >
        {icon}
        <span className="truncate">{children}</span>
      </button>
      {open && menu && (
        <LinPopover
          onClose={() => setOpen(false)}
          className="absolute top-full left-0 z-50 mt-1 w-64 max-w-xs shell-phone:max-w-[calc(100vw-2rem)] rounded-lg border border-line bg-panel p-1 shadow-lg"
          role="dialog"
          aria-label={label}
        >
          {menu(() => setOpen(false))}
        </LinPopover>
      )}
    </span>
  );
}
