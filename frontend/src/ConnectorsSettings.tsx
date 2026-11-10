import React, { useEffect, useState } from 'react';
import { Activity, CalendarDays, ChartNoAxesColumn, Code2, FileText, Hash, Mail, Video } from 'lucide-react';

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
  /** Customer-supplied Grafana origin. Not a secret; shown so an account is identifiable. */
  externalUrl?: string | null;
};

type ConnectorConfig = {
  github?: boolean;
  /** The app's public install page. Present only on a deployment with a configured GitHub App. */
  githubInstallUrl?: string | null;
  /** `/api/connectors/config`: per-host reachability, not just "the deployment is configured".
   *  A provider whose callback is not registered for this workspace host would fail at the
   *  consent screen, so Settings says so instead of offering the button. */
  availableOnThisHost?: Record<string, boolean>;
  unavailableReason?: Record<string, string | null>;
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

/** One glyph per connector, chosen by capability rather than brand art: the icon set ships no
 *  third-party logos, and vendoring brand marks would add trademark obligations for no
 *  functional gain. Each glyph is decorative; the tile's heading carries the accessible name. */
/** The connector ids this surface knows how to draw. Narrower than `string` on purpose: a
 *  typo in a provider id must fail the typecheck instead of silently rendering nothing. */
export type ProviderId = 'google_workspace' | 'github' | 'notion' | 'slack' | 'calendly' | 'sentry' | 'zoom' | 'grafana';

const PROVIDER_GLYPH: Record<ProviderId, React.ComponentType<{ size?: number }>> = {
  google_workspace: Mail,
  github: Code2,
  notion: FileText,
  slack: Hash,
  calendly: CalendarDays,
  sentry: Activity,
  zoom: Video,
  grafana: ChartNoAxesColumn,
};

export function ConnectorsSettings({ isAdmin }: { isAdmin: boolean }) {
  const [accounts, setAccounts] = useState<ConnectorAccount[]>([]);
  const [actions, setActions] = useState<PendingAction[]>([]);
  const [config, setConfig] = useState<ConnectorConfig>({});
  const [message, setMessage] = useState('');
  const [grafanaUrl, setGrafanaUrl] = useState('');
  const [grafanaToken, setGrafanaToken] = useState('');
  const [grafanaNotice, setGrafanaNotice] = useState('');
  const [grafanaBusy, setGrafanaBusy] = useState(false);
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
  const grafanaAccounts = accounts.filter((a) => a.provider === 'grafana');

  useEffect(() => {
    let live = true;
    Promise.all([
      api<{ accounts: ConnectorAccount[] }>('/api/connectors/accounts'),
      api<{ actions: PendingAction[] }>('/api/connectors/actions'),
      api<ConnectorConfig>('/api/connectors/config').catch(() => ({}) as ConnectorConfig),
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

  /** The provider's availability on *this* workspace host, or undefined when unreported. */
  const available = (provider: string) => config.availableOnThisHost?.[provider];

  /** The tile's state, derived from loaded accounts and this host's reported reachability.
   *  `loading` wins so a tile never claims "not connected" before the first answer arrives. */
  const badge = (provider: ProviderId, count: number, unavailable = false) => {
    if (loading) return <span className="connector-badge" data-state="loading">Checking…</span>;
    if (unavailable || available(provider) === false) return <span className="connector-badge" data-state="unavailable">Unavailable</span>;
    if (count > 0) return <span className="connector-badge" data-state="connected">Connected{count > 1 ? ` · ${count}` : ''}</span>;
    return <span className="connector-badge" data-state="idle">Not connected</span>;
  };

  /** Either the Connect link, or the reason this host cannot use the provider. */
  const connect = (provider: string, href: string, label: string, primary = true) => {
    if (available(provider) === false) {
      return <small>{config.unavailableReason?.[provider] || 'not available on this workspace host'}</small>;
    }
    return <a className={primary ? 'primary' : undefined} href={href}>{label}</a>;
  };

  const revoke = async (id: string) => {
    setMessage('');
    try {
      await api(`/api/connectors/accounts/${encodeURIComponent(id)}/revoke`, { method: 'POST' });
      setRefresh((n) => n + 1);
    } catch (e) {
      setMessage((e as Error).message);
    }
  };

  const grafana = async (path: 'validate' | 'connect') => {
    setMessage('');
    setGrafanaNotice('');
    setGrafanaBusy(true);
    try {
      const body = await api<{ account: string; instance: string }>(
        `/api/connectors/grafana/${path}`,
        {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ url: grafanaUrl.trim(), token: grafanaToken.trim() }),
        },
      );
      if (path === 'validate') {
        setGrafanaNotice(`Reached ${body.instance} as ${body.account}.`);
      } else {
        setGrafanaToken('');
        setGrafanaNotice(`Connected ${body.instance}.`);
        setRefresh((n) => n + 1);
      }
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setGrafanaBusy(false);
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

      <div className="connector-board" role="list">
      <div className="settings-group connector-settings connector-card" role="listitem" aria-labelledby="connector-google_workspace">
        {/* The product name is the heading, not an uppercase kicker: app/docs/UI-RULES.md forbids
            letter-spaced capitals, which `.settings-section-kicker` applies. */}
        <header className="connector-card-head">
          <span className="connector-glyph" aria-hidden="true">{React.createElement(PROVIDER_GLYPH['google_workspace'], { size: 20 })}</span>
          <div className="connector-card-title"><h2 id="connector-google_workspace">Google Workspace</h2>{badge('google_workspace', googleAccounts.length)}</div>
        </header>
        <p className="settings-section-intro">Read-only access for Gmail, Drive, Calendar, Docs, Meet and Chat. Writes always require approval.</p>
        {message && <p role="alert">{message}</p>}
        <div className="setting-row">
          <div>
            <b>Your account</b>
            <p>Read-only access. Writes always need your approval.</p>
          </div>
          {connect('google_workspace', '/api/connectors/google/start?scope=user', 'Connect Google')}
        </div>
        {isAdmin && (
          <div className="setting-row">
            <div>
              <b>Organization account</b>
              <p>Shared with everyone in this workspace. Admins only.</p>
            </div>
            {connect('google_workspace', '/api/connectors/google/start?scope=org', 'Connect organization account', false)}
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
            connect('google_workspace', '/api/connectors/google/start?addon=meet', 'Add Meet access')
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
            connect('google_workspace', '/api/connectors/google/start?addon=chat', 'Add Chat access')
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

      <div className="settings-group connector-settings connector-card" role="listitem" aria-labelledby="connector-sentry">
        {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
        <header className="connector-card-head">
          <span className="connector-glyph" aria-hidden="true">{React.createElement(PROVIDER_GLYPH['sentry'], { size: 20 })}</span>
          <div className="connector-card-title"><h2 id="connector-sentry">Sentry</h2>{badge('sentry', sentryAccounts.length)}</div>
        </header>
        <p className="settings-section-intro">Read-only access to the projects, issues and latest event of one Sentry organization. Nothing is resolved, assigned or deleted.</p>
        <div className="setting-row">
          <div>
            <b>Your organization</b>
            <p>You choose the Sentry organization on Sentry’s install page. The connection is personal to you and reads only that organization.</p>
          </div>
          {connect('sentry', '/api/connectors/sentry/start', 'Connect Sentry')}
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

      <div className="settings-group connector-settings connector-card" role="listitem" aria-labelledby="connector-zoom">
        {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
        <header className="connector-card-head">
          <span className="connector-glyph" aria-hidden="true">{React.createElement(PROVIDER_GLYPH['zoom'], { size: 20 })}</span>
          <div className="connector-card-title"><h2 id="connector-zoom">Zoom</h2>{badge('zoom', zoomAccounts.length)}</div>
        </header>
        <p className="settings-section-intro">Read-only access to your meetings, cloud recordings and meeting transcripts. Nothing is scheduled, edited or shared.</p>
        <div className="setting-row">
          <div>
            <b>Your meetings</b>
            <p>Hoja reads with your own Zoom permissions. A Zoom connection is personal to you and is never shared with the workspace.</p>
          </div>
          {connect('zoom', '/api/connectors/zoom/start', 'Connect Zoom')}
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

      <div className="settings-group connector-settings connector-card" role="listitem" aria-labelledby="connector-notion">
        <header className="connector-card-head">
          <span className="connector-glyph" aria-hidden="true">{React.createElement(PROVIDER_GLYPH['notion'], { size: 20 })}</span>
          <div className="connector-card-title"><h2 id="connector-notion">Notion</h2>{badge('notion', notionAccounts.length)}</div>
        </header>
        <p className="settings-section-intro">Read-only access to the pages and databases you select in Notion. Nothing is created or edited.</p>
        <div className="setting-row">
          <div>
            <b>Your workspace</b>
            <p>Choose the pages to share on Notion’s consent screen. Reconnect if you shared nothing or want to add pages.</p>
          </div>
          {connect('notion', '/api/connectors/notion/start', 'Connect Notion')}
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

      <div className="settings-group connector-settings connector-card" role="listitem" aria-labelledby="connector-slack">
        {/* No `.settings-section-kicker` here: app/docs/UI-RULES.md forbids uppercase label
            text, and that class sets `text-transform: uppercase`. The product name is the
            group heading instead. */}
        <header className="connector-card-head">
          <span className="connector-glyph" aria-hidden="true">{React.createElement(PROVIDER_GLYPH['slack'], { size: 20 })}</span>
          <div className="connector-card-title"><h2 id="connector-slack">Slack</h2>{badge('slack', slackAccounts.length)}</div>
        </header>
        <p className="settings-section-intro">Read-only access to channels, channel history, search and the people directory. Nothing is posted or edited.</p>
        <div className="setting-row">
          <div>
            <b>Your workspace</b>
            <p>Hoja reads with your own Slack permissions. Private channels work only where you are a member.</p>
          </div>
          {connect('slack', '/api/connectors/slack/start', 'Connect Slack')}
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

      <div className="settings-group connector-settings connector-card" role="listitem" aria-labelledby="connector-calendly">
        {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
        <header className="connector-card-head">
          <span className="connector-glyph" aria-hidden="true">{React.createElement(PROVIDER_GLYPH['calendly'], { size: 20 })}</span>
          <div className="connector-card-title"><h2 id="connector-calendly">Calendly</h2>{badge('calendly', calendlyAccounts.length)}</div>
        </header>
        <p className="settings-section-intro">Read-only access to your scheduling pages, booked meetings and their invitees. Nothing is booked, moved or cancelled.</p>
        <div className="setting-row">
          <div>
            <b>Your calendar</b>
            <p>Hoja reads with your own Calendly permissions. A Calendly connection is personal to you and is never shared with the workspace.</p>
          </div>
          {connect('calendly', '/api/connectors/calendly/start', 'Connect Calendly')}
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

      <div className="settings-group connector-settings connector-card" role="listitem" aria-labelledby="connector-github">
        {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
        <header className="connector-card-head">
          <span className="connector-glyph" aria-hidden="true">{React.createElement(PROVIDER_GLYPH['github'], { size: 20 })}</span>
          <div className="connector-card-title"><h2 id="connector-github">GitHub</h2>{badge('github', githubAccounts.length, config.github === false)}</div>
        </header>
        <p className="settings-section-intro">Read-only access to your repositories, files, issues and pull requests. Nothing is created, commented, merged or pushed.</p>
        <div className="setting-row">
          <div>
            <b>Your repositories</b>
            <p>Hoja reads with your own GitHub permissions and the app’s read-only permissions. A GitHub connection is personal to you and is never shared with the workspace.</p>
          </div>
          {connect('github', '/api/connectors/github/start', 'Connect GitHub')}
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

      <div className="settings-group connector-settings connector-card" role="listitem" aria-labelledby="connector-grafana">
        {/* Product name as the heading: UI-RULES forbids the uppercase kicker. */}
        <header className="connector-card-head">
          <span className="connector-glyph" aria-hidden="true">{React.createElement(PROVIDER_GLYPH['grafana'], { size: 20 })}</span>
          <div className="connector-card-title"><h2 id="connector-grafana">Grafana</h2>{badge('grafana', grafanaAccounts.length)}</div>
        </header>
        <p className="settings-section-intro">Read-only access to dashboards, data sources, alert rules and annotations of one Grafana instance. Queries are bounded and nothing is created, edited or deleted.</p>
        <form
          className="setting-row"
          onSubmit={(event) => {
            event.preventDefault();
            void grafana('connect');
          }}
        >
          <div>
            <b>Your instance</b>
            <p>Use the instance origin (for example https://example.grafana.net) and a service-account token with the Viewer role. The token is stored sealed and checked with a live read.</p>
            <label className="connector-field">
              Instance URL
              <input
                type="url"
                inputMode="url"
                autoComplete="off"
                placeholder="https://example.grafana.net"
                value={grafanaUrl}
                onChange={(event) => setGrafanaUrl(event.target.value)}
                required
              />
            </label>
            <label className="connector-field">
              Service-account token
              <input
                type="password"
                autoComplete="off"
                placeholder="glsa_..."
                value={grafanaToken}
                onChange={(event) => setGrafanaToken(event.target.value)}
                required
              />
            </label>
            {grafanaNotice && <small role="status">{grafanaNotice}</small>}
          </div>
          <div className="connector-action-buttons">
            <button
              type="button"
              className="outline"
              disabled={grafanaBusy}
              onClick={() => void grafana('validate')}
            >
              Test connection
            </button>
            <button type="submit" className="primary" disabled={grafanaBusy}>
              Connect Grafana
            </button>
          </div>
        </form>
        <ul aria-label="Connected Grafana instances" className="connector-account-list">
          {loading && <li role="status">Checking connected instances…</li>}
          {!loading && grafanaAccounts.length === 0 && <li className="connector-empty">No Grafana instance connected.</li>}
          {grafanaAccounts.map((a) => (
            <li key={a.id}>
              <span className="connector-account-icon" aria-hidden="true">{a.externalAccount.slice(0, 1).toUpperCase()}</span>
              <span className="connector-account-copy"><b>{a.externalAccount}</b><small>{a.externalUrl || 'Grafana instance'} · {a.status}</small></span>
              <button className="outline danger" onClick={() => revoke(a.id)}>Disconnect</button>
            </li>
          ))}
        </ul>
      </div>

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
