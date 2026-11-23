import { Check } from "lucide-react";
import { RadioGroup as RadioPrimitive } from "radix-ui";
import { cn } from "cn";

/* A label colour picker built from a radio group of swatches: arrow keys move, Space picks,
 * every swatch has a name. A colour outside the palette (set through the API) shows as an
 * extra "Current colour" swatch so picking something else is never a silent loss. */
export const LABEL_COLORS = [
  { value: "#6b7280", name: "Gray" },
  { value: "#ef4444", name: "Red" },
  { value: "#f97316", name: "Orange" },
  { value: "#f59e0b", name: "Amber" },
  { value: "#eab308", name: "Yellow" },
  { value: "#84cc16", name: "Lime" },
  { value: "#22c55e", name: "Green" },
  { value: "#14b8a6", name: "Teal" },
  { value: "#06b6d4", name: "Cyan" },
  { value: "#3b82f6", name: "Blue" },
  { value: "#6366f1", name: "Indigo" },
  { value: "#8b5cf6", name: "Violet" },
  { value: "#d946ef", name: "Fuchsia" },
  { value: "#ec4899", name: "Pink" },
] as const;

export const DEFAULT_LABEL_COLOR = LABEL_COLORS[0].value;

export function colorName(value?: string | null) {
  if (!value) return "No colour";
  return (
    LABEL_COLORS.find((c) => c.value === value.toLowerCase())?.name ||
    `Custom (${value})`
  );
}

export function LabelDot({
  color,
  className,
}: {
  color?: string | null;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-block size-3 flex-none rounded-full border border-black/10",
        !color && "bg-subtle",
        className,
      )}
      style={color ? { background: color } : undefined}
    />
  );
}

export function ColorSwatches({
  value,
  onChange,
  label = "Colour",
  disabled,
}: {
  value: string | null | undefined;
  onChange: (color: string) => void;
  label?: string;
  disabled?: boolean;
}) {
  const current = (value || "").toLowerCase();
  const custom =
    current && !LABEL_COLORS.some((c) => c.value === current) ? current : null;
  const options = [
    ...LABEL_COLORS,
    ...(custom ? [{ value: custom, name: "Current colour" }] : []),
  ];
  return (
    <RadioPrimitive.Root
      aria-label={label}
      value={current || undefined}
      onValueChange={onChange}
      disabled={disabled}
      orientation="horizontal"
      loop
      className="flex flex-wrap gap-1.5"
    >
      {options.map((c) => (
        <RadioPrimitive.Item
          key={c.value}
          value={c.value}
          aria-label={c.name}
          title={c.name}
          className="group grid size-8 place-items-center rounded-full outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-50 state-checked:ring-2 state-checked:ring-foreground state-checked:ring-offset-2 state-checked:ring-offset-popover pointer-coarse:size-10"
        >
          <span
            className="grid size-6 place-items-center rounded-full border border-black/10 text-white"
            style={{ background: c.value }}
          >
            <Check
              className="hidden size-3.5 group-data-[state=checked]:block"
              aria-hidden="true"
            />
          </span>
        </RadioPrimitive.Item>
      ))}
    </RadioPrimitive.Root>
  );
}
