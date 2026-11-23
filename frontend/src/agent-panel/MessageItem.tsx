import { useState } from "react";
import { useReducedMotion } from "framer-motion";
import { AlertCircle, Check, Copy, RotateCcw } from "lucide-react";
import { Markdown } from "../markdown";
import { ReasoningBlock } from "./ReasoningBlock";
import { ToolCallGroup } from "./ToolCallGroup";
import { splitAsk, type ChatMessage } from "./types";

function CopyAction({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={copied ? "Copied message" : "Copy message"}
      title={copied ? "Copied" : "Copy"}
      onClick={() => {
        void navigator.clipboard
          ?.writeText(text)
          .then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          })
          .catch(() => {});
      }}
      className="inline-flex size-7 items-center justify-center rounded-md text-muted transition-colors duration-150 ease-out hover:bg-subtle hover:text-foreground hover:transition-none focus-visible:outline-2 focus-visible:outline-accent shell-phone:size-11"
    >
      {copied ? (
        <Check
          className="size-3.5 text-emerald-600 dark:text-emerald-400"
          aria-hidden="true"
        />
      ) : (
        <Copy className="size-3.5" aria-hidden="true" />
      )}
    </button>
  );
}

function StreamingPulse() {
  const reduced = useReducedMotion();
  return (
    <div
      className="inline-flex items-center gap-2 py-1 text-xs text-muted"
      role="status"
      aria-label="Agent is thinking"
    >
      <span className="inline-flex items-center gap-1" aria-hidden="true">
        <span
          className={`size-1.5 rounded-full bg-muted ${reduced ? "" : "animate-pulse"}`}
        />
        <span
          className={`size-1.5 rounded-full bg-muted ${reduced ? "" : "animate-pulse delay-150"}`}
        />
        <span
          className={`size-1.5 rounded-full bg-muted ${reduced ? "" : "animate-pulse delay-300"}`}
        />
      </span>
      <span>Thinking…</span>
    </div>
  );
}

export function MessageItem({
  message,
  isStreaming = false,
  canRetry = false,
  onRetry,
}: {
  message: ChatMessage;
  isStreaming?: boolean;
  canRetry?: boolean;
  onRetry?: () => void;
}) {
  if (message.role === "user") {
    return (
      <article
        className="group mx-auto flex w-full max-w-3xl flex-col items-end gap-1 py-2"
        data-agent-message="user"
      >
        <div className="max-w-xl rounded-2xl border border-line bg-subtle px-4 py-2.5 text-sm leading-relaxed text-foreground whitespace-pre-wrap break-words wrap-anywhere">
          {message.content}
        </div>
        <div className="flex items-center gap-1 opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:opacity-100">
          <CopyAction text={message.content} />
        </div>
      </article>
    );
  }

  const { text, questions } = splitAsk(message.content ?? "");
  const hasTools = Boolean(message.toolCalls && message.toolCalls.length > 0);
  const hasThinking = Boolean(message.thinking && message.thinking.trim());
  const hasVisibleBody =
    Boolean(text.trim()) || Boolean(questions && questions.length > 0);
  const waitingForFirstEvent =
    isStreaming && !hasVisibleBody && !hasTools && !hasThinking;

  return (
    <article
      className="group mx-auto flex w-full max-w-3xl flex-col gap-2 py-3"
      data-agent-message="assistant"
    >
      {hasThinking && (
        <ReasoningBlock
          thinking={message.thinking ?? ""}
          isStreaming={isStreaming && !hasVisibleBody && !hasTools}
        />
      )}

      {hasTools && <ToolCallGroup calls={message.toolCalls ?? []} />}

      {waitingForFirstEvent && <StreamingPulse />}

      {text.trim() !== "" && (
        <div className="min-w-0 text-sm leading-relaxed text-foreground break-words wrap-anywhere">
          <Markdown text={text} density="compact" />
        </div>
      )}

      {questions && questions.length > 0 && (
        <ul className="my-1 flex flex-col gap-1 border-l-2 border-line pl-3 text-sm text-muted">
          {questions.map((q, i) => (
            <li key={i}>{q.question}</li>
          ))}
        </ul>
      )}

      {message.error && (
        <div
          className="flex items-start gap-2.5 rounded-xl border border-danger/30 bg-danger/10 p-3 text-sm text-danger"
          role="alert"
        >
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <p className="m-0 break-words">{message.error}</p>
            {canRetry && onRetry && (
              <div>
                <button
                  type="button"
                  onClick={onRetry}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-danger/40 bg-panel px-2.5 py-1 text-xs font-medium text-danger transition-colors duration-150 ease-out hover:bg-danger/10 hover:transition-none pointer-coarse:min-h-11"
                >
                  <RotateCcw className="size-3" aria-hidden="true" />
                  <span>Try again</span>
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {message.aborted && !isStreaming && (
        <p className="m-0 text-xs text-muted">Response stopped.</p>
      )}

      {!isStreaming && (text.trim() !== "" || canRetry) && (
        <div className="flex items-center gap-1 opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:opacity-100">
          {text.trim() !== "" && <CopyAction text={text} />}
          {canRetry && onRetry && (
            <button
              type="button"
              aria-label="Regenerate response"
              title="Regenerate"
              onClick={onRetry}
              className="inline-flex size-7 items-center justify-center rounded-md text-muted transition-colors duration-150 ease-out hover:bg-subtle hover:text-foreground hover:transition-none focus-visible:outline-2 focus-visible:outline-accent shell-phone:size-11"
            >
              <RotateCcw className="size-3.5" aria-hidden="true" />
            </button>
          )}
        </div>
      )}
    </article>
  );
}
