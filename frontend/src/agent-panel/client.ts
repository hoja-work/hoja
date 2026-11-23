import {
  makeMessageId,
  type ChatMessage,
  type ThreadSummary,
  type ToolCallRecord,
} from "./types";

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("hoja_token");
  return token && token !== "session"
    ? { authorization: `Bearer ${token}` }
    : {};
}

/* The token is read on every request, not once when this module loads: a sign-in, sign-out or
 * workspace switch after the first import must never keep sending an older token. */
export function authedFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const headers = new Headers(init?.headers);
  headers.delete("authorization");
  for (const [name, value] of Object.entries(authHeaders()))
    headers.set(name, value);
  return fetch(input, { ...init, headers, credentials: "include" });
}

async function readError(res: Response, fallback: string): Promise<string> {
  try {
    const body = (await res.json()) as {
      error?: string;
      errors?: { message?: string }[];
    };
    if (typeof body?.error === "string" && body.error) return body.error;
    if (Array.isArray(body?.errors) && body.errors[0]?.message)
      return body.errors[0].message;
  } catch {
    /* Non-JSON body; use HTTP fallback. */
  }
  return `${fallback} (HTTP ${res.status})`;
}

export async function listThreads(): Promise<ThreadSummary[]> {
  const res = await authedFetch("/api/agent/threads/get");
  if (!res.ok) throw new Error(await readError(res, "Could not load history"));
  const data = (await res.json()) as { threads?: ThreadSummary[] };
  return Array.isArray(data.threads) ? data.threads : [];
}

type RawThreadMessage = {
  role?: string;
  content?: unknown;
  toolCalls?: {
    id?: string;
    name?: string;
    args?: unknown;
    status?: string;
    output?: string | null;
    error?: string | null;
    durationMs?: number | null;
  }[];
};

function normalizeToolStatus(status?: string): ToolCallRecord["status"] {
  if (status === "error") return "error";
  if (status === "running") return "running";
  return "completed";
}

function normalizeContent(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) =>
        part && typeof part === "object" && "text" in part
          ? String((part as { text: unknown }).text)
          : "",
      )
      .join("");
  }
  return "";
}

export async function getThreadMessages(
  threadId: string,
): Promise<ChatMessage[]> {
  const res = await authedFetch(
    `/api/agent/threads/get/${encodeURIComponent(threadId)}`,
  );
  if (!res.ok)
    throw new Error(await readError(res, "Could not load conversation"));
  const rows = (await res.json()) as RawThreadMessage[];
  if (!Array.isArray(rows)) return [];
  return rows
    .filter((m) => m.role === "user" || m.role === "assistant")
    .map((m, idx) => {
      const toolCalls: ToolCallRecord[] | undefined = Array.isArray(m.toolCalls)
        ? m.toolCalls
            .filter((tc) => tc && typeof tc.name === "string")
            .map((tc, j) => ({
              id: tc.id || `tc-${idx}-${j}`,
              name: tc.name as string,
              args: tc.args,
              status: normalizeToolStatus(tc.status),
              output: tc.output ?? null,
              error: tc.error ?? null,
              durationMs:
                typeof tc.durationMs === "number" ? tc.durationMs : null,
            }))
        : undefined;
      return {
        id: makeMessageId(`${threadId}-${idx}`),
        role: m.role as "user" | "assistant",
        content: normalizeContent(m.content),
        ...(toolCalls && toolCalls.length > 0 ? { toolCalls } : {}),
      };
    });
}

export async function renameThread(
  threadId: string,
  title: string,
): Promise<ThreadSummary> {
  const res = await authedFetch(
    `/api/agent/threads/update/${encodeURIComponent(threadId)}`,
    {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ id: threadId, title }),
    },
  );
  if (!res.ok) throw new Error(await readError(res, "Could not rename chat"));
  return (await res.json()) as ThreadSummary;
}

export async function deleteThread(threadId: string): Promise<void> {
  const res = await authedFetch(
    `/api/agent/threads/delete/${encodeURIComponent(threadId)}`,
    { method: "DELETE" },
  );
  if (!res.ok) throw new Error(await readError(res, "Could not delete chat"));
}

export type StreamEvent =
  | { type: "thread"; threadId: string }
  | { type: "content"; delta: string }
  | { type: "thinking"; delta: string }
  | { type: "tool_start"; id: string; name: string; args?: unknown }
  | {
      type: "tool_end";
      id: string;
      name: string;
      status: "ok" | "error";
      output?: string | null;
      error?: string | null;
      durationMs?: number | null;
    };

export async function streamChatTurn({
  threadId,
  messages,
  signal,
  onEvent,
}: {
  threadId: string | null;
  messages: { role: "user" | "assistant"; content: string }[];
  signal: AbortSignal;
  onEvent: (event: StreamEvent) => void;
}): Promise<void> {
  const res = await authedFetch("/api/agent/chat", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      ...(threadId ? { threadId } : {}),
      messages,
    }),
    signal,
  });

  if (!res.ok) {
    throw new Error(await readError(res, "Could not reach the agent"));
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error("The agent response stream was empty.");
  }

  const decoder = new TextDecoder();
  let buffer = "";

  const processLine = (rawLine: string) => {
    const line = rawLine.trim();
    if (!line.startsWith("data:")) return;
    const payload = line.slice(5).trim();
    if (!payload || payload === "[DONE]") return;
    try {
      const parsed = JSON.parse(payload) as {
        choices?: {
          delta?: {
            content?: string;
            hoja_event?: Record<string, unknown>;
          };
        }[];
      };
      const delta = parsed.choices?.[0]?.delta;
      if (!delta) return;
      if (typeof delta.content === "string" && delta.content) {
        onEvent({ type: "content", delta: delta.content });
      }
      const ev = delta.hoja_event;
      if (ev && typeof ev.type === "string") {
        if (ev.type === "thread" && typeof ev.threadId === "string") {
          onEvent({ type: "thread", threadId: ev.threadId });
        } else if (ev.type === "thinking" && typeof ev.delta === "string") {
          onEvent({ type: "thinking", delta: ev.delta });
        } else if (
          ev.type === "tool_start" &&
          typeof ev.id === "string" &&
          typeof ev.name === "string"
        ) {
          onEvent({
            type: "tool_start",
            id: ev.id,
            name: ev.name,
            args: ev.args,
          });
        } else if (
          ev.type === "tool_end" &&
          typeof ev.id === "string" &&
          typeof ev.name === "string"
        ) {
          onEvent({
            type: "tool_end",
            id: ev.id,
            name: ev.name,
            status: ev.status === "error" ? "error" : "ok",
            output: typeof ev.output === "string" ? ev.output : null,
            error: typeof ev.error === "string" ? ev.error : null,
            durationMs:
              typeof ev.durationMs === "number" ? ev.durationMs : null,
          });
        }
      }
    } catch {
      /* Ignore malformed SSE chunk rather than tearing down the turn. */
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newlineIndex = buffer.indexOf("\n");
    while (newlineIndex !== -1) {
      const line = buffer.slice(0, newlineIndex);
      buffer = buffer.slice(newlineIndex + 1);
      processLine(line);
      newlineIndex = buffer.indexOf("\n");
    }
  }
  if (buffer.trim()) {
    processLine(buffer);
  }
}
