import React from 'react';
import { AgentInterface, fetchLLM, openAIAdapter, openAIMessageFormat, restStorage } from '@openuidev/react-ui';
import '@openuidev/react-ui/styles/index.css';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('hoja_token');
  return token ? { authorization: `Bearer ${token}` } : {};
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
    .map((part, i) => (i % 2 === 1 ? part : part.replace(ISSUE_ID, (id) => `[${id}](/workspace/issue/${id})`)))
    .join('');
}

function AssistantMessage({ message }: { message: { content?: string | null } }) {
  return (
    <div className="agent-msg agent-msg-assistant">
      <div className="agent-msg-avatar" aria-hidden="true">H</div>
      <div className="agent-msg-body agent-chat-reply">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a: ({ href, children }) => (
              <a href={href} target={href?.startsWith('/') ? undefined : '_blank'} rel="noopener noreferrer">{children}</a>
            ),
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

const STARTERS = [
  { displayText: 'Summarize my open issues', prompt: 'Summarize my open issues.' },
  { displayText: 'What is in progress this cycle?', prompt: 'What is in progress in the active cycle?' },
  { displayText: 'What is due this week?', prompt: 'What issues are due this week?' },
  { displayText: 'Find unassigned bugs', prompt: 'Find unassigned bugs and list them.' },
];

export function AgentChatPage({ workspaceName }: { workspaceName: string }) {
  const name = workspaceName || 'Hoja';
  return (
    <section className="agent-chat-page" aria-label="Agent chat">
      <AgentInterface
        llm={llm}
        storage={storage}
        components={{ AssistantMessage, UserMessage }}
        agentName={`${name} agent`}
        theme={{ mode: 'light' }}
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
        <AgentInterface.Welcome title={`Ask the ${name} agent`} description="Search, summarize, and update issues in your workspace." />
        <AgentInterface.Composer placeholder="Ask about issues, projects, or cycles…" />
      </AgentInterface>
    </section>
  );
}
