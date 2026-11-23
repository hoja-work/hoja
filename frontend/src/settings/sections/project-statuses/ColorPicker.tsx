import { Ban, Check } from "lucide-react";
import { RadioGroup as RadioPrimitive } from "radix-ui";
import { cn } from "cn";

/* A small swatch picker for list items (statuses, templates, initiative labels).
 * Radio semantics: arrow keys move between swatches, the selected one is announced.
 * `null` means "no colour" (the item falls back to the neutral dot). */
const SWATCHES = [
  "#6b7280",
  "#5e6ad2",
  "#26b5ce",
  "#4cb782",
  "#f2c94c",
  "#f2994a",
  "#eb5757",
  "#bb87fc",
] as const;

const NONE = "none";

export function ColorPicker({
  value,
  onChange,
  label,
  allowNone = true,
  id,
}: {
  value: string | null;
  onChange: (next: string | null) => void;
  label: string;
  allowNone?: boolean;
  id?: string;
}) {
  const current = value ? value.toLowerCase() : NONE;
  const known = SWATCHES.some((s) => s === current) || current === NONE;
  const options: string[] = [
    ...(allowNone ? [NONE] : []),
    ...SWATCHES,
    ...(known ? [] : [current]),
  ];
  return (
    <RadioPrimitive.Root
      id={id}
      aria-label={label}
      value={current}
      onValueChange={(v) => onChange(v === NONE ? null : v)}
      className="flex flex-wrap gap-2"
    >
      {options.map((c) => (
        <RadioPrimitive.Item
          key={c}
          value={c}
          aria-label={c === NONE ? "No colour" : c}
          className={cn(
            "grid size-7 place-items-center rounded-full border border-line outline-none hover:scale-105 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring state-checked:ring-2 state-checked:ring-primary state-checked:ring-offset-2 state-checked:ring-offset-popover motion-reduce:hover:scale-100 pointer-coarse:size-10",
            c === NONE && "bg-subtle text-muted-foreground",
          )}
          style={c === NONE ? undefined : { background: c }}
        >
          {c === NONE ? (
            <Ban className="size-3.5" aria-hidden="true" />
          ) : (
            <RadioPrimitive.Indicator>
              <Check
                className="size-3.5 text-white drop-shadow"
                aria-hidden="true"
              />
            </RadioPrimitive.Indicator>
          )}
        </RadioPrimitive.Item>
      ))}
    </RadioPrimitive.Root>
  );
}

/** The coloured dot that stands for a status or label in lists. */
export function ColorDot({ color }: { color?: string | null }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-block size-2.5 flex-none rounded-full",
        !color && "bg-muted-foreground/40",
      )}
      style={color ? { background: color } : undefined}
    />
  );
}
