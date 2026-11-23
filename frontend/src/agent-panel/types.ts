export type ToolCallStatus = "running" | "completed" | "error";

export interface ToolCallRecord {
  id: string;
  name: string;
  args?: unknown;
  status: ToolCallStatus;
  output?: string | null;
  error?: string | null;
  durationMs?: number | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking?: string;
  toolCalls?: ToolCallRecord[];
  error?: string;
  aborted?: boolean;
}

export interface ThreadSummary {
  id: string;
  title: string;
  createdAt: string;
}

export type AskOption = { label: string; description?: string };

export type AskQuestion = {
  question: string;
  header?: string;
  options?: AskOption[];
  multiple?: boolean;
  allow_other?: boolean;
};

export type AskAnswer = {
  picked: string[];
  other: string;
  otherOn: boolean;
  note: string;
};

export const EMPTY_ANSWER: AskAnswer = {
  picked: [],
  other: "",
  otherOn: false,
  note: "",
};

// The agent's ask_user tool arrives as a ```hoja-ask fenced JSON block at the end of the reply.
const ASK_BLOCK = /```hoja-ask\n([\s\S]*?)\n```/;

export function splitAsk(content: string): {
  text: string;
  questions: AskQuestion[] | null;
} {
  const match = content.match(ASK_BLOCK);
  if (!match) return { text: content, questions: null };
  try {
    const parsed = JSON.parse(match[1]) as { questions?: AskQuestion[] };
    if (!Array.isArray(parsed.questions) || parsed.questions.length === 0)
      return { text: content, questions: null };
    return {
      text: content.replace(ASK_BLOCK, "").trim(),
      questions: parsed.questions,
    };
  } catch {
    return { text: content.replace(ASK_BLOCK, "").trim(), questions: null };
  }
}

export function answerText(q: AskQuestion, a: AskAnswer): string {
  const values = [...a.picked];
  if ((a.otherOn || !q.options?.length) && a.other.trim())
    values.push(a.other.trim());
  let line = values.join(", ");
  if (a.note.trim()) line += line ? ` (${a.note.trim()})` : a.note.trim();
  return line;
}

export function makeMessageId(prefix = "msg"): string {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}
