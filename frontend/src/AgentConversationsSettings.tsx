import React, { useEffect, useState } from 'react';

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

  const heading = <header className="settings-page-heading">
    <span className="eyebrow">Personal</span>
    <h1>Agent conversations</h1>
    <p>Review the conversation history stored for your agent, including message count and local storage use.</p>
  </header>;

  if (error) return <section className="settings-page">{heading}<div className="settings-group settings-empty-state" role="alert"><b>Could not load conversations</b><p>{error}</p></div></section>;
  if (!summary) return <section className="settings-page">{heading}<div className="settings-group settings-empty-state" role="status">Loading conversation storage…</div></section>;

  return (
    <section className="settings-page" aria-label="Agent conversations">
      <header className="settings-page-heading">
        <span className="eyebrow">Personal</span>
        <h1>Agent conversations</h1>
        <p>
          Stored agent history for this account. Review activity and storage without exposing conversation content.
        </p>
      </header>
      <div className="conversation-summary" aria-label="Conversation storage summary">
        <div><span>Saved conversations</span><b>{summary.count}</b><small>Across your agent history</small></div>
        <div><span>Total storage</span><b>{formatBytes(summary.totalBytes)}</b><small>Messages and conversation data</small></div>
      </div>
      <div className="settings-group conversation-log">
        <div className="settings-section-head"><div><span className="settings-section-kicker">History</span><h2>Conversation activity</h2></div><span className="settings-badge">Most recent first</span></div>
        {summary.conversations.length ? <div className="conversation-table-wrap"><table className="conversation-table">
          <caption>Saved agent conversations, message counts, and storage size</caption>
          <thead>
            <tr>
              <th scope="col">Last updated</th>
              <th scope="col">Messages</th>
              <th scope="col">Size</th>
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
        </table></div> : <div className="settings-empty-state"><span className="settings-empty-mark" aria-hidden="true">0</span><b>No saved conversations</b><p>Agent conversations will appear here after you start a conversation.</p></div>}
      </div>
    </section>
  );
}
