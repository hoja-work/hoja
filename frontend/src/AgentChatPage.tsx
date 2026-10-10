import React from 'react';
import { AgentInterface, fetchLLM, openAIAdapter, openAIMessageFormat } from '@openuidev/react-ui';
import '@openuidev/react-ui/styles/index.css';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const llm = fetchLLM({
  url: '/api/agent/chat',
  streamAdapter: openAIAdapter(),
  messageFormat: openAIMessageFormat,
  fetch: (input, init) => {
    const token = localStorage.getItem('hoja_token');
    const headers = new Headers(init?.headers);
    if (token) headers.set('authorization', `Bearer ${token}`);
    return fetch(input, { ...init, headers, credentials: 'include' });
  },
});

// The agent replies in Markdown. Links are rendered as anchors; react-markdown escapes raw HTML by default.
function PlainAssistantMessage({ message }: { message: { content?: string | null } }) {
  return (
    <div className="agent-chat-reply">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children }) => (
            <a href={href} target={href?.startsWith('/') ? undefined : '_blank'} rel="noopener noreferrer">{children}</a>
          ),
        }}
      >
        {message.content ?? ''}
      </ReactMarkdown>
    </div>
  );
}

export function AgentChatPage({ workspaceName }: { workspaceName: string }) {
  return (
    <section className="agent-chat-page" aria-label="Agent chat">
      <AgentInterface
        llm={llm}
        components={{ AssistantMessage: PlainAssistantMessage }}
        agentName={`${workspaceName || 'Hoja'} agent`}
        theme={{ mode: 'light' }}
        starters={[
          { displayText: 'Summarize my open issues', prompt: 'Summarize my open issues.' },
          { displayText: 'What is in progress this cycle?', prompt: 'What is in progress in the active cycle?' },
        ]}
      />
    </section>
  );
}
