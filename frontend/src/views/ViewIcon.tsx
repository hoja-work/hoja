/* A view's look: its icon (a name from the icon set, or an emoji) in its colour, and the picker
 * Linear opens from the icon button of the view editor: Icons / Emojis tabs, the nine swatches,
 * a search box and the grid.
 */
import { useMemo, useState } from "react";
import {
  Bell,
  Bookmark,
  Box,
  Briefcase,
  Bug,
  Calendar,
  CircleCheck,
  CircleDot,
  Clock,
  Code,
  Database,
  Eye,
  Flag,
  Flame,
  Funnel,
  Globe,
  Heart,
  Inbox,
  Layers,
  ListTodo,
  Lock,
  Megaphone,
  MessageCircle,
  Palette,
  RefreshCw,
  Rocket,
  ShieldAlert,
  SignalHigh,
  Siren,
  Sparkles,
  Star,
  Tag,
  Target,
  UserRound,
  Users,
  Wrench,
  Zap,
  type LucideIcon,
} from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../components/ui/popover";
import {
  isEmojiIcon,
  VIEW_COLORS,
  VIEW_EMOJI,
  VIEW_ICON_GROUPS,
} from "./viewModel";

const ICONS: Record<string, LucideIcon> = {
  Bell,
  Bookmark,
  Box,
  Briefcase,
  Bug,
  Calendar,
  CircleCheck,
  CircleDot,
  Clock,
  Code,
  Database,
  Eye,
  Flag,
  Flame,
  Funnel,
  Globe,
  Heart,
  Inbox,
  Layers,
  ListTodo,
  Lock,
  Megaphone,
  MessageCircle,
  Palette,
  RefreshCw,
  Rocket,
  ShieldAlert,
  SignalHigh,
  Siren,
  Sparkles,
  Star,
  Tag,
  Target,
  UserRound,
  Users,
  Wrench,
  Zap,
};

/** A view's icon in its colour; a view without one shows the layers glyph. */
export function ViewIcon({
  icon,
  color,
  size = 15,
}: {
  icon?: string | null;
  color?: string | null;
  size?: number;
}) {
  if (icon && isEmojiIcon(icon))
    return (
      <span
        aria-hidden="true"
        className="inline-grid flex-none place-items-center leading-none"
        style={{ width: size, height: size, fontSize: size - 2 }}
        data-view-icon="emoji"
      >
        {icon}
      </span>
    );
  const Glyph = (icon && ICONS[icon]) || Layers;
  return (
    <Glyph
      size={size}
      aria-hidden="true"
      className="flex-none"
      style={color && color !== VIEW_COLORS[0] ? { color } : undefined}
      data-view-icon={icon || "default"}
    />
  );
}

/** The icon button of the view editor and header, opening the icon and colour picker. */
export function ViewIconPicker({
  icon,
  color,
  onChange,
  disabled = false,
}: {
  icon?: string | null;
  color?: string | null;
  onChange: (next: { icon?: string | null; color?: string | null }) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"icons" | "emoji">(
    isEmojiIcon(icon) ? "emoji" : "icons",
  );
  const [query, setQuery] = useState("");
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    return VIEW_ICON_GROUPS.map(
      ([label, names]) =>
        [label, names.filter((n) => !q || n.toLowerCase().includes(q))] as const,
    ).filter(([, names]) => names.length);
  }, [query]);
  const button =
    "grid size-8 place-items-center rounded-md border border-line bg-panel text-muted hover:bg-subtle hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11 shell-phone:min-w-11";
  if (disabled)
    return (
      <span className={button} aria-hidden="true">
        <ViewIcon icon={icon} color={color} size={16} />
      </span>
    );
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={button}
          aria-label="Change view icon and colour"
          data-view-icon-button=""
        >
          <ViewIcon icon={icon} color={color} size={16} />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        sideOffset={6}
        className="z-50 grid w-[min(400px,calc(100vw-32px))] gap-2 rounded-lg border border-line bg-panel p-2 text-sm text-foreground shadow-lg"
        data-view-icon-picker=""
        aria-label="View icon"
      >
        <div className="flex gap-1" role="tablist" aria-label="Icon type">
          {(
            [
              ["icons", "Icons"],
              ["emoji", "Emojis"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={tab === key}
              className="rounded-md px-2 py-1 text-muted aria-selected:bg-subtle aria-selected:text-foreground hover:text-foreground shell-phone:min-h-11"
              onClick={() => setTab(key)}
            >
              {label}
            </button>
          ))}
        </div>
        {tab === "icons" && (
          <div
            className="flex flex-wrap items-center gap-1.5 border-b border-line pb-2"
            role="radiogroup"
            aria-label="Colour"
          >
            {VIEW_COLORS.map((swatch) => (
              <button
                key={swatch}
                type="button"
                role="radio"
                aria-checked={(color || VIEW_COLORS[0]) === swatch}
                aria-label={`Colour ${swatch}`}
                className="size-6 rounded-full ring-offset-2 ring-offset-panel aria-checked:ring-2 aria-checked:ring-accent focus-visible:outline-2 focus-visible:outline-accent shell-phone:size-9"
                style={{ background: swatch }}
                onClick={() => onChange({ color: swatch })}
              />
            ))}
            <label
              className="relative ml-1 size-6 overflow-hidden rounded-full bg-[conic-gradient(#eb5757,#f2c94c,#4cb782,#26b5ce,#5e6ad2,#eb5757)] shell-phone:size-9"
              title="Custom colour"
            >
              <span className="sr-only">Custom colour</span>
              <input
                type="color"
                className="absolute inset-0 cursor-pointer opacity-0"
                value={/^#[0-9a-f]{6}$/i.test(color || "") ? color! : "#5e6ad2"}
                onChange={(e) => onChange({ color: e.target.value })}
              />
            </label>
          </div>
        )}
        {tab === "icons" && (
          <input
            className="w-full rounded-md border border-line bg-bg px-2 py-1.5 text-sm placeholder:text-muted focus-visible:outline-2 focus-visible:outline-accent max-md:text-base shell-phone:min-h-11"
            placeholder="Search icons…"
            aria-label="Search icons"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        )}
        <div className="grid max-h-60 gap-2 overflow-y-auto overscroll-contain">
          {tab === "icons" ? (
            groups.length ? (
              groups.map(([label, names]) => (
                <section key={label} className="grid gap-1">
                  <h3 className="text-xs font-medium text-muted">{label}</h3>
                  <div className="grid grid-cols-[repeat(auto-fill,minmax(32px,1fr))] gap-0.5">
                    {names.map((name) => (
                      <button
                        key={name}
                        type="button"
                        aria-label={name}
                        aria-pressed={icon === name}
                        title={name}
                        className="grid size-8 place-items-center rounded-md text-muted hover:bg-subtle hover:text-foreground aria-pressed:bg-subtle aria-pressed:text-foreground shell-phone:size-11"
                        onClick={() => {
                          onChange({ icon: name });
                          setOpen(false);
                        }}
                      >
                        <ViewIcon icon={name} color={color} size={16} />
                      </button>
                    ))}
                  </div>
                </section>
              ))
            ) : (
              <p className="py-3 text-center text-muted">No matching icons</p>
            )
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(32px,1fr))] gap-0.5">
              {VIEW_EMOJI.map((emoji) => (
                <button
                  key={emoji}
                  type="button"
                  aria-label={`Emoji ${emoji}`}
                  aria-pressed={icon === emoji}
                  className="grid size-8 place-items-center rounded-md text-base hover:bg-subtle aria-pressed:bg-subtle shell-phone:size-11"
                  onClick={() => {
                    onChange({ icon: emoji });
                    setOpen(false);
                  }}
                >
                  {emoji}
                </button>
              ))}
            </div>
          )}
        </div>
        {icon && (
          <button
            type="button"
            className="justify-self-start rounded-md px-2 py-1 text-muted hover:bg-subtle hover:text-foreground shell-phone:min-h-11"
            onClick={() => {
              onChange({ icon: null });
              setOpen(false);
            }}
          >
            Remove icon
          </button>
        )}
      </PopoverContent>
    </Popover>
  );
}
