/* The one glyph vocabulary shared by the issue list and the task page.
 *
 * Drawn from Phosphor's filled weights, imported per icon so the bundle carries only what is
 * used (the package's barrel re-exports the whole set and must not be imported). Shape carries
 * the workflow state and the state's own colour is applied on top, so a glyph reads the same
 * in light and dark themes.
 */
import type { ComponentType } from "react";
import { Circle } from "@phosphor-icons/react/dist/csr/Circle";
import { CircleDashed } from "@phosphor-icons/react/dist/csr/CircleDashed";
import { CircleHalf } from "@phosphor-icons/react/dist/csr/CircleHalf";
import { CheckCircle } from "@phosphor-icons/react/dist/csr/CheckCircle";
import { XCircle } from "@phosphor-icons/react/dist/csr/XCircle";

type Weight = "regular" | "fill";
type Glyph = { Icon: ComponentType<{ size?: number; weight?: Weight; color?: string; className?: string }>; weight: Weight };

/* Empty states stay open (outline is the "nothing here yet" signal); work in flight and finished
   work are solid, which is what gives the column its weight. */
const BY_TYPE: Record<string, Glyph> = {
  backlog: { Icon: CircleDashed, weight: "regular" },
  unstarted: { Icon: Circle, weight: "regular" },
  started: { Icon: CircleHalf, weight: "fill" },
  completed: { Icon: CheckCircle, weight: "fill" },
  canceled: { Icon: XCircle, weight: "fill" },
};

const FALLBACK = BY_TYPE.unstarted;

export function stateGlyphFor(state?: { name?: string | null; type?: string | null } | null): Glyph {
  const type = (state?.type || "").toLowerCase();
  const name = (state?.name || "").toLowerCase();
  // "Duplicate" is a workflow state in some teams, not a type: give it the canceled shape.
  if (name.includes("duplicate")) return BY_TYPE.canceled;
  return BY_TYPE[type] || FALLBACK;
}

export function StateGlyph({
  state,
  size = 14,
  className,
}: {
  state?: { name?: string | null; type?: string | null; color?: string | null } | null;
  size?: number;
  className?: string;
}) {
  const { Icon, weight } = stateGlyphFor(state);
  return (
    <Icon
      size={size}
      weight={weight}
      color={state?.color || "currentColor"}
      className={className}
      aria-hidden="true"
    />
  );
}
