import { useEffect, useRef } from "react";
import { ArrowUp, Square } from "lucide-react";

export function Composer({
  value,
  onChange,
  onSend,
  onStop,
  isRunning,
  placeholder = "Ask about tasks, projects, or cycles…",
}: {
  value: string;
  onChange: (next: string) => void;
  onSend: () => void;
  onStop: () => void;
  isRunning: boolean;
  placeholder?: string;
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 192)}px`;
  }, [value]);

  const canSend = !isRunning && value.trim().length > 0;

  return (
    <form
      className="mx-auto w-full max-w-3xl"
      data-agent-composer=""
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSend();
      }}
    >
      <div className="flex w-full flex-col gap-2 rounded-2xl border border-line bg-panel p-3 shadow-sm transition-colors duration-150 focus-within:border-foreground/30">
        <textarea
          ref={textareaRef}
          rows={1}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              if (canSend) onSend();
            }
          }}
          placeholder={placeholder}
          aria-label="Message the workspace agent"
          className="max-h-48 min-h-6 w-full resize-none border-0 bg-transparent px-1 py-0.5 text-sm pointer-coarse:min-h-11 leading-relaxed text-foreground placeholder:text-muted focus:outline-none"
        />
        <div className="flex items-center justify-between gap-2">
          <span className="hidden text-xs text-muted sm:inline">
            {isRunning
              ? "Agent is working…"
              : "Enter to send · Shift+Enter for new line"}
          </span>
          <div className="ml-auto flex items-center gap-2">
            {isRunning ? (
              <button
                type="button"
                onClick={onStop}
                aria-label="Stop generating"
                title="Stop generating"
                className="inline-flex size-8 items-center justify-center rounded-full bg-foreground text-bg transition-colors duration-150 ease-out hover:opacity-85 focus-visible:outline-2 focus-visible:outline-accent shell-phone:size-11"
              >
                <Square className="size-3.5 fill-current" aria-hidden="true" />
              </button>
            ) : (
              <button
                type="submit"
                disabled={!canSend}
                aria-label="Send message"
                title="Send message"
                className="inline-flex size-8 items-center justify-center rounded-full bg-foreground text-bg transition-colors duration-150 ease-out hover:opacity-85 disabled:cursor-not-allowed disabled:opacity-30 focus-visible:outline-2 focus-visible:outline-accent shell-phone:size-11"
              >
                <ArrowUp className="size-4" aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
      </div>
    </form>
  );
}
