import React, { useEffect, useState } from 'react';

type ConnectorAccount = {
  id: string;
  provider: string;
  externalAccount: string;
  scope: 'user' | 'org';
  status: string;
  scopes: string[];
  /** Per-API answer for a Google grant: a user may untick individual permissions on the consent
   *  screen, so "the account exists" is not the same as "Meet is available". */
  capabilities?: Record<string, boolean>;
};

type PendingAction = {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  createdAt: string;
};

type ConnectorConfig = {
  github?: boolean;
  /** The app's public install page. Present only on a deployment with a configured GitHub App. */
  githubInstallUrl?: string | null;
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
  const [config, setConfig] = useState<ConnectorConfig>({});
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const googleAccounts = accounts.filter((a) => a.provider === 'google_workspace');
  const meetAccounts = googleAccounts.filter((a) => a.capabilities?.meet);
  const chatAccounts = googleAccounts.filter((a) => a.capabilities?.chat);
  const notionAccounts = accounts.filter((a) => a.provider === 'notion');
  const slackAccounts = accounts.filter((a) => a.provider === 'slack');
  const calendlyAccounts = accounts.filter((a) => a.provider === 'calendly');
  const sentryAccounts = accounts.filter((a) => a.provider === 'sentry');
  const zoomAccounts = accounts.filter((a) => a.provider === 'zoom');
  const githubAccounts = accounts.filter((a) => a.provider === 'github');
  const githubInstallUrl = typeof config.githubInstallUrl === 'string' ? config.githubInstallUrl : null;

  useEffect(() => {
    let live = true;
    Promise.all([
      api<{ accounts: ConnectorAccount[] }>('/api/connectors/accounts'),
      api<{ actions: PendingAction[] }>('/api/connectors/actions'),
      api<ConnectorConfig>('/api/connectors/config'),
    ])
      .then(([a, p, c]) => {
        if (!live) return;
        setAccounts(a.accounts);
        setActions(p.actions);
        setConfig(c);
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
        {/* The product name is the heading, not an uppercase kicker: app/docs/UI-RULES.md forbids
            letter-spaced capitals, which `.settings-section-kicker` applies. */}
        <h2>Google Workspace</h2>
        <p className="settings-section-intro">Read-only access for Gmail, Drive, Calendar, Docs, Meet and Chat. Writes always require approval.</p>
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
        <div className="setting-row">
          <div>
            <b>Google Meet</b>
            <p>Read past meetings, participants, recordings and transcripts. Meet rides on the same Google permission as the account above, so Disconnecting it also removes Meet access.</p>
            {meetAccounts.length > 0 && (
              <small>Added to {meetAccounts.map((a) => a.externalAccount).join(', ')}</small>
            )}
          </div>
          {meetAccounts.length > 0 ? (
            <small>Meet access granted</small>
          ) : googleAccounts.length > 0 ? (
            <a className="primary" href="/api/connectors/google/start?addon=meet">Add Meet access</a>
          ) : (
            <small>Connect Google first</small>
          )}
        </div>
        <div className="setting-row">
          <div>
            <b>Google Chat</b>
            <p>Read the spaces you belong to, their messages and their members. Chat rides on the same Google permission as the account above, so Disconnecting it also removes Chat access.</p>
            {chatAccounts.length > 0 && (
              <small>Added to {chatAccounts.map((a) => a.externalAccount).join(', ')}</small>
            )}
          </div>
          {chatAccounts.length > 0 ? (
            <small>Chat access granted</small>
          ) : googleAccounts.length > 0 ? (
            <a className="primary" href="/api/connectors/google/start?addon=chat">Add Chat access</a>
          ) : (
            <small>Connect Google first</small>
          )}
        </div>
        <ul aria-label="Connected Google accounts" className="connector-account-list">
          {loading && <li role="status">Checking connected accounts…</li>}
          {!loading && googleAccounts.length === 0 && <li className="connector-empty">No Google accounts connected.</li>}
          {googleAccounts.map((a) => (
            <li key={a.id}>
              <span className="connector-account-icon" aria-hidden="true">{a.externalAccount.slice(0, 1).toUpperCase()}</span>
              <span className="connector-account-copy"><b>{a.externalAccount}</b><small>{a.scope === 'org' ? 'Organization account' : 'Personal account'} · {a.status}{a.capabilities?.meet ? ' · Meet access' : ''}{a.capabilities?.chat ? ' · Chat access' : ''}</small></span>
              <button className="outline danger" onClick={() => revoke(a.id)}>Disconnect</button>
            </li>
          ))}
        </ul>
      </div>

      <div className="settings-group connector-settings">
        {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
        <h2>Sentry</h2>
        <p className="settings-section-intro">Read-only access to the projects, issues and latest event of one Sentry organization. Nothing is resolved, assigned or deleted.</p>
        <div className="setting-row">
          <div>
            <b>Your organization</b>
            <p>You choose the Sentry organization on Sentry’s install page. The connection is personal to you and reads only that organization.</p>
          </div>
          <a className="primary" href="/api/connectors/sentry/start">Connect Sentry</a>
        </div>
        <ul aria-label="Connected Sentry organizations" className="connector-account-list">
          {loading && <li role="status">Checking connected organizations…</li>}
          {!loading && sentryAccounts.length === 0 && <li className="connector-empty">No Sentry organization connected.</li>}
          {sentryAccounts.map((a) => (
            <li key={a.id}>
              <span className="connector-account-icon" aria-hidden="true">{a.externalAccount.slice(0, 1).toUpperCase()}</span>
              <span className="connector-account-copy"><b>{a.externalAccount}</b><small>Sentry organization · {a.status}</small></span>
              <button className="outline danger" onClick={() => revoke(a.id)}>Disconnect</button>
            </li>
          ))}
        </ul>
      </div>

      <div className="settings-group connector-settings">
        {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
        <h2>Zoom</h2>
        <p className="settings-section-intro">Read-only access to your meetings, cloud recordings and meeting transcripts. Nothing is scheduled, edited or shared.</p>
        <div className="setting-row">
          <div>
            <b>Your meetings</b>
            <p>Hoja reads with your own Zoom permissions. A Zoom connection is personal to you and is never shared with the workspace.</p>
          </div>
          <a className="primary" href="/api/connectors/zoom/start">Connect Zoom</a>
        </div>
        <ul aria-label="Connected Zoom accounts" className="connector-account-list">
          {loading && <li role="status">Checking connected accounts…</li>}
          {!loading && zoomAccounts.length === 0 && <li className="connector-empty">No Zoom account connected.</li>}
          {zoomAccounts.map((a) => (
            <li key={a.id}>
              <span className="connector-account-icon" aria-hidden="true">{a.externalAccount.slice(0, 1).toUpperCase()}</span>
              <span className="connector-account-copy"><b>{a.externalAccount}</b><small>Personal account · {a.status}</small></span>
              <button className="outline danger" onClick={() => revoke(a.id)}>Disconnect</button>
            </li>
          ))}
        </ul>
      </div>

      <div className="settings-group connector-settings">
        {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
        <h2>GitHub</h2>
        <p className="settings-section-intro">Read-only access to your repositories, files, issues and pull requests. Nothing is created, commented, merged or pushed.</p>
        <div className="setting-row">
          <div>
            <b>Your repositories</b>
            <p>Hoja reads with your own GitHub permissions and the app’s read-only permissions. A GitHub connection is personal to you and is never shared with the workspace.</p>
          </div>
          <a className="primary" href="/api/connectors/github/start">Connect GitHub</a>
        </div>
        {githubInstallUrl && (
          <div className="setting-row">
            <div>
              <b>Private repositories</b>
              <p>GitHub only lets the app read what it is installed on, and only what you can already see. Install it on the account that owns the private repositories you want Hoja to read. Public repositories need no installation.</p>
            </div>
            <a href={githubInstallUrl}>Install on GitHub</a>
          </div>
        )}
        {config.github === false && (
          <p className="settings-section-intro">This deployment has no GitHub App configured.</p>
        )}
        <ul aria-label="Connected GitHub accounts" className="connector-account-list">
          {loading && <li role="status">Checking connected accounts…</li>}
          {!loading && githubAccounts.length === 0 && <li className="connector-empty">No GitHub account connected.</li>}
          {githubAccounts.map((a) => (
            <li key={a.id}>
              <span className="connector-account-icon" aria-hidden="true">{a.externalAccount.slice(0, 1).toUpperCase()}</span>
              <span className="connector-account-copy"><b>{a.externalAccount}</b><small>Personal account · {a.status}</small></span>
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
        {/* No `.settings-section-kicker` here: app/docs/UI-RULES.md forbids uppercase label
            text, and that class sets `text-transform: uppercase`. The product name is the
            group heading instead. */}
        <h2>Slack</h2>
        <p className="settings-section-intro">Read-only access to channels, channel history, search and the people directory. Nothing is posted or edited.</p>
        <div className="setting-row">
          <div>
            <b>Your workspace</b>
            <p>Hoja reads with your own Slack permissions. Private channels work only where you are a member.</p>
          </div>
          <a className="primary" href="/api/connectors/slack/start">Connect Slack</a>
        </div>
        <ul aria-label="Connected Slack workspaces" className="connector-account-list">
          {loading && <li role="status">Checking connected workspaces…</li>}
          {!loading && slackAccounts.length === 0 && <li className="connector-empty">No Slack workspace connected.</li>}
          {slackAccounts.map((a) => (
            <li key={a.id}>
              <span className="connector-account-icon" aria-hidden="true">{a.externalAccount.slice(0, 1).toUpperCase()}</span>
              <span className="connector-account-copy"><b>{a.externalAccount}</b><small>Personal workspace · {a.status}</small></span>
              <button className="outline danger" onClick={() => revoke(a.id)}>Disconnect</button>
            </li>
          ))}
        </ul>
      </div>

      <div className="settings-group connector-settings">
        {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
        <h2>Calendly</h2>
        <p className="settings-section-intro">Read-only access to your scheduling pages, booked meetings and their invitees. Nothing is booked, moved or cancelled.</p>
        <div className="setting-row">
          <div>
            <b>Your calendar</b>
            <p>Hoja reads with your own Calendly permissions. A Calendly connection is personal to you and is never shared with the workspace.</p>
          </div>
          <a className="primary" href="/api/connectors/calendly/start">Connect Calendly</a>
        </div>
        <ul aria-label="Connected Calendly accounts" className="connector-account-list">
          {loading && <li role="status">Checking connected accounts…</li>}
          {!loading && calendlyAccounts.length === 0 && <li className="connector-empty">No Calendly account connected.</li>}
          {calendlyAccounts.map((a) => (
            <li key={a.id}>
              <span className="connector-account-icon" aria-hidden="true">{a.externalAccount.slice(0, 1).toUpperCase()}</span>
              <span className="connector-account-copy"><b>{a.externalAccount}</b><small>Personal calendar · {a.status}</small></span>
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
