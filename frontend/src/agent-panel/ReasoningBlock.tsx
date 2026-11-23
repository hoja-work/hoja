import { useState } from "react";
import { Collapsible } from "radix-ui";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { ChevronRight, Sparkles } from "lucide-react";

export function ReasoningBlock({
  thinking,
  isStreaming,
}: {
  thinking: string;
  isStreaming: boolean;
}) {
  const [open, setOpen] = useState(false);
  const reduced = useReducedMotion();

  if (!thinking.trim()) return null;

  return (
    <Collapsible.Root
      open={open}
      onOpenChange={setOpen}
      className="my-1 w-full rounded-xl border border-line bg-panel overflow-hidden"
      data-reasoning-block=""
    >
      <Collapsible.Trigger
        type="button"
        className="flex w-full min-w-0 items-center gap-2 px-3 py-2 text-left text-xs text-muted transition-colors duration-150 ease-out hover:bg-subtle hover:text-foreground hover:transition-none focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11"
      >
        <Sparkles
          className={`size-3.5 shrink-0 ${isStreaming && !reduced ? "animate-pulse text-foreground" : "text-muted"}`}
          aria-hidden="true"
        />
        <span className="font-medium text-foreground">
          {isStreaming ? "Thinking…" : "Thought process"}
        </span>
        <ChevronRight
          className={`ml-auto size-4 text-muted transition-transform duration-150 ${open ? "rotate-90" : ""}`}
          aria-hidden="true"
        />
      </Collapsible.Trigger>
      <AnimatePresence initial={false}>
        {open && (
          <Collapsible.Content forceMount asChild>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: reduced ? 0 : 0.14 }}
              className="border-t border-line bg-bg/60 p-3"
            >
              <div className="border-l-2 border-line pl-3 text-xs leading-relaxed text-muted whitespace-pre-wrap break-words">
                {thinking.trim()}
              </div>
            </motion.div>
          </Collapsible.Content>
        )}
      </AnimatePresence>
    </Collapsible.Root>
  );
}
