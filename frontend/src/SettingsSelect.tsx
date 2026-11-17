import React, { useEffect, useId, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from "./components/ui/popover";

export type SettingsOption = {
  value: string;
  label: string;
  description?: string;
  meta?: string;
  disabled?: boolean;
};

type Props = {
  label: string;
  value: string;
  options: SettingsOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
};

/** A select-only combobox with contextual option copy, viewport-aware placement,
 * aria-activedescendant navigation, typeahead, and no focus theft from its trigger. */
export function SettingsSelect({
  label,
  value,
  options,
  onChange,
  placeholder = "Choose an option…",
  disabled,
  className = "",
}: Props) {
  const uid = useId().replace(/:/g, "");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState("");
  const trigger = useRef<HTMLDivElement>(null);
  const typeahead = useRef("");
  const typeaheadTimer = useRef<number | undefined>(undefined);
  const enabled = options.filter((o) => !o.disabled);
  const selected = options.find((o) => o.value === value);
  const listId = `${uid}-listbox`;
  const optionId = React.useCallback(
    (option: string) => `${uid}-option-${option.replace(/[^a-z0-9_-]/gi, "-")}`,
    [uid],
  );

  useEffect(() => {
    if (!open || !active) return;
    document
      .getElementById(optionId(active))
      ?.scrollIntoView({ block: "nearest" });
  }, [active, open, optionId]);

  useEffect(() => () => window.clearTimeout(typeaheadTimer.current), []);

  const show = (preferred?: string) => {
    if (disabled || !enabled.length) return;
    const next = preferred || selected?.value || enabled[0].value;
    setActive(next);
    setOpen(true);
  };
  const move = (direction: 1 | -1) => {
    const index = Math.max(
      0,
      enabled.findIndex((o) => o.value === active),
    );
    setActive(
      enabled[(index + direction + enabled.length) % enabled.length].value,
    );
  };
  const choose = (option: string) => {
    if (!enabled.some((o) => o.value === option)) return;
    onChange(option);
    setOpen(false);
    trigger.current?.focus();
  };
  const findMatch = (character: string) => {
    window.clearTimeout(typeaheadTimer.current);
    typeahead.current += character.toLowerCase();
    const query = typeahead.current;
    const match =
      enabled.find((o) =>
        `${o.label} ${o.description || ""}`.toLowerCase().startsWith(query),
      ) ||
      enabled.find((o) =>
        o.label.toLowerCase().startsWith(character.toLowerCase()),
      );
    if (match) setActive(match.value);
    typeaheadTimer.current = window.setTimeout(() => {
      typeahead.current = "";
    }, 600);
  };
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (disabled) return;
    if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      if (!open)
        show(
          event.key === "Home"
            ? enabled[0]?.value
            : event.key === "End"
              ? enabled[enabled.length - 1]?.value
              : undefined,
        );
      else if (event.key === "Home") setActive(enabled[0].value);
      else if (event.key === "End")
        setActive(enabled[enabled.length - 1].value);
      else move(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      if (open && active) choose(active);
      else show();
    } else if (event.key === "Escape" && open) {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
    } else if (event.key === "Tab") {
      if (open) setOpen(false);
    } else if (
      event.key.length === 1 &&
      !event.ctrlKey &&
      !event.metaKey &&
      !event.altKey
    ) {
      event.preventDefault();
      if (!open) show();
      findMatch(event.key);
    }
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <div
        className={`relative ${className}` + " relative min-w-0 text-[#1f1f1f]"}
      >
        <PopoverAnchor asChild>
          <div
            ref={trigger}
            className={
              `flex cursor-pointer items-center justify-between border hover:border-muted focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-foreground aria-disabled:cursor-not-allowed aria-disabled:opacity-50 ${open ? "border-[#171717] shadow-[0_0_0_3px_#00000012] [html[data-theme=dark]_&]:border-[#aaa] [html[data-theme=dark]_&]:shadow-[0_0_0_3px_#ffffff12]" : ""}` +
              " flex items-center justify-between gap-3 w-full min-h-10.5 pt-2 pr-3 pb-2 pl-3 border border-solid border-[#d3d3d3] rounded-md bg-[#fefefe] text-[#1f1f1f] cursor-pointer [transition:border-color_.15s_ease,box-shadow_.15s_ease] hover:border-[#888] focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-[#525252] focus-visible:outline-offset-2 aria-disabled:cursor-not-allowed aria-disabled:opacity-58 [html[data-theme=dark]_&]:border-[#4d4d4d] [html[data-theme=dark]_&]:bg-[#2a2a2a] [html[data-theme=dark]_&]:text-[#f2f2f2] motion-reduce:[transition:none] focus-visible:outline-[#171717] [html[data-theme=dark]_&]:focus-visible:outline-[#ddd]"
            }
            role="combobox"
            aria-label={label}
            aria-expanded={open}
            aria-controls={open ? listId : undefined}
            aria-haspopup="listbox"
            aria-activedescendant={
              open && active ? optionId(active) : undefined
            }
            aria-disabled={disabled || undefined}
            tabIndex={disabled ? -1 : 0}
            onKeyDown={onKeyDown}
            onClick={() => (open ? setOpen(false) : show())}
          >
            <span className="grid text-left gap-0.5 min-w-0">
              <b className="truncate overflow-hidden text-ellipsis whitespace-nowrap text-[13px] font-[570]">
                {selected?.label || placeholder}
              </b>
              {selected?.meta && (
                <small className="text-[#6d6d6d] text-[11px] [html[data-theme=dark]_&]:text-[#b2b2b2]">
                  {selected.meta}
                </small>
              )}
            </span>
            <ChevronDown
              className={`shrink-0 text-[#6d6d6d] [html[data-theme=dark]_&]:text-[#b2b2b2] [transition:transform_.16s_ease] ${open ? "rotate-180" : ""}`}
              size={16}
              aria-hidden="true"
            />
          </div>
        </PopoverAnchor>
        <PopoverContent
          align="start"
          sideOffset={8}
          collisionPadding={12}
          onOpenAutoFocus={(event) => event.preventDefault()}
          onCloseAutoFocus={(event) => event.preventDefault()}
          onEscapeKeyDown={(event) => {
            event.stopPropagation();
            trigger.current?.focus();
          }}
          onInteractOutside={(event) => {
            if (trigger.current?.contains(event.target as Node))
              event.preventDefault();
          }}
          id={listId}
          className="overflow-y-auto overscroll-contain border border-line z-1000 pt-1.5 pr-1.5 pb-1.5 pl-1.5 border-solid border-[#d2d2d2] rounded-[10px] bg-[#fefefe] shadow-[0_20px_65px_#25252538,0_3px_12px_#25252512] text-[#1f1f1f] data-[state=closed]:hidden [html[data-theme=dark]_&]:border-[#4d4d4d] [html[data-theme=dark]_&]:bg-[#242424] [html[data-theme=dark]_&]:text-[#f2f2f2] [@media_(max-width:_700px)]:max-w-[calc(100vw-24px)] motion-reduce:animate-none"
          role="listbox"
          aria-label={label}
          style={{
            width: "min(420px, max(300px, var(--radix-popover-trigger-width)))",
            maxWidth: "calc(100vw - 24px)",
            maxHeight:
              "min(390px, var(--radix-popover-content-available-height))",
          }}
        >
          <div className="pt-2.25 pr-2.5 pb-1.75 pl-2.5 text-[#6d6d6d] font-(family-name:--hoja-font-ui) text-[11px] font-[650] tracking-[.08em] uppercase [html[data-theme=dark]_&]:text-[#b2b2b2]">
            {label}
          </div>
          {options.map((option) => (
            <div
              id={optionId(option.value)}
              key={option.value}
              className={
                `flex cursor-pointer items-center aria-disabled:cursor-not-allowed aria-disabled:opacity-50 ${active === option.value ? "bg-[#ededed] [html[data-theme=dark]_&]:bg-[#353535]" : ""}  ${value === option.value ? "text-[#171717] [html[data-theme=dark]_&]:text-inherit" : ""}` +
                " flex items-center gap-3.5 min-h-14.5 pt-2.5 pr-2.5 pb-2.5 pl-2.5 rounded-md cursor-pointer aria-disabled:cursor-not-allowed aria-disabled:opacity-45"
              }
              role="option"
              aria-selected={value === option.value}
              aria-disabled={option.disabled || undefined}
              data-active={active === option.value || undefined}
              data-value={option.value}
              onPointerMove={() => !option.disabled && setActive(option.value)}
              onClick={() => !option.disabled && choose(option.value)}
              onKeyDown={(event) => {
                if (
                  (event.key === "Enter" || event.key === " ") &&
                  !option.disabled
                )
                  choose(option.value);
              }}
              tabIndex={-1}
            >
              <span className="grid flex-1 gap-1 min-w-0">
                <b className="text-[13px] font-[620]">{option.label}</b>
                {option.description && (
                  <small className="text-[#6d6d6d] text-[12px] leading-[1.45] [html[data-theme=dark]_&]:text-[#b2b2b2]">
                    {option.description}
                  </small>
                )}
              </span>
              <span className="flex shrink-0 items-center justify-end text-right gap-1.75 max-w-[42%] text-[#6d6d6d] text-[11px] [html[data-theme=dark]_&]:text-[#b2b2b2]">
                {option.meta}
                {value === option.value && (
                  <Check
                    className="shrink-0 text-[#525252] [html[data-theme=dark]_&]:text-[#dadada]"
                    size={15}
                  />
                )}
              </span>
            </div>
          ))}
          <div className="border-t border-line mt-1.25 -mr-1.5 -mb-1.5 -ml-1.5 pt-2.25 pr-3 pb-2.25 pl-3 border-t-[#e6e6e6] text-[#6d6d6d] text-[11px] [html[data-theme=dark]_&]:text-[#b2b2b2] [html[data-theme=dark]_&]:border-[#434343]">
            ↑↓ to explore · Enter to select · Esc to close
          </div>
        </PopoverContent>
      </div>
    </Popover>
  );
}
