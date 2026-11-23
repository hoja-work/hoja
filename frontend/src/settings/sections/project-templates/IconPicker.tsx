import { Ban, FolderKanban } from "lucide-react";
import { RadioGroup as RadioPrimitive } from "radix-ui";
import { cn } from "cn";

/* Template icon: an emoji, stored as the project's icon string. Emoji render the same in
 * every surface (list, API clients, notifications) without a glyph registry. */
const TEMPLATE_ICONS = [
  "🚀",
  "🎯",
  "📈",
  "🧭",
  "🛠️",
  "💡",
  "🐛",
  "📣",
  "🎨",
  "🔒",
  "📱",
  "🌍",
] as const;
const NONE = "none";

export function IconPicker({
  value,
  onChange,
  id,
}: {
  value: string | null;
  onChange: (next: string | null) => void;
  id?: string;
}) {
  const current = value || NONE;
  const options: string[] = [
    NONE,
    ...TEMPLATE_ICONS,
    ...(current !== NONE && !TEMPLATE_ICONS.some((i) => i === current)
      ? [current]
      : []),
  ];
  return (
    <RadioPrimitive.Root
      id={id}
      aria-label="Template icon"
      value={current}
      onValueChange={(v) => onChange(v === NONE ? null : v)}
      className="flex flex-wrap gap-1"
    >
      {options.map((icon) => (
        <RadioPrimitive.Item
          key={icon}
          value={icon}
          aria-label={icon === NONE ? "No icon" : `Icon ${icon}`}
          className="grid size-8 place-items-center rounded-md border border-transparent text-base outline-none hover:bg-subtle focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring state-checked:border-primary state-checked:bg-subtle pointer-coarse:size-10"
        >
          {icon === NONE ? (
            <Ban
              className="size-3.5 text-muted-foreground"
              aria-hidden="true"
            />
          ) : (
            <span aria-hidden="true">{icon}</span>
          )}
        </RadioPrimitive.Item>
      ))}
    </RadioPrimitive.Root>
  );
}

/** The template's mark in lists: its emoji on a tile tinted with its colour. */
export function TemplateMark({
  icon,
  color,
}: {
  icon?: string | null;
  color?: string | null;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "relative grid size-8 flex-none place-items-center overflow-hidden rounded-lg border border-line bg-subtle text-base",
      )}
    >
      {color && (
        <span
          className="absolute inset-0 opacity-20"
          style={{ background: color }}
        />
      )}
      {color && (
        <span
          className="absolute inset-x-0 bottom-0 h-0.5"
          style={{ background: color }}
        />
      )}
      <span className="relative">
        {icon || <FolderKanban className="size-4 text-muted-foreground" />}
      </span>
    </span>
  );
}
