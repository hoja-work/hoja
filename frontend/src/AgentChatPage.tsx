import React from 'react';
import { AgentInterface, fetchLLM, openAIAdapter, openAIMessageFormat } from '@openuidev/react-ui';
import { openuiChatLibrary } from '@openuidev/react-ui/genui-lib';
import '@openuidev/react-ui/styles/index.css';

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

export function AgentChatPage({ workspaceName }: { workspaceName: string }) {
  return (
    <section className="agent-chat-page" aria-label="Agent chat">
      <AgentInterface
        llm={llm}
        componentLibrary={openuiChatLibrary}
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
