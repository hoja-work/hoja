import * as React from "react";
import { cn } from "cn";
import { Slot } from "radix-ui";
import { Loader2 } from "lucide-react";

/* One button for the whole settings surface. Variants carry meaning, not decoration:
 * primary = the one commit action on a surface, secondary = everything else,
 * ghost = row-level/inline actions, destructive = irreversible. */
const base =
  "inline-flex shrink-0 select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-lg font-medium transition-colors duration-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-50 aria-busy:cursor-progress [&_svg]:shrink-0 pointer-coarse:min-h-11";

const variants = {
  primary: "bg-primary text-primary-foreground hover:opacity-90",
  secondary:
    "border border-line bg-panel text-foreground shadow-xs hover:bg-subtle",
  ghost: "bg-transparent text-foreground hover:bg-subtle",
  destructive: "bg-destructive text-white hover:opacity-90",
  "destructive-ghost":
    "bg-transparent text-destructive hover:bg-destructive/10",
  link: "h-auto bg-transparent p-0 text-foreground underline-offset-4 hover:underline",
} as const;

const sizes = {
  sm: "h-7 px-2.5 text-sm",
  md: "h-8 px-3 text-sm",
  lg: "h-9 px-4 text-base",
  icon: "size-8 p-0 pointer-coarse:min-w-11",
  "icon-sm": "size-7 p-0 pointer-coarse:min-w-11",
} as const;

export type ButtonProps = React.ComponentProps<"button"> & {
  variant?: keyof typeof variants;
  size?: keyof typeof sizes;
  /** Shows a spinner, sets aria-busy and blocks re-submits; the label stays for width stability. */
  loading?: boolean;
  asChild?: boolean;
};

export function buttonClass({
  variant = "secondary",
  size = "md",
  className,
}: Pick<ButtonProps, "variant" | "size" | "className"> = {}) {
  return cn(base, variants[variant], sizes[size], className);
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      variant = "secondary",
      size = "md",
      loading,
      asChild,
      className,
      children,
      disabled,
      type,
      ...props
    },
    ref,
  ) {
    const Comp = asChild ? Slot.Root : "button";
    return (
      <Comp
        ref={ref}
        data-slot="button"
        data-variant={variant}
        type={asChild ? undefined : (type ?? "button")}
        className={buttonClass({ variant, size, className })}
        disabled={asChild ? undefined : disabled || loading}
        aria-busy={loading || undefined}
        {...props}
      >
        {asChild ? (
          children
        ) : (
          <>
            {loading && (
              <Loader2
                aria-hidden="true"
                className="size-3.5 animate-spin motion-reduce:animate-none"
              />
            )}
            {children}
          </>
        )}
      </Comp>
    );
  },
);
