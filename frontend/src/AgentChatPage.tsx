import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown } from "lucide-react";
import {
  deleteThread,
  getThreadMessages,
  listThreads,
  renameThread,
  streamChatTurn,
  type StreamEvent,
} from "./agent-panel/client";
import { AskWizard } from "./agent-panel/AskWizard";
import { ChatHeader } from "./agent-panel/ChatHeader";
import { Composer } from "./agent-panel/Composer";
import { MessageItem } from "./agent-panel/MessageItem";
import { WelcomeScreen } from "./agent-panel/WelcomeScreen";
import {
  makeMessageId,
  splitAsk,
  type ChatMessage,
  type ThreadSummary,
  type ToolCallRecord,
} from "./agent-panel/types";

// Preserved exports so callers that imported these from AgentChatPage keep working.
export { linkIssueIds } from "./markdown";
export { splitAsk } from "./agent-panel/types";

function applyStreamEvent(msg: ChatMessage, event: StreamEvent): ChatMessage {
  if (event.type === "content") {
    return { ...msg, content: msg.content + event.delta };
  }
  if (event.type === "thinking") {
    return { ...msg, thinking: (msg.thinking ?? "") + event.delta };
  }
  if (event.type === "tool_start") {
    const existing = msg.toolCalls ?? [];
    const next: ToolCallRecord = {
      id: event.id,
      name: event.name,
      args: event.args,
      status: "running",
    };
    const index = existing.findIndex((t) => t.id === event.id);
    const updated =
      index >= 0
        ? existing.map((t, i) => (i === index ? { ...t, ...next } : t))
        : [...existing, next];
    return { ...msg, toolCalls: updated };
  }
  if (event.type === "tool_end") {
    const existing = msg.toolCalls ?? [];
    const status: ToolCallRecord["status"] =
      event.status === "error" ? "error" : "completed";
    const index = existing.findIndex((t) => t.id === event.id);
    const updated =
      index >= 0
        ? existing.map((t, i) =>
            i === index
              ? {
                  ...t,
                  status,
                  output: event.output ?? t.output,
                  error: event.error ?? t.error,
                  durationMs: event.durationMs ?? t.durationMs,
                }
              : t,
          )
        : [
            ...existing,
            {
              id: event.id,
              name: event.name,
              status,
              output: event.output,
              error: event.error,
              durationMs: event.durationMs,
            },
          ];
    return { ...msg, toolCalls: updated };
  }
  return msg;
}

// Same query as the `shell-phone` variant (tailwind.css): phone portrait and landscape.
const SHELL_PHONE_QUERY = "(max-width: 880px), (max-width: 1000px) and (max-height: 500px)";

function useShellPhone(): boolean {
  const [phone, setPhone] = useState(
    () => typeof window.matchMedia === "function" && window.matchMedia(SHELL_PHONE_QUERY).matches,
  );
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(SHELL_PHONE_QUERY);
    const sync = () => setPhone(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  return phone;
}

export function AgentChatPage({ workspaceName }: { workspaceName: string }) {
  const phone = useShellPhone();
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [threadsLoading, setThreadsLoading] = useState(true);
  const [selectedThreadId, setSelectedThreadId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loadingThread, setLoadingThread] = useState(false);
  const [threadError, setThreadError] = useState("");
  const [draft, setDraft] = useState("");
  const [streamingId, setStreamingId] = useState<string | null>(null);
  const [showJumpButton, setShowJumpButton] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const lastScrollTopRef = useRef(0);
  const loadGenRef = useRef(0);

  const isRunning = streamingId !== null;

  const refreshThreads = useCallback(async () => {
    try {
      const list = await listThreads();
      setThreads(list);
    } catch {
      /* History load failure shouldn't block composing a new chat. */
    } finally {
      setThreadsLoading(false);
    }
  }, []);

  useEffect(() => {
    void refreshThreads();
    return () => {
      abortRef.current?.abort();
    };
  }, [refreshThreads]);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    stickRef.current = true;
    setShowJumpButton(false);
    el.scrollTop = el.scrollHeight;
    lastScrollTopRef.current = el.scrollTop;
  }, []);

  // Stay pinned to the bottom while streaming unless the user scrolled up.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const onScroll = () => {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      if (el.scrollTop < lastScrollTopRef.current - 2) {
        stickRef.current = distFromBottom < 72;
      } else if (distFromBottom < 72) {
        stickRef.current = true;
      }
      lastScrollTopRef.current = el.scrollTop;
      setShowJumpButton(!stickRef.current && distFromBottom > 120);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (messages.length === 0) {
      // The welcome screen reads from the top; pinning it to the bottom hid its heading
      // under the chat header on short phones.
      el.scrollTop = 0;
      lastScrollTopRef.current = 0;
      return;
    }
    if (stickRef.current) {
      el.scrollTop = el.scrollHeight;
      lastScrollTopRef.current = el.scrollTop;
    }
  }, [messages, loadingThread, phone]);

  const selectThread = useCallback(async (threadId: string) => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStreamingId(null);
    setSelectedThreadId(threadId);
    setThreadError("");
    setLoadingThread(true);
    const mine = ++loadGenRef.current;
    try {
      const rows = await getThreadMessages(threadId);
      if (mine !== loadGenRef.current) return;
      setMessages(rows);
      stickRef.current = true;
      setShowJumpButton(false);
    } catch (e) {
      if (mine !== loadGenRef.current) return;
      setThreadError((e as Error).message);
      setMessages([]);
    } finally {
      if (mine === loadGenRef.current) setLoadingThread(false);
    }
  }, []);

  const startNewChat = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    loadGenRef.current++;
    setStreamingId(null);
    setSelectedThreadId(null);
    setMessages([]);
    setThreadError("");
    setDraft("");
    stickRef.current = true;
    setShowJumpButton(false);
  }, []);

  const sendPrompt = useCallback(
    async (rawText: string, baseMessages?: ChatMessage[]) => {
      const prompt = rawText.trim();
      if (!prompt) return;

      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      const prior = baseMessages ?? messages;
      const userMsg: ChatMessage = {
        id: makeMessageId("user"),
        role: "user",
        content: prompt,
      };
      const assistantId = makeMessageId("assistant");
      const assistantPlaceholder: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
      };

      const nextHistory = [...prior, userMsg];
      setMessages([...nextHistory, assistantPlaceholder]);
      setDraft("");
      setThreadError("");
      setStreamingId(assistantId);
      stickRef.current = true;
      setShowJumpButton(false);

      const wireMessages = nextHistory.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      let resolvedThreadId = selectedThreadId;

      try {
        await streamChatTurn({
          threadId: selectedThreadId,
          messages: wireMessages,
          signal: controller.signal,
          onEvent: (event) => {
            if (event.type === "thread") {
              resolvedThreadId = event.threadId;
              setSelectedThreadId(event.threadId);
              return;
            }
            setMessages((prev) =>
              prev.map((m) =>
                m.id === assistantId ? applyStreamEvent(m, event) : m,
              ),
            );
          },
        });
      } catch (err) {
        if (controller.signal.aborted) {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, aborted: true } : m,
            ),
          );
        } else {
          const message =
            err instanceof Error ? err.message : "Could not complete request.";
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, error: message } : m,
            ),
          );
        }
      } finally {
        if (abortRef.current === controller) {
          abortRef.current = null;
          setStreamingId(null);
        }
        // Mark any tool still stuck in "running" as completed/error when stream ends.
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id !== assistantId || !m.toolCalls?.length) return m;
            const settled = m.toolCalls.map((tc) =>
              tc.status === "running"
                ? {
                    ...tc,
                    status: (controller.signal.aborted
                      ? "error"
                      : "completed") as ToolCallRecord["status"],
                  }
                : tc,
            );
            return { ...m, toolCalls: settled };
          }),
        );
        if (resolvedThreadId || !selectedThreadId) {
          void refreshThreads();
        }
      }
    },
    [messages, refreshThreads, selectedThreadId],
  );

  const stopGeneration = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const retryLastTurn = useCallback(() => {
    if (isRunning || messages.length === 0) return;
    let lastUserIdx = -1;
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === "user") {
        lastUserIdx = i;
        break;
      }
    }
    if (lastUserIdx < 0) return;
    const prompt = messages[lastUserIdx].content;
    const prior = messages.slice(0, lastUserIdx);
    void sendPrompt(prompt, prior);
  }, [isRunning, messages, sendPrompt]);

  const handleRenameThread = useCallback(
    async (threadId: string, nextTitle: string) => {
      const updated = await renameThread(threadId, nextTitle);
      setThreads((prev) => prev.map((t) => (t.id === threadId ? updated : t)));
    },
    [],
  );

  const handleDeleteThread = useCallback(
    async (threadId: string) => {
      await deleteThread(threadId);
      setThreads((prev) => prev.filter((t) => t.id !== threadId));
      if (selectedThreadId === threadId) {
        startNewChat();
      }
    },
    [selectedThreadId, startNewChat],
  );

  const activeTitle = useMemo(() => {
    if (selectedThreadId) {
      const found = threads.find((t) => t.id === selectedThreadId);
      if (found) return found.title;
    }
    const firstUser = messages.find((m) => m.role === "user");
    return firstUser ? firstUser.content.slice(0, 60) : "New chat";
  }, [messages, selectedThreadId, threads]);

  // While the latest assistant reply carries an unanswered `hoja-ask` question, show the wizard.
  const pendingAsk = useMemo(() => {
    if (isRunning || messages.length === 0) return null;
    const last = messages[messages.length - 1];
    if (last.role !== "assistant" || !last.content) return null;
    const { questions } = splitAsk(last.content);
    return questions ? { key: last.id, questions } : null;
  }, [isRunning, messages]);

  const hasMessages = messages.length > 0;
  // On phones the wizard joins the conversation's scroll instead of being pinned under it:
  // pinned, it covered the question it answers and hid Next inside a nested scroller.
  const askWizard = pendingAsk ? (
    <AskWizard
      key={pendingAsk.key}
      questions={pendingAsk.questions}
      onSubmit={(answer) => void sendPrompt(answer)}
      disabled={isRunning}
      inline={phone}
    />
  ) : null;
  const inlineAsk = phone && askWizard !== null;
  const inlineAskKey = inlineAsk ? pendingAsk?.key : undefined;
  useEffect(() => {
    // The inline wizard mounts when the reply finishes; keep its Next button in view.
    const el = scrollRef.current;
    if (!el || !inlineAskKey || !stickRef.current) return;
    el.scrollTop = el.scrollHeight;
    lastScrollTopRef.current = el.scrollTop;
  }, [inlineAskKey]);

  return (
    <section
      className="agent-chat-page flex min-h-0 flex-1 flex-col overflow-hidden bg-bg text-foreground"
      data-surface="agent-chat"
      data-agent-panel=""
      aria-label="Agent chat"
    >
      <ChatHeader
        title={activeTitle}
        isRunning={isRunning}
        threads={threads}
        threadsLoading={threadsLoading}
        selectedThreadId={selectedThreadId}
        onSelectThread={(id) => void selectThread(id)}
        onNewChat={startNewChat}
        onRenameThread={handleRenameThread}
        onDeleteThread={handleDeleteThread}
      />

      <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden">
        <div
          ref={scrollRef}
          className="flex min-h-0 flex-1 flex-col overflow-y-auto overscroll-contain px-4 py-4 sm:px-6"
          data-agent-scroll=""
        >
          {loadingThread ? (
            <div
              className="flex flex-1 items-center justify-center p-4 text-sm text-muted"
              role="status"
            >
              Loading conversation…
            </div>
          ) : threadError ? (
            <div
              className="mx-auto my-auto flex max-w-md flex-col items-center gap-3 rounded-xl border border-line bg-panel p-6 text-center"
              role="alert"
            >
              <p className="m-0 text-sm text-danger">{threadError}</p>
              <button
                type="button"
                onClick={startNewChat}
                className="rounded-lg border border-line bg-panel px-3 py-1.5 text-sm font-medium text-foreground transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none"
              >
                Start a new chat
              </button>
            </div>
          ) : !hasMessages ? (
            <WelcomeScreen
              workspaceName={workspaceName}
              onSelectPrompt={(prompt) => void sendPrompt(prompt)}
              disabled={isRunning}
            />
          ) : (
            <div
              className="flex flex-col gap-1 pb-4"
              role="log"
              aria-label="Conversation"
              aria-live="polite"
            >
              {messages.map((msg, idx) => {
                const isLast = idx === messages.length - 1;
                return (
                  <MessageItem
                    key={msg.id}
                    message={msg}
                    isStreaming={msg.id === streamingId}
                    canRetry={isLast && msg.role === "assistant" && !isRunning}
                    onRetry={retryLastTurn}
                  />
                );
              })}
              {inlineAsk && <div className="pt-2">{askWizard}</div>}
            </div>
          )}
        </div>

        {showJumpButton && (
          <div className="pointer-events-none absolute inset-x-0 bottom-2 flex justify-center px-4">
            <button
              type="button"
              onClick={scrollToBottom}
              className="pointer-events-auto inline-flex items-center gap-1.5 rounded-full border border-line bg-panel px-3 py-1.5 text-xs font-medium pointer-coarse:min-h-11 pointer-coarse:px-4 text-foreground shadow-md transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none focus-visible:outline-2 focus-visible:outline-accent"
            >
              <ArrowDown className="size-3.5" aria-hidden="true" />
              <span>Jump to latest</span>
            </button>
          </div>
        )}
      </div>

      {!inlineAsk && (
        <div className="shrink-0 bg-bg px-4 pt-2 pb-4 sm:px-6">
          {askWizard ?? (
            <Composer
              value={draft}
              onChange={setDraft}
              onSend={() => void sendPrompt(draft)}
              onStop={stopGeneration}
              isRunning={isRunning}
            />
          )}
        </div>
      )}
    </section>
  );
}
