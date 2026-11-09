import React, { useEffect, useState } from 'react';

type ConnectorAccount = {
  id: string;
  provider: string;
  externalAccount: string;
  scope: 'user' | 'org';
  status: string;
};

type PendingAction = {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  createdAt: string;
};

function authHeaders(): Record<string, string> {
  const token = localStorage.getItem('hoja_token');
  return token && token !== 'session' ? { authorization: `Bearer ${token}` } : {};
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: 'include',
    headers: { ...authHeaders(), ...(init.headers as Record<string, string> | undefined) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `Request failed (${res.status})`);
  return body as T;
}

export function ConnectorsSettings({ isAdmin }: { isAdmin: boolean }) {
  const [accounts, setAccounts] = useState<ConnectorAccount[]>([]);
  const [actions, setActions] = useState<PendingAction[]>([]);
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const googleAccounts = accounts.filter((a) => a.provider === 'google_workspace');
  const notionAccounts = accounts.filter((a) => a.provider === 'notion');

  useEffect(() => {
    let live = true;
    Promise.all([
      api<{ accounts: ConnectorAccount[] }>('/api/connectors/accounts'),
      api<{ actions: PendingAction[] }>('/api/connectors/actions'),
    ])
      .then(([a, p]) => {
        if (!live) return;
        setAccounts(a.accounts);
        setActions(p.actions);
      })
      .catch((e) => live && setMessage((e as Error).message))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [refresh]);

  const revoke = async (id: string) => {
    setMessage('');
    try {
      await api(`/api/connectors/accounts/${encodeURIComponent(id)}/revoke`, { method: 'POST' });
      setRefresh((n) => n + 1);
    } catch (e) {
      setMessage((e as Error).message);
    }
  };

  const decide = async (id: string, decision: 'approve' | 'reject') => {
    setMessage('');
    try {
      await api(`/api/connectors/actions/${encodeURIComponent(id)}/${decision}`, { method: 'POST' });
      setRefresh((n) => n + 1);
    } catch (e) {
      setMessage((e as Error).message);
    }
  };

  return (
    <section className="settings-page">
      <header className="settings-page-heading">
        <span className="eyebrow">Personal</span>
        <h1>Connections</h1>
        <p>Connect a workspace so the assistant can read the content you share with it.</p>
      </header>

      <div className="settings-group connector-settings">
        <span className="settings-section-kicker">Google Workspace</span>
        <h2>Connected accounts</h2>
        <p className="settings-section-intro">Read-only access for Gmail, Drive, Calendar, and Docs. Writes always require approval.</p>
        {message && <p role="alert">{message}</p>}
        <div className="setting-row">
          <div>
            <b>Your account</b>
            <p>Read-only access. Writes always need your approval.</p>
          </div>
          <a className="primary" href="/api/connectors/google/start?scope=user">Connect Google</a>
        </div>
        {isAdmin && (
          <div className="setting-row">
            <div>
              <b>Organization account</b>
              <p>Shared with everyone in this workspace. Admins only.</p>
            </div>
            <a href="/api/connectors/google/start?scope=org">Connect organization account</a>
          </div>
        )}
        <ul aria-label="Connected accounts" className="connector-account-list">
          {loading && <li role="status">Checking connected accounts…</li>}
          {!loading && googleAccounts.length === 0 && <li className="connector-empty">No Google accounts connected.</li>}
          {googleAccounts.map((a) => (
            <li key={a.id}>
              <span className="connector-account-icon" aria-hidden="true">{a.externalAccount.slice(0, 1).toUpperCase()}</span>
              <span className="connector-account-copy"><b>{a.externalAccount}</b><small>{a.scope === 'org' ? 'Organization account' : 'Personal account'} · {a.status}</small></span>
              <button className="outline danger" onClick={() => revoke(a.id)}>Disconnect</button>
            </li>
          ))}
        </ul>
      </div>

      <div className="settings-group connector-settings">
        <span className="settings-section-kicker">Notion</span>
        <h2>Connected workspaces</h2>
        <p className="settings-section-intro">Read-only access to the pages and databases you select in Notion. Nothing is created or edited.</p>
        <div className="setting-row">
          <div>
            <b>Your workspace</b>
            <p>Choose the pages to share on Notion’s consent screen. Reconnect if you shared nothing or want to add pages.</p>
          </div>
          <a className="primary" href="/api/connectors/notion/start">Connect Notion</a>
        </div>
        <ul aria-label="Connected Notion workspaces" className="connector-account-list">
          {loading && <li role="status">Checking connected workspaces…</li>}
          {!loading && notionAccounts.length === 0 && <li className="connector-empty">No Notion workspace connected.</li>}
          {notionAccounts.map((a) => (
            <li key={a.id}>
              <span className="connector-account-icon" aria-hidden="true">{a.externalAccount.slice(0, 1).toUpperCase()}</span>
              <span className="connector-account-copy"><b>{a.externalAccount}</b><small>Personal workspace · {a.status}</small></span>
              <button className="outline danger" onClick={() => revoke(a.id)}>Disconnect</button>
            </li>
          ))}
        </ul>
      </div>

      <div className="settings-group connector-settings">
        <span className="settings-section-kicker">Human in the loop</span>
        <h2>Pending actions</h2>
        <p className="settings-section-intro">Review every proposed write before the assistant can act.</p>
        <ul aria-label="Pending actions" className="connector-action-list">
          {!loading && actions.length === 0 && <li className="connector-empty">Nothing waiting for approval.</li>}
          {actions.map((a) => (
            <li key={a.id}>
              <b>{a.tool}</b>
              <pre>{JSON.stringify(a.args, null, 2)}</pre>
              <div className="connector-action-buttons"><button className="primary" onClick={() => decide(a.id, 'approve')}>Approve</button>
              <button className="outline" onClick={() => decide(a.id, 'reject')}>Reject</button></div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
