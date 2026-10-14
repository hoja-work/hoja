import React, { useEffect, useState } from 'react';
import {tr} from './i18n';

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
  // eslint-disable-next-line i18next/no-literal-string -- HTTP authorization header value, not UI text
  return token && token !== 'session' ? { authorization: `Bearer ${token}` } : {};
}

async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...init,
    credentials: 'include',
    headers: { ...authHeaders(), ...(init.headers as Record<string, string> | undefined) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || tr('Request failed ({{v0}})',{v0:res.status}));
  return body as T;
}

export function ConnectorsSettings({ isAdmin }: { isAdmin: boolean }) {
  const [accounts, setAccounts] = useState<ConnectorAccount[]>([]);
  const [actions, setActions] = useState<PendingAction[]>([]);
  const [message, setMessage] = useState('');
  const [refresh, setRefresh] = useState(0);

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
      .catch((e) => live && setMessage((e as Error).message));
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
        <h1>{tr("Connections")}</h1>
        <p>{tr("Connect Google Workspace so the assistant can read your Gmail, Drive, Calendar, and Docs.")}</p>
      </header>

      <div className="settings-group">
        <h2>{tr("Google Workspace")}</h2>
        {message && <p role="alert">{message}</p>}
        <div className="setting-row">
          <div>
            <b>{tr("Your account")}</b>
            <p>{tr("Read-only access. Writes always need your approval.")}</p>
          </div>
          <a className="primary" href="/api/connectors/google/start?scope=user">{tr("Connect Google")}</a>
        </div>
        {isAdmin && (
          <div className="setting-row">
            <div>
              <b>{tr("Organization account")}</b>
              <p>{tr("Shared with everyone in this workspace. Admins only.")}</p>
            </div>
            <a href="/api/connectors/google/start?scope=org">{tr("Connect organization account")}</a>
          </div>
        )}
        <ul aria-label={tr("Connected accounts")}>
          {accounts.length === 0 && <li>{tr("No Google accounts connected.")}</li>}
          {accounts.map((a) => (
            <li key={a.id}>
              {a.externalAccount} · {a.scope === 'org' ? tr('Organization') : tr('Personal')}
              <button onClick={() => revoke(a.id)}>{tr("Disconnect")}</button>
            </li>
          ))}
        </ul>
      </div>

      <div className="settings-group">
        <h2>{tr("Pending actions")}</h2>
        <ul aria-label={tr("Pending actions")}>
          {actions.length === 0 && <li>{tr("Nothing waiting for approval.")}</li>}
          {actions.map((a) => (
            <li key={a.id}>
              <b>{a.tool}</b>
              <pre>{JSON.stringify(a.args, null, 2)}</pre>
              <button onClick={() => decide(a.id, 'approve')}>{tr("Approve")}</button>
              <button onClick={() => decide(a.id, 'reject')}>{tr("Reject")}</button>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
