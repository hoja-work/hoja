import { useMemo, useState } from "react";
import { MobileNavTrigger } from "../mobileNav";
import {
  Bot,
  Check,
  History,
  Pencil,
  Search,
  SquarePen,
  Trash2,
  X,
} from "lucide-react";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../components/ui/popover";
import { confirmDialog } from "../dialogs";
import type { ThreadSummary } from "./types";

function bucketLabel(iso: string): string {
  const parsed = Date.parse(iso);
  if (Number.isNaN(parsed)) return "Earlier";
  const now = new Date();
  const startOfToday = new Date(
    now.getFullYear(),
    now.getMonth(),
    now.getDate(),
  ).getTime();
  const dayMs = 24 * 60 * 60 * 1000;
  if (parsed >= startOfToday) return "Today";
  if (parsed >= startOfToday - dayMs) return "Yesterday";
  if (parsed >= startOfToday - 7 * dayMs) return "Previous 7 days";
  return "Older";
}

function groupThreads(
  threads: ThreadSummary[],
): { label: string; items: ThreadSummary[] }[] {
  const order = ["Today", "Yesterday", "Previous 7 days", "Older", "Earlier"];
  const map = new Map<string, ThreadSummary[]>();
  for (const t of threads) {
    const label = bucketLabel(t.createdAt);
    const list = map.get(label) ?? [];
    list.push(t);
    map.set(label, list);
  }
  return order
    .filter((label) => (map.get(label)?.length ?? 0) > 0)
    .map((label) => ({ label, items: map.get(label)! }));
}

export function ChatHeader({
  title,
  isRunning,
  threads,
  threadsLoading,
  selectedThreadId,
  onSelectThread,
  onNewChat,
  onRenameThread,
  onDeleteThread,
}: {
  title: string;
  isRunning: boolean;
  threads: ThreadSummary[];
  threadsLoading: boolean;
  selectedThreadId: string | null;
  onSelectThread: (threadId: string) => void;
  onNewChat: () => void;
  onRenameThread: (threadId: string, nextTitle: string) => Promise<void>;
  onDeleteThread: (threadId: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return threads;
    return threads.filter((t) => t.title.toLowerCase().includes(q));
  }, [threads, query]);

  const groups = useMemo(() => groupThreads(filtered), [filtered]);

  const commitRename = async (threadId: string) => {
    const trimmed = draftTitle.trim();
    setEditingId(null);
    if (!trimmed) return;
    await onRenameThread(threadId, trimmed);
  };

  const handleDelete = async (thread: ThreadSummary) => {
    const confirmed = await confirmDialog(
      `Delete conversation "${thread.title}"?`,
      { danger: true },
    );
    if (!confirmed) return;
    await onDeleteThread(thread.id);
  };

  return (
    <header
      className="flex h-12 shrink-0 items-center gap-2 border-b border-line bg-bg px-4"
      data-agent-header=""
    >
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <MobileNavTrigger />
        <Bot className="size-4 shrink-0 text-muted" aria-hidden="true" />
        <span className="min-w-0 truncate text-sm font-medium text-foreground">
          {title || "New chat"}
        </span>
        {isRunning && (
          <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-line bg-panel px-2 py-0.5 text-xs text-muted">
            <span
              className="size-1.5 rounded-full bg-emerald-500"
              aria-hidden="true"
            />
            <span>Active</span>
          </span>
        )}
      </div>

      <div className="flex shrink-0 items-center gap-1">
        <Popover
          open={open}
          onOpenChange={(next) => {
            setOpen(next);
            if (!next) {
              setEditingId(null);
              setQuery("");
            }
          }}
        >
          <PopoverTrigger asChild>
            <button
              type="button"
              aria-haspopup="dialog"
              aria-expanded={open}
              aria-controls="agent-history"
              aria-label="History"
              className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-sm text-muted transition-colors duration-150 ease-out hover:bg-subtle hover:text-foreground hover:transition-none aria-expanded:bg-subtle aria-expanded:text-foreground focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11 shell-phone:min-w-11"
            >
              <History className="size-4" aria-hidden="true" />
              <span className="hidden sm:inline">History</span>
              {threads.length > 0 && (
                <span className="hidden rounded-full bg-subtle px-1.5 text-xs text-muted tabular-nums sm:inline">
                  {threads.length}
                </span>
              )}
            </button>
          </PopoverTrigger>
          <PopoverContent
            id="agent-history"
            role="dialog"
            aria-label="Chat history"
            align="end"
            sideOffset={6}
            className="z-50 flex max-h-[min(24rem,var(--radix-popover-content-available-height))] w-80 max-w-[calc(100vw-16px)] flex-col overflow-hidden rounded-xl border border-line bg-panel p-2 text-foreground shadow-lg"
          >
            <label className="mb-2 flex items-center gap-2 rounded-lg border border-line bg-bg px-2.5 py-1.5 text-muted focus-within:border-foreground/30 pointer-coarse:min-h-11">
              <Search className="size-3.5 shrink-0" aria-hidden="true" />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search conversations…"
                aria-label="Search conversations"
                className="min-w-0 flex-1 border-0 bg-transparent text-sm text-foreground placeholder:text-muted focus:outline-none"
              />
            </label>

            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
              {threadsLoading && threads.length === 0 ? (
                <p className="p-4 text-center text-xs text-muted" role="status">
                  Loading conversations…
                </p>
              ) : groups.length === 0 ? (
                <p className="p-4 text-center text-xs text-muted">
                  {query.trim()
                    ? "No conversations match your search."
                    : "No saved conversations yet."}
                </p>
              ) : (
                <div className="flex flex-col gap-2">
                  {groups.map((group) => (
                    <div key={group.label} className="flex flex-col gap-0.5">
                      <span className="px-2 py-1 text-xs font-medium text-muted">
                        {group.label}
                      </span>
                      {group.items.map((thread) => {
                        const isSelected = thread.id === selectedThreadId;
                        const isEditing = editingId === thread.id;
                        return (
                          <div
                            key={thread.id}
                            className={`group/row flex items-center gap-1 rounded-lg px-2 py-1.5 pointer-coarse:min-h-11 pointer-coarse:py-0 transition-colors duration-150 ease-out hover:bg-subtle hover:transition-none ${
                              isSelected ? "bg-subtle" : ""
                            }`}
                          >
                            {isEditing ? (
                              <form
                                className="flex min-w-0 flex-1 items-center gap-1"
                                onSubmit={(e) => {
                                  e.preventDefault();
                                  void commitRename(thread.id);
                                }}
                              >
                                <input
                                  type="text"
                                  value={draftTitle}
                                  onChange={(e) =>
                                    setDraftTitle(e.target.value)
                                  }
                                  onKeyDown={(e) => {
                                    if (e.key === "Escape") {
                                      e.stopPropagation();
                                      setEditingId(null);
                                    }
                                  }}
                                  aria-label="Conversation title"
                                  className="min-w-0 flex-1 rounded border border-line bg-bg px-2 py-1 text-xs text-foreground focus:border-foreground/40 focus:outline-none"
                                />
                                <button
                                  type="submit"
                                  aria-label="Save title"
                                  className="inline-flex size-6 pointer-coarse:size-11 items-center justify-center rounded text-muted hover:bg-panel hover:text-foreground"
                                >
                                  <Check
                                    className="size-3.5"
                                    aria-hidden="true"
                                  />
                                </button>
                                <button
                                  type="button"
                                  aria-label="Cancel rename"
                                  onClick={() => setEditingId(null)}
                                  className="inline-flex size-6 pointer-coarse:size-11 items-center justify-center rounded text-muted hover:bg-panel hover:text-foreground"
                                >
                                  <X className="size-3.5" aria-hidden="true" />
                                </button>
                              </form>
                            ) : (
                              <>
                                <button
                                  type="button"
                                  onClick={() => {
                                    onSelectThread(thread.id);
                                    setOpen(false);
                                  }}
                                  className="min-w-0 flex-1 truncate bg-transparent p-0 text-left text-sm text-foreground pointer-coarse:self-stretch focus-visible:outline-2 focus-visible:outline-accent"
                                  title={thread.title}
                                >
                                  {thread.title}
                                </button>
                                <div className="flex shrink-0 items-center gap-0.5 opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 pointer-coarse:opacity-100">
                                  <button
                                    type="button"
                                    aria-label={`Rename ${thread.title}`}
                                    title="Rename"
                                    onClick={() => {
                                      setEditingId(thread.id);
                                      setDraftTitle(thread.title);
                                    }}
                                    className="inline-flex size-6 pointer-coarse:size-11 items-center justify-center rounded text-muted transition-colors duration-150 ease-out hover:bg-panel hover:text-foreground hover:transition-none"
                                  >
                                    <Pencil
                                      className="size-3"
                                      aria-hidden="true"
                                    />
                                  </button>
                                  <button
                                    type="button"
                                    aria-label={`Delete ${thread.title}`}
                                    title="Delete"
                                    onClick={() => void handleDelete(thread)}
                                    className="inline-flex size-6 pointer-coarse:size-11 items-center justify-center rounded text-muted transition-colors duration-150 ease-out hover:bg-panel hover:text-danger hover:transition-none"
                                  >
                                    <Trash2
                                      className="size-3"
                                      aria-hidden="true"
                                    />
                                  </button>
                                </div>
                              </>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </PopoverContent>
        </Popover>

        <button
          type="button"
          onClick={() => {
            setOpen(false);
            onNewChat();
          }}
          aria-label="New chat"
          className="inline-flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-sm text-muted transition-colors duration-150 ease-out hover:bg-subtle hover:text-foreground hover:transition-none focus-visible:outline-2 focus-visible:outline-accent shell-phone:min-h-11 shell-phone:min-w-11"
        >
          <SquarePen className="size-4" aria-hidden="true" />
          <span className="hidden sm:inline">New chat</span>
        </button>
      </div>
    </header>
  );
}
