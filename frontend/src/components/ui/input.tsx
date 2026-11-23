import * as React from "react";
import { cn } from "cn";
import { Label as LabelPrimitive } from "radix-ui";

const field =
  "w-full min-w-0 rounded-lg border border-input bg-panel px-2.5 text-base text-foreground shadow-xs outline-none transition-[border-color,box-shadow] duration-100 placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25 disabled:cursor-not-allowed disabled:opacity-60 read-only:bg-subtle aria-invalid:border-destructive aria-invalid:ring-2 aria-invalid:ring-destructive/20 pointer-coarse:min-h-11";

export const Input = React.forwardRef<
  HTMLInputElement,
  React.ComponentProps<"input">
>(function Input({ className, type = "text", ...props }, ref) {
  return (
    <input
      ref={ref}
      type={type}
      data-slot="input"
      className={cn(field, "h-8", className)}
      {...props}
    />
  );
});

export const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.ComponentProps<"textarea">
>(function Textarea({ className, ...props }, ref) {
  return (
    <textarea
      ref={ref}
      data-slot="textarea"
      className={cn(field, "min-h-20 resize-y py-2 leading-normal", className)}
      {...props}
    />
  );
});

export function Label({
  className,
  ...props
}: React.ComponentProps<typeof LabelPrimitive.Root>) {
  return (
    <LabelPrimitive.Root
      data-slot="label"
      className={cn(
        "text-base font-medium text-foreground select-none peer-disabled:opacity-60",
        className,
      )}
      {...props}
    />
  );
}
