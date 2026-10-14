import React from 'react';
import { AgentInterface, fetchLLM, openAIAdapter, openAIMessageFormat, restStorage } from '@openuidev/react-ui';
import '@openuidev/react-ui/styles/index.css';
import { useThread } from '@openuidev/react-headless';
import { ArrowUp, Paperclip, Plus, Square, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('hoja_token');
  return token && token !== 'session' ? { authorization: `Bearer ${token}` } : {};
}

// Upload IDs for the next chat turn. The library builds the request body, so the fetch wrapper adds them once.
let pendingAttachmentIds: string[] = [];

// One thread per chat: the library sends threadId on every turn, and the server appends to that thread.
const llm = fetchLLM({
  url: '/api/agent/chat',
  streamAdapter: openAIAdapter(),
  messageFormat: openAIMessageFormat,
  headers: authHeaders(),
  fetch: (input, init) => {
    let nextInit: RequestInit = { ...init, credentials: 'include' };
    if (typeof input === 'string' && input.includes('/api/agent/chat') && typeof init?.body === 'string' && pendingAttachmentIds.length > 0) {
      const body = JSON.parse(init.body);
      body.attachmentIds = pendingAttachmentIds;
      pendingAttachmentIds = [];
      nextInit = { ...nextInit, body: JSON.stringify(body) };
    }
    return fetch(input, nextInit);
  },
});

const MAX_FILE_BYTES = 1_000_000;
const MAX_FILES = 5;
const TEXT_EXTENSIONS = ['.txt', '.md', '.csv', '.json', '.log', '.py', '.ts', '.tsx', '.js', '.sql', '.yaml', '.yml', '.toml'];

async function uploadFile(file: File): Promise<string> {
  if (!TEXT_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext))) throw new Error('Only text files are supported');
  if (file.size > MAX_FILE_BYTES) throw new Error('File is larger than 1 MB');
  const res = await fetch('/api/agent/uploads', {
    method: 'POST',
    credentials: 'include',
    headers: { 'content-type': 'application/json', ...authHeaders() },
    body: JSON.stringify({ name: file.name, content: await file.text() }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.id) throw new Error(data.error || data.errors?.[0]?.message || 'Upload failed');
  return data.id as string;
}

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
    <a className="agent-issue-chip" style={{ '--chip-color': color } as React.CSSProperties} href={`/workspace/issue/${identifier}`} title={issue?.title ?? identifier}>
      <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke={color} strokeWidth="1.5" aria-hidden="true">
        <circle cx="8" cy="8" r="6" />
        {issue?.state?.type === 'completed' && <path d="M5 8.2l2 2 4-4.4" stroke={color} />}
      </svg>
      <span className="agent-issue-chip-id">{identifier}</span>
      {issue && <span className="agent-issue-chip-title">{issue.title}</span>}
    </a>
  );
}

function AssistantMessage({ message }: { message: { content?: string | null } }) {
  return (
    <div className="agent-msg agent-msg-assistant">
      <div className="agent-msg-body agent-chat-reply">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => {
              if (href?.startsWith('hoja-issue:')) return <IssueChip identifier={href.slice('hoja-issue:'.length)} />;
              const issue = href?.match(/^\/workspace\/issue\/([A-Z][A-Z0-9]{1,9}-\d+)(\/|$)/);
              if (issue) return <IssueChip identifier={issue[1]} />;
              return <a href={href} target={href?.startsWith('/') ? undefined : '_blank'} rel="noopener noreferrer">{children}</a>;
            },
          }}
        >
          {linkIssueIds(message.content ?? '')}
        </ReactMarkdown>
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

type StagedFile = { key: number; name: string; status: 'uploading' | 'ready' | 'error'; id?: string; error?: string };

function AgentComposer({ placeholder }: { placeholder: string }) {
  const processMessage = useThread((s) => s.processMessage);
  const cancelMessage = useThread((s) => s.cancelMessage);
  const isRunning = useThread((s) => s.isRunning);
  const isLoadingMessages = useThread((s) => s.isLoadingMessages);
  const [text, setText] = useState('');
  const [files, setFiles] = useState<StagedFile[]>([]);
  const nextKey = useRef(0);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const busyUploading = files.some((f) => f.status === 'uploading');
  const hasError = files.some((f) => f.status === 'error');
  const readyIds = files.filter((f) => f.status === 'ready' && f.id).map((f) => f.id as string);
  const canSend = (text.trim().length > 0 || readyIds.length > 0) && !busyUploading && !hasError && !isRunning && !isLoadingMessages;

  useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = '0px';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [text]);

  const addFiles = (picked: File[]) => {
    const room = MAX_FILES - files.length;
    picked.slice(0, Math.max(room, 0)).forEach((file) => {
      const key = nextKey.current++;
      setFiles((current) => [...current, { key, name: file.name, status: 'uploading' }]);
      uploadFile(file).then(
        (id) => setFiles((current) => current.map((f) => (f.key === key ? { ...f, status: 'ready', id } : f))),
        (err: Error) => setFiles((current) => current.map((f) => (f.key === key ? { ...f, status: 'error', error: err.message } : f))),
      );
    });
  };

  const send = () => {
    if (!canSend) return;
    pendingAttachmentIds = readyIds;
    processMessage({ role: 'user', content: text.trim() || 'Please review the attached files.' });
    setText('');
    setFiles([]);
  };

  return (
    <form className="agent-composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
      {files.length > 0 && (
        <ul className="agent-composer-files" aria-label="Attached files">
          {files.map((file) => (
            <li className="agent-composer-file" data-status={file.status} key={file.key} title={file.error}>
              <Paperclip size={13} aria-hidden="true" />
              <span className="agent-composer-file-name">{file.name}</span>
              {file.status === 'uploading' && <span className="agent-composer-file-state">Uploading…</span>}
              {file.status === 'error' && <span className="agent-composer-file-state agent-composer-file-error">{file.error}</span>}
              <button
                type="button"
                className="agent-composer-file-remove"
                aria-label={`Remove ${file.name}`}
                onClick={() => setFiles((current) => current.filter((f) => f.key !== file.key))}
              >
                <X size={12} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <textarea
        ref={inputRef}
        className="agent-composer-input"
        rows={1}
        value={text}
        placeholder={placeholder}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
      />
      <div className="agent-composer-bar">
        <div className="agent-composer-tools">
          <button type="button" className="agent-composer-attach" aria-label="Add file" title="Add file" onClick={() => fileInputRef.current?.click()}>
            <Plus size={18} aria-hidden="true" />
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            hidden
            accept={TEXT_EXTENSIONS.join(',')}
            onChange={(e) => {
              addFiles(Array.from(e.target.files ?? []));
              e.target.value = '';
            }}
          />
        </div>
        {isRunning ? (
          <button type="button" className="agent-composer-send" aria-label="Stop response" onClick={cancelMessage}>
            <Square size={12} fill="currentColor" aria-hidden="true" />
          </button>
        ) : (
          <button type="submit" className="agent-composer-send" aria-label="Send message" disabled={!canSend}>
            <ArrowUp size={18} aria-hidden="true" />
          </button>
        )}
      </div>
    </form>
  );
}

const STARTERS = [
  { displayText: 'Summarize my open issues', prompt: 'Summarize my open issues.' },
  { displayText: 'What is in progress this cycle?', prompt: 'What is in progress in the active cycle?' },
  { displayText: 'What is due this week?', prompt: 'What issues are due this week?' },
  { displayText: 'Find unassigned bugs', prompt: 'Find unassigned bugs and list them.' },
];

function AgentWelcome({ name }: { name: string }) {
  const processMessage = useThread((s) => s.processMessage);
  const isRunning = useThread((s) => s.isRunning);
  return (
    <div className="agent-welcome">
      <h2 className="agent-welcome-title">Ask the {name} agent</h2>
      <p className="agent-welcome-description">Search, summarize, and update issues in your workspace.</p>
      <AgentComposer placeholder="Ask about issues, projects, or cycles…" />
      <div className="agent-welcome-starters">
        {STARTERS.map((starter) => (
          <button
            type="button"
            key={starter.displayText}
            disabled={isRunning}
            onClick={() => processMessage({ role: 'user', content: starter.prompt })}
          >
            {starter.displayText}
          </button>
        ))}
      </div>
    </div>
  );
}

export function AgentChatPage({ workspaceName, themeMode = 'light' }: { workspaceName: string; themeMode?: 'light' | 'dark' }) {
  const name = workspaceName || 'Hoja';
  return (
    <section className="agent-chat-page" aria-label="Agent chat">
      <AgentInterface
        llm={llm}
        storage={storage}
        components={{ AssistantMessage, UserMessage }}
        agentName={`${name} agent`}
        theme={{ mode: themeMode }}
        starters={STARTERS}
      >
        <AgentInterface.Sidebar>
          <AgentInterface.SidebarHeader>
            <AgentInterface.NewChatButton />
          </AgentInterface.SidebarHeader>
          <AgentInterface.SidebarContent>
            <AgentInterface.ThreadList />
          </AgentInterface.SidebarContent>
        </AgentInterface.Sidebar>
        <AgentInterface.Welcome>
          <AgentWelcome name={name} />
        </AgentInterface.Welcome>
        <AgentInterface.Composer>
          <AgentComposer placeholder="Ask about issues, projects, or cycles…" />
        </AgentInterface.Composer>
      </AgentInterface>
    </section>
  );
}
