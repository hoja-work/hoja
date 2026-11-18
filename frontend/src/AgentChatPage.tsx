import React from "react";
import {
  AgentInterface,
  fetchLLM,
  openAIAdapter,
  openAIMessageFormat,
  restStorage,
} from "@openuidev/react-ui";
import { useThread, useThreadList } from "@openuidev/react-headless";
import { Markdown } from "./markdown";
import { DelegationCard, splitDelegations } from "./DelegationCard";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { History, SquarePen } from "lucide-react";
import {
  Popover,
  PopoverTrigger,
  PopoverContent,
} from "./components/ui/popover";

// The shared renderer lives in ./markdown. Re-exported so callers that imported
// linkIssueIds from here keep working.
export { linkIssueIds } from "./markdown";

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem("hoja_token");
  return token && token !== "session"
    ? { authorization: `Bearer ${token}` }
    : {};
}

// One thread per chat: the library sends threadId on every turn, and the server appends to that thread.
const llm = fetchLLM({
  url: "/api/agent/chat",
  streamAdapter: openAIAdapter(),
  messageFormat: openAIMessageFormat,
  headers: authHeaders(),
  fetch: (input, init) => fetch(input, { ...init, credentials: "include" }),
});

const storage = restStorage({
  baseUrl: "/api/agent/threads",
  messageFormat: openAIMessageFormat,
  headers: authHeaders(),
  fetch: (input, init) => fetch(input, { ...init, credentials: "include" }),
});

type AskOption = { label: string; description?: string };
type AskQuestion = {
  question: string;
  header?: string;
  options?: AskOption[];
  multiple?: boolean;
  allow_other?: boolean;
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

type AskAnswer = {
  picked: string[];
  other: string;
  otherOn: boolean;
  note: string;
};
const EMPTY_ANSWER: AskAnswer = {
  picked: [],
  other: "",
  otherOn: false,
  note: "",
};

function answerText(q: AskQuestion, a: AskAnswer): string {
  const values = [...a.picked];
  if ((a.otherOn || !q.options?.length) && a.other.trim())
    values.push(a.other.trim());
  let line = values.join(", ");
  if (a.note.trim()) line += line ? ` (${a.note.trim()})` : a.note.trim();
  return line;
}

// Shows one question at a time in place of the composer; answers are sent together as one user message.
function AskWizard({ questions }: { questions: AskQuestion[] }) {
  const processMessage = useThread((s) => s.processMessage);
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState<AskAnswer[]>(() =>
    questions.map(() => ({ ...EMPTY_ANSWER })),
  );
  const q = questions[step];
  const a = answers[step];
  const options = q.options ?? [];
  const allowOther = options.length === 0 || q.allow_other !== false;
  const last = step === questions.length - 1;
  const answered = answerText(q, a) !== "";
  const update = (patch: Partial<AskAnswer>) =>
    setAnswers((prev) =>
      prev.map((x, j) => (j === step ? { ...x, ...patch } : x)),
    );
  const toggle = (label: string) => {
    if (q.multiple)
      update({
        picked: a.picked.includes(label)
          ? a.picked.filter((x) => x !== label)
          : [...a.picked, label],
      });
    else update({ picked: [label], otherOn: false });
  };
  const next = () => {
    if (!answered) return;
    if (!last) {
      setStep(step + 1);
      return;
    }
    const body =
      questions.length === 1
        ? answerText(q, a)
        : questions
            .map(
              (x, i) =>
                `${x.header || x.question}: ${answerText(x, answers[i])}`,
            )
            .join("\n");
    void processMessage({ role: "user", content: body });
  };
  return (
    <form
      className="flex flex-col overflow-auto border border-line gap-2.5 w-full max-w-(--ag-max) mt-0 mr-auto mb-0 ml-auto pt-3.5 pr-3.5 pb-3.5 pl-3.5 border-solid rounded-(--ag-radius) bg-panel shadow-(--shadow) max-h-[60vh]"
      aria-label="Agent question"
      onSubmit={(e) => {
        e.preventDefault();
        next();
      }}
    >
      <div className="flex items-center justify-between gap-2 min-h-4">
        {q.header && (
          <span className="text-[11px] font-semibold uppercase tracking-[.04em] text-muted">
            {q.header}
          </span>
        )}
        {questions.length > 1 && (
          <span className="ml-auto text-[12px] text-muted">
            {step + 1} of {questions.length}
          </span>
        )}
      </div>
      <fieldset
        key={step}
        className="flex flex-col border-0 gap-1.5 mt-0 mr-0 mb-0 ml-0 pt-0 pr-0 pb-0 pl-0 min-w-0"
      >
        <legend className="flex flex-col gap-0.5 pt-0 pr-0 pb-0 pl-0 mb-1.5">
          <span className="font-semibold">{q.question}</span>
          {q.multiple && (
            <span className="text-[12px] text-muted">
              Select all that apply
            </span>
          )}
        </legend>
        {options.map((o) => (
          <label
            key={o.label}
            className={
              `flex cursor-pointer items-start border ${a.picked.includes(o.label) ? "border-foreground" : "border-line"}` +
              " flex gap-2.5 items-start pt-2 pr-2.5 pb-2 pl-2.5 border border-solid border-line rounded-lg cursor-pointer"
            }
          >
            <input
              className="shell-phone:min-h-11 pointer-coarse:min-h-11 mt-0.75"
              type={q.multiple ? "checkbox" : "radio"}
              name={`ask-${step}`}
              checked={a.picked.includes(o.label)}
              onChange={() => toggle(o.label)}
            />
            <span className="flex flex-col gap-0.5">
              <span className="">{o.label}</span>
              {o.description && (
                <span className="text-[12px] text-muted">{o.description}</span>
              )}
            </span>
          </label>
        ))}
        {allowOther && options.length > 0 && (
          <label
            className={
              `flex cursor-pointer items-start border ${a.otherOn ? "border-foreground" : "border-line"}` +
              " flex gap-2.5 items-start pt-2 pr-2.5 pb-2 pl-2.5 border border-solid border-line rounded-lg cursor-pointer"
            }
          >
            <input
              className="shell-phone:min-h-11 pointer-coarse:min-h-11 mt-0.75"
              type={q.multiple ? "checkbox" : "radio"}
              name={`ask-${step}`}
              checked={a.otherOn}
              onChange={() =>
                update(
                  q.multiple
                    ? { otherOn: !a.otherOn }
                    : { otherOn: true, picked: [] },
                )
              }
            />
            <span className="flex flex-col gap-0.5">Other</span>
          </label>
        )}
        {allowOther && (options.length === 0 || a.otherOn) && (
          <textarea
            className="resize-y border border-line shell-phone:min-h-11 pointer-coarse:min-h-11 w-full pt-2 pr-2.5 pb-2 pl-2.5 border-solid rounded-lg [font:inherit] bg-bg text-foreground"
            rows={2}
            placeholder="Write your answer…"
            value={a.other}

            onChange={(e) => update({ other: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                next();
              }
            }}
          />
        )}
        {options.length > 0 && (
          <input
            className="resize-y border border-line shell-phone:min-h-11 pointer-coarse:min-h-11 w-full pt-2 pr-2.5 pb-2 pl-2.5 border-solid rounded-lg [font:inherit] bg-bg text-foreground"
            type="text"
            placeholder="Add details (optional)"
            value={a.note}
            onChange={(e) => update({ note: e.target.value })}
          />
        )}
      </fieldset>
      <div className="flex justify-end gap-2">
        {step > 0 && (
          <button
            type="button"
            className="shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 pt-1.75 pr-3 pb-1.75 pl-3 rounded-md bg-subtle"
            onClick={() => setStep(step - 1)}
          >
            Back
          </button>
        )}
        <button
          type="submit"
          className="inline-flex items-center justify-center hover:brightness-110 disabled:opacity-50 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-1.75 rounded-none bg-accent text-white pt-2.25 pr-3.5 pb-2.25 pl-3.5 font-[650] shadow-none hover:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] hover:not-disabled:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] hover:not-disabled:filter-none active:not-disabled:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 [html[data-theme=dark]_&]:text-[#171719]"
          disabled={!answered}
        >
          {last ? "Send" : "Next"}
        </button>
      </div>
    </form>
  );
}

function AssistantMessage({
  message,
}: {
  message: { id?: string; content?: string | null };
}) {
  const { text: withoutCards, delegations } = splitDelegations(
    message.content ?? "",
  );
  const { text, questions } = splitAsk(withoutCards);
  return (
    <div className="flex items-start gap-3 w-full max-w-(--ag-max) mt-0 mr-auto mb-0 ml-auto pt-3.5 pr-0 pb-3.5 pl-0">
      <div className="flex-1 wrap-break-word [&_p]:mb-3 [&_p:last-child]:mb-0 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_li+li]:mt-1 [&_code]:rounded-md [&_code]:bg-subtle [&_code]:px-1 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-xs [&_pre]:my-2 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:border [&_pre]:border-line [&_pre]:bg-subtle [&_pre]:p-4 [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:leading-normal [&_a]:text-foreground [&_a]:underline [&_a]:decoration-line [&_a]:underline-offset-2 [&_a:hover]:decoration-foreground [&_table]:my-2 [&_table]:border-collapse [&_table]:text-xs [&_th]:border [&_th]:border-line [&_th]:px-2.5 [&_th]:py-1.5 [&_th]:text-left [&_td]:border [&_td]:border-line [&_td]:px-2.5 [&_td]:py-1.5 [&_td]:text-left min-w-0 leading-[1.65] wrap-anywhere pt-0 pr-0 pb-0 pl-0">
        <Markdown text={text} density="compact" />
        {delegations.map((d) => (
          <DelegationCard key={d.issueId} delegation={d} />
        ))}
        {questions && (
          <ul className="mt-[.3em] mr-0 mb-[.7em] ml-0 pl-[1.3em] text-muted">
            {questions.map((q, i) => (
              <li key={i}>{q.question}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function userText(content: unknown): string {
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

function UserMessage({ message }: { message: { content?: unknown } }) {
  return (
    <div className="flex items-start justify-end gap-3 w-full max-w-(--ag-max) mt-0 mr-auto mb-0 ml-auto pt-3.5 pr-0 pb-3.5 pl-0">
      <div className="whitespace-pre-wrap wrap-break-word max-w-[min(85%,560px)] pt-2.5 pr-4 pb-2.5 pl-4 rounded-(--ag-radius) bg-(--ag-bubble) text-foreground leading-[1.55] wrap-anywhere">
        {userText(message.content)}
      </div>
    </div>
  );
}

// While the latest reply is an unanswered ask_user question, the wizard replaces the input box.
function ComposerSwitch() {
  const pending = useThread((s) => {
    const lastMsg = s.messages[s.messages.length - 1];
    if (
      s.isRunning ||
      lastMsg?.role !== "assistant" ||
      typeof lastMsg.content !== "string"
    )
      return null;
    return lastMsg.content;
  });
  const questions = pending ? splitAsk(pending).questions : null;
  if (questions) return <AskWizard key={pending} questions={questions} />;
  // Nested default composer keeps the library's input and starters; CSS removes the doubled slot padding.
  return (
    <AgentInterface.Composer placeholder="Ask about tasks, projects, or cycles…" />
  );
}

// History opens as a panel inside the chat column instead of a second sidebar next to the app navigation.
function ChatToolbar() {
  const [open, setOpen] = useState(false);
  const switchToNewThread = useThreadList((s) => s.switchToNewThread);
  const title = useThreadList(
    (s) => s.threads.find((t) => t.id === s.selectedThreadId)?.title,
  );
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <div className="relative flex items-center border-b border-line sm:px-6 gap-1.5 h-12 pt-0 pr-[clamp(12px,4vw,24px)] pb-0 pl-[clamp(12px,4vw,24px)] border-b-line">
        <span className="flex-1 truncate min-w-0 overflow-hidden text-ellipsis whitespace-nowrap text-[13px] font-medium text-foreground">
          {title || "New chat"}
        </span>
        <PopoverTrigger asChild>
          <button
            type="button"
            className="inline-flex items-center hover:bg-subtle hover:text-foreground aria-expanded:bg-subtle aria-expanded:text-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-1.5 h-7.5 pt-0 pr-2.5 pb-0 pl-2.5 rounded-[7px] bg-transparent text-muted text-[13px]"
            aria-expanded={open}
            aria-controls="agent-history"
          >
            <History size={16} aria-hidden="true" /> History
          </button>
        </PopoverTrigger>
        <button
          type="button"
          className="inline-flex items-center hover:bg-subtle hover:text-foreground aria-expanded:bg-subtle aria-expanded:text-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-1.5 h-7.5 pt-0 pr-2.5 pb-0 pl-2.5 rounded-[7px] bg-transparent text-muted text-[13px]"
          onClick={() => {
            switchToNewThread();
            setOpen(false);
          }}
        >
          <SquarePen size={16} aria-hidden="true" /> New chat
        </button>
        {open && (
          // Closes when a thread title is clicked. The thread buttons inside are the keyboard path, so the container needs no key handler.
          <PopoverContent
            align="end"
            sideOffset={4}
            onInteractOutside={(event) => {
              if (
                event.target instanceof Element &&
                event.target.closest(
                  ".openui-agent-thread-button-dropdown-menu",
                )
              )
                event.preventDefault();
            }}
            id="agent-history"
            className="flex max-w-full flex-col overflow-hidden border border-line z-50 w-[min(340px,calc(100vw-24px))] max-h-[min(60vh,480px)] pt-1.5 pr-1.5 pb-1.5 pl-1.5 border-solid rounded-[10px] bg-panel shadow-(--shadow)"
            role="dialog"
            aria-label="Chat history"
            onClick={(e) => {
              if (
                (e.target as Element).closest(
                  ".openui-agent-thread-button-title",
                )
              )
                setOpen(false);
            }}
          >
            <AgentInterface.ThreadList className="min-h-0 flex-1 overflow-y-auto overscroll-contain" />
          </PopoverContent>
        )}
      </div>
    </Popover>
  );
}

// The library's auto-scroll never matches our custom user messages and uses competing smooth scrolls, so we own it:
// open each thread at the bottom, then stay pinned while content grows unless the user has scrolled away.
function ScrollFollower() {
  const isRunning = useThread((s) => s.isRunning);
  const isLoadingMessages = useThread((s) => s.isLoadingMessages);
  const threadId = useThreadList((s) => s.selectedThreadId);
  const stick = useRef(true);
  const area = () =>
    document.querySelector<HTMLElement>(
      ".agent-chat-page .openui-agent-thread-scroll-area",
    );

  useLayoutEffect(() => {
    const el = area();
    if (!el || isLoadingMessages) return;
    stick.current = true;
    el.scrollTop = el.scrollHeight;
  }, [threadId, isLoadingMessages]);

  useEffect(() => {
    if (!isRunning) return;
    stick.current = true;
    const el = area();
    if (el) el.scrollTop = el.scrollHeight;
  }, [isRunning]);

  useEffect(() => {
    const el = area();
    const content = el?.firstElementChild;
    if (!el || !content) return;
    let last = el.scrollTop;
    const onScroll = () => {
      // Only an upward move is the user leaving the bottom; growth below never unsticks.
      if (el.scrollTop < last - 2)
        stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      else if (el.scrollHeight - el.scrollTop - el.clientHeight < 80)
        stick.current = true;
      last = el.scrollTop;
    };
    const observer = new ResizeObserver(() => {
      if (stick.current) {
        el.scrollTop = el.scrollHeight;
        last = el.scrollTop;
      }
    });
    observer.observe(content);
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      observer.disconnect();
      el.removeEventListener("scroll", onScroll);
    };
  }, []);
  return null;
}

const STARTERS = [
  {
    displayText: "Summarize my open tasks",
    prompt: "Summarize my open tasks.",
  },
  {
    displayText: "What is in progress this cycle?",
    prompt: "What is in progress in the active cycle?",
  },
  {
    displayText: "What is due this week?",
    prompt: "What tasks are due this week?",
  },
  {
    displayText: "Find unassigned bugs",
    prompt: "Find unassigned bugs and list them.",
  },
];

export function AgentChatPage({ workspaceName }: { workspaceName: string }) {
  const name = workspaceName || "Hoja";
  return (
    <section
      className="agent-chat-page flex-1 h-dvh min-h-0 shell-phone:h-[calc(100dvh-60px)] text-[15px] text-foreground"
      aria-label="Agent chat"
    >
      <AgentInterface
        llm={llm}
        storage={storage}
        components={{ AssistantMessage, UserMessage }}
        agentName={`${name} agent`}
        theme={{ mode: "light" }}
        starters={STARTERS}
        scrollOnLoad={false}
      >
        <AgentInterface.Sidebar>{null}</AgentInterface.Sidebar>
        <AgentInterface.ThreadHeader>
          <ChatToolbar />
        </AgentInterface.ThreadHeader>
        <AgentInterface.MobileHeader>
          <ChatToolbar />
        </AgentInterface.MobileHeader>
        <AgentInterface.Welcome
          title={`Ask the ${name} agent`}
          description="Search, summarize, and update tasks in your workspace."
        />
        <AgentInterface.Composer>
          <ComposerSwitch />
          <ScrollFollower />
        </AgentInterface.Composer>
      </AgentInterface>
    </section>
  );
}
