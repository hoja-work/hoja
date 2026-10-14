import React, { useEffect, useState } from 'react';
import {tr} from './i18n';

type Conversation = { id: string; messageCount: number; bytes: number; updatedAt: string };
type Summary = { count: number; totalBytes: number; conversations: Conversation[] };

const QUERY =
  'query AgentConversations { agentConversations { count totalBytes conversations { id messageCount bytes updatedAt } } }';

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function AgentConversationsSettings() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    const token = localStorage.getItem('hoja_token');
    fetch('/graphql', {
      method: 'POST',
      headers: { 'content-type': 'application/json', ...(token && token !== 'session' ? { authorization: `Bearer ${token}` } : {}) },
      credentials: 'include',
      body: JSON.stringify({ query: QUERY }),
    })
      .then((r) => r.json())
      .then((body) => {
        if (!live) return;
        if (body.errors?.length) throw new Error(body.errors[0].message);
        setSummary(body.data.agentConversations);
      })
      .catch((e) => live && setError((e as Error).message));
    return () => {
      live = false;
    };
  }, []);

  if (error) return <p role="alert">{tr("Could not load conversations:")} {error}</p>;
  if (!summary) return <p role="status">{tr("Loading conversations…")}</p>;

  return (
    <section className="settings-page" aria-label={tr("Agent conversations")}>
      <header className="settings-page-heading">
        <h1>{tr("Agent conversations")}</h1>
        <p>
          {summary.count} {tr("saved")} {summary.count === 1 ? 'conversation' : 'conversations'} · {formatBytes(summary.totalBytes)} {tr("total")}
        </p>
      </header>
      <table>
        <thead>
          <tr>
            <th>{tr("Updated")}</th>
            <th>{tr("Messages")}</th>
            <th>{tr("Size")}</th>
          </tr>
        </thead>
        <tbody>
          {summary.conversations.map((c) => (
            <tr key={c.id}>
              <td>{new Date(c.updatedAt).toLocaleString()}</td>
              <td>{c.messageCount}</td>
              <td>{formatBytes(c.bytes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
