import React from 'react';
import { AgentInterface, fetchLLM, openAIAdapter, openAIMessageFormat, restStorage } from '@openuidev/react-ui';
import { useThread, useThreadList } from '@openuidev/react-headless';
import '@openuidev/react-ui/styles/index.css';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { History, SquarePen } from 'lucide-react';
import {tr} from './i18n';

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('hoja_token');
  // eslint-disable-next-line i18next/no-literal-string -- HTTP authorization header value, not UI text
  return token && token !== 'session' ? { authorization: `Bearer ${token}` } : {};
}

// One thread per chat: the library sends threadId on every turn, and the server appends to that thread.
const llm = fetchLLM({
  url: '/api/agent/chat',
  streamAdapter: openAIAdapter(),
  messageFormat: openAIMessageFormat,
  headers: authHeaders(),
  fetch: (input, init) => fetch(input, { ...init, credentials: 'include' }),
});

const storage = restStorage({
  baseUrl: '/api/agent/threads',
  messageFormat: openAIMessageFormat,
  headers: authHeaders(),
  fetch: (input, init) => fetch(input, { ...init, credentials: 'include' }),
});

// Bare issue IDs (TREBOL-12) become links outside code spans, fences, and existing links.
const ISSUE_ID = /\b([A-Z][A-Z0-9]{1,9}-\d+)\b/g;
export function linkIssueIds(markdown: string): string {
  return markdown
    .split(/(```[\s\S]*?```|`[^`\n]*`|\[[^\]]*\]\([^)]*\))/)
    .map((part, i) => (i % 2 === 1 ? part : part.replace(ISSUE_ID, (id) => `[${id}](hoja-issue:${id})`)))
    .join('');
}

type IssueSummary = { identifier: string; title: string; state?: { name?: string; type?: string; color?: string } | null };

const ISSUE_CHIP_QUERY = 'query AgentIssueChip($id:String!){issue(id:$id){identifier title state{name type color}}}';

function IssueChip({ identifier }: { identifier: string }) {
  const [issue, setIssue] = useState<IssueSummary | null>(null);
  useEffect(() => {
    let cancelled = false;
    fetch('/graphql', {
      method: 'POST',
      credentials: 'include',
      headers: { 'content-type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ query: ISSUE_CHIP_QUERY, variables: { id: identifier } }),
    })
      .then((r) => r.json())
      .then((d) => { if (!cancelled && d?.data?.issue) setIssue(d.data.issue as IssueSummary); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [identifier]);
  const color = issue?.state?.color || '#a1a1aa';
  return (
    <a className="agent-issue-chip" href={`/workspace/issue/${identifier}`} title={issue?.title ?? identifier}>
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.5" aria-hidden="true">
        <circle cx="8" cy="8" r="6" />
        {issue?.state?.type === 'completed' && <path d="M5 8.2l2 2 4-4.4" stroke={color} />}
      </svg>
      <span className="agent-issue-chip-id">{identifier}</span>
      {issue && <span className="agent-issue-chip-title">{issue.title}</span>}
    </a>
  );
}

type AskOption = { label: string; description?: string };
type AskQuestion = { question: string; header?: string; options?: AskOption[]; multiple?: boolean; allow_other?: boolean };

// The agent's ask_user tool arrives as a ```hoja-ask fenced JSON block at the end of the reply.
const ASK_BLOCK = /```hoja-ask\n([\s\S]*?)\n```/;

export function splitAsk(content: string): { text: string; questions: AskQuestion[] | null } {
  const match = content.match(ASK_BLOCK);
  if (!match) return { text: content, questions: null };
  try {
    const parsed = JSON.parse(match[1]) as { questions?: AskQuestion[] };
    if (!Array.isArray(parsed.questions) || parsed.questions.length === 0) return { text: content, questions: null };
    return { text: content.replace(ASK_BLOCK, '').trim(), questions: parsed.questions };
  } catch {
    return { text: content.replace(ASK_BLOCK, '').trim(), questions: null };
  }
}

type AskAnswer = { picked: string[]; other: string; otherOn: boolean; note: string };
const EMPTY_ANSWER: AskAnswer = { picked: [], other: '', otherOn: false, note: '' };

function answerText(q: AskQuestion, a: AskAnswer): string {
  const values = [...a.picked];
  if ((a.otherOn || !q.options?.length) && a.other.trim()) values.push(a.other.trim());
  let line = values.join(', ');
  if (a.note.trim()) line += line ? ` (${a.note.trim()})` : a.note.trim();
  return line;
}

// Shows one question at a time in place of the composer; answers are sent together as one user message.
function AskWizard({ questions }: { questions: AskQuestion[] }) {
  const processMessage = useThread((s) => s.processMessage);
  const [step, setStep] = useState(0);
  const [answers, setAnswers] = useState<AskAnswer[]>(() => questions.map(() => ({ ...EMPTY_ANSWER })));
  const q = questions[step];
  const a = answers[step];
  const options = q.options ?? [];
  const allowOther = options.length === 0 || q.allow_other !== false;
  const last = step === questions.length - 1;
  const answered = answerText(q, a) !== '';
  const update = (patch: Partial<AskAnswer>) =>
    setAnswers((prev) => prev.map((x, j) => (j === step ? { ...x, ...patch } : x)));
  const toggle = (label: string) => {
    if (q.multiple) update({ picked: a.picked.includes(label) ? a.picked.filter((x) => x !== label) : [...a.picked, label] });
    else update({ picked: [label], otherOn: false });
  };
  const next = () => {
    if (!answered) return;
    if (!last) { setStep(step + 1); return; }
    const body = questions.length === 1
      ? answerText(q, a)
      : questions.map((x, i) => `${x.header || x.question}: ${answerText(x, answers[i])}`).join('\n');
    void processMessage({ role: 'user', content: body });
  };
  return (
    <form className="agent-ask" aria-label={tr("Agent question")} onSubmit={(e) => { e.preventDefault(); next(); }}>
      <div className="agent-ask-top">
        {q.header && <span className="agent-ask-header">{q.header}</span>}
        {questions.length > 1 && <span className="agent-ask-step">{step + 1} {tr("of")} {questions.length}</span>}
      </div>
      <fieldset key={step} className="agent-ask-q">
        <legend>
          <span className="agent-ask-question">{q.question}</span>
          {q.multiple && <span className="agent-ask-hint">{tr("Select all that apply")}</span>}
        </legend>
        {options.map((o) => (
          <label key={o.label} className={`agent-ask-option${a.picked.includes(o.label) ? ' is-picked' : ''}`}>
            <input type={q.multiple ? 'checkbox' : 'radio'} name={`ask-${step}`} checked={a.picked.includes(o.label)} onChange={() => toggle(o.label)} />
            <span>
              <span className="agent-ask-label">{o.label}</span>
              {o.description && <span className="agent-ask-desc">{o.description}</span>}
            </span>
          </label>
        ))}
        {allowOther && options.length > 0 && (
          <label className={`agent-ask-option${a.otherOn ? ' is-picked' : ''}`}>
            <input
              type={q.multiple ? 'checkbox' : 'radio'}
              name={`ask-${step}`}
              checked={a.otherOn}
              onChange={() => update(q.multiple ? { otherOn: !a.otherOn } : { otherOn: true, picked: [] })}
            />
            <span className="agent-ask-label">{tr("Other")}</span>
          </label>
        )}
        {allowOther && (options.length === 0 || a.otherOn) && (
          <textarea
            className="agent-ask-input"
            rows={2}
            placeholder={tr("Write your answer…")}
            value={a.other}
            autoFocus
            onChange={(e) => update({ other: e.target.value })}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); next(); } }}
          />
        )}
        {options.length > 0 && (
          <input className="agent-ask-input" type="text" placeholder={tr("Add details (optional)")} value={a.note} onChange={(e) => update({ note: e.target.value })} />
        )}
      </fieldset>
      <div className="agent-ask-actions">
        {step > 0 && <button type="button" className="agent-ask-back" onClick={() => setStep(step - 1)}>{tr("Back")}</button>}
        <button type="submit" className="primary" disabled={!answered}>{last ? tr('Send') : tr('Next')}</button>
      </div>
    </form>
  );
}

// Stable across renders: an inline `a` component would be a new type per streamed token, remounting every chip.
const MARKDOWN_COMPONENTS: Components = {
  a: ({ href, children }) => {
    if (href?.startsWith('hoja-issue:')) return <IssueChip identifier={href.slice('hoja-issue:'.length)} />;
    const issue = href?.match(/^\/workspace\/issue\/([A-Z][A-Z0-9]{1,9}-\d+)(\/|$)/);
    if (issue) return <IssueChip identifier={issue[1]} />;
    return <a href={href} target={href?.startsWith('/') ? undefined : '_blank'} rel="noopener noreferrer">{children}</a>;
  },
};

function AssistantMessage({ message }: { message: { id?: string; content?: string | null } }) {
  const { text, questions } = splitAsk(message.content ?? '');
  return (
    <div className="agent-msg agent-msg-assistant">
      <div className="agent-msg-body agent-chat-reply">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={MARKDOWN_COMPONENTS}
        >
          {linkIssueIds(text)}
        </ReactMarkdown>
        {questions && (
          <ul className="agent-ask-summary">
            {questions.map((q, i) => <li key={i}>{q.question}</li>)}
          </ul>
        )}
      </div>
    </div>
  );
}

function userText(content: unknown): string {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content.map((part) => (part && typeof part === 'object' && 'text' in part ? String((part as { text: unknown }).text) : '')).join('');
  }
  return '';
}

function UserMessage({ message }: { message: { content?: unknown } }) {
  return (
    <div className="agent-msg agent-msg-user">
      <div className="agent-msg-bubble">{userText(message.content)}</div>
    </div>
  );
}

// While the latest reply is an unanswered ask_user question, the wizard replaces the input box.
function ComposerSwitch() {
  const pending = useThread((s) => {
    const lastMsg = s.messages[s.messages.length - 1];
    if (s.isRunning || lastMsg?.role !== 'assistant' || typeof lastMsg.content !== 'string') return null;
    return lastMsg.content;
  });
  const questions = pending ? splitAsk(pending).questions : null;
  if (questions) return <AskWizard key={pending} questions={questions} />;
  // Nested default composer keeps the library's input and starters; CSS removes the doubled slot padding.
  return <AgentInterface.Composer placeholder={tr("Ask about issues, projects, or cycles…")} />;
}

// History opens as a panel inside the chat column instead of a second sidebar next to the app navigation.
function ChatToolbar() {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const switchToNewThread = useThreadList((s) => s.switchToNewThread);
  const title = useThreadList((s) => s.threads.find((t) => t.id === s.selectedThreadId)?.title);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      const target = e.target as Element;
      // Radix renders the thread actions menu in a portal outside the panel.
      if (ref.current?.contains(target) || target.closest('.openui-agent-thread-button-dropdown-menu')) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('pointerdown', onDown); document.removeEventListener('keydown', onKey); };
  }, [open]);
  return (
    <div className="agent-toolbar" ref={ref}>
      <span className="agent-toolbar-title">{title || tr('New chat')}</span>
      <button type="button" className="agent-toolbar-button" aria-expanded={open} aria-controls="agent-history" onClick={() => setOpen((v) => !v)}>
        <History size={16} aria-hidden="true" /> {tr("History")}
      </button>
      <button type="button" className="agent-toolbar-button" onClick={() => { switchToNewThread(); setOpen(false); }}>
        <SquarePen size={16} aria-hidden="true" /> {tr("New chat")}
      </button>
      {open && (
        <div
          id="agent-history"
          className="agent-history"
          role="dialog"
          aria-label={tr("Chat history")}
          onClick={(e) => { if ((e.target as Element).closest('.openui-agent-thread-button-title')) setOpen(false); }}
        >
          <AgentInterface.ThreadList />
        </div>
      )}
    </div>
  );
}

// The library's auto-scroll never matches our custom user messages and uses competing smooth scrolls, so we own it:
// open each thread at the bottom, then stay pinned while content grows unless the user has scrolled away.
function ScrollFollower() {
  const isRunning = useThread((s) => s.isRunning);
  const isLoadingMessages = useThread((s) => s.isLoadingMessages);
  const threadId = useThreadList((s) => s.selectedThreadId);
  const stick = useRef(true);
  const area = () => document.querySelector<HTMLElement>('.agent-chat-page .openui-agent-thread-scroll-area');

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
      if (el.scrollTop < last - 2) stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
      else if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) stick.current = true;
      last = el.scrollTop;
    };
    const observer = new ResizeObserver(() => {
      if (stick.current) { el.scrollTop = el.scrollHeight; last = el.scrollTop; }
    });
    observer.observe(content);
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => { observer.disconnect(); el.removeEventListener('scroll', onScroll); };
  }, []);
  return null;
}

const STARTERS = [
  { displayText: 'Summarize my open issues', prompt: 'Summarize my open issues.' },
  { displayText: 'What is in progress this cycle?', prompt: 'What is in progress in the active cycle?' },
  { displayText: 'What is due this week?', prompt: 'What issues are due this week?' },
  { displayText: 'Find unassigned bugs', prompt: 'Find unassigned bugs and list them.' },
];

export function AgentChatPage({ workspaceName }: { workspaceName: string }) {
  const name = workspaceName || tr('Hoja');
  return (
    <section className="agent-chat-page" aria-label={tr("Agent chat")}>
      <AgentInterface
        llm={llm}
        storage={storage}
        components={{ AssistantMessage, UserMessage }}
        agentName={`${name} agent`}
        theme={{ mode: 'light' }}
        starters={STARTERS}
        scrollOnLoad={false}
      >
        <AgentInterface.Sidebar>{null}</AgentInterface.Sidebar>
        <AgentInterface.ThreadHeader><ChatToolbar /></AgentInterface.ThreadHeader>
        <AgentInterface.MobileHeader><ChatToolbar /></AgentInterface.MobileHeader>
        <AgentInterface.Welcome title={tr("Ask the {{v0}} agent", {v0: name})} description={tr("Search, summarize, and update issues in your workspace.")} />
        <AgentInterface.Composer>
          <ComposerSwitch />
          <ScrollFollower />
        </AgentInterface.Composer>
      </AgentInterface>
    </section>
  );
}
