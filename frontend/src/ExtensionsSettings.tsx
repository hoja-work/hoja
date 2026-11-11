import React, {useEffect, useState} from 'react';
import {
  ExtensionError, clearExtensionActionsCache, errorText, fetchExtensions, installExtension,
  uninstallExtension, updateExtension, type Extension,
} from './extensions';

/* Administration -> Extensions. Monochrome, shape-first: the settings design system has
 * no accent colours, so hierarchy comes from borders, spacing and type weight only. */

const EXAMPLE_MANIFEST = JSON.stringify({
  key: 'worktrees',
  name: 'Worktree provisioner',
  version: '1.0.0',
  url: 'https://ext.example.com',
  access: 'read_write',
  events: [{key: 'issues', resourceTypes: ['Issue'], teamId: 'ENG'}],
  hooks: [{key: 'guard', on: 'issue.beforeCreate', path: '/hooks/guard', failurePolicy: 'allow'}],
  actions: [{key: 'provision', title: 'Provision environment', entity: 'Issue', path: '/actions/provision'}],
}, null, 2);

type Notice = {kind: 'status' | 'error'; text: string};
type Editor = {id: string; text: string; error: string};

function formatDate(value?: string | null) {
  if (!value) return 'Unknown';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'Unknown' : date.toLocaleString();
}

/** Parse before sending: the textarea should explain a broken manifest without a round trip. */
function parseManifest(text: string): {value?: Record<string, unknown>; error?: string} {
  const trimmed = text.trim();
  if (!trimmed) return {error: 'Paste a manifest to continue.'};
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch (e) {
    return {error: `Invalid JSON: ${errorText(e)}`};
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    return {error: 'The manifest must be a JSON object with key, name, version, url and access.'};
  }
  return {value: parsed as Record<string, unknown>};
}

export function ExtensionsSettings() {
  const [extensions, setExtensions] = useState<Extension[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [manifestText, setManifestText] = useState('');
  const [formError, setFormError] = useState('');
  const [installing, setInstalling] = useState(false);
  const [secrets, setSecrets] = useState<{name: string; apiKey: string; signingSecret: string} | null>(null);
  const [copied, setCopied] = useState<'apiKey' | 'signingSecret' | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let live = true;
    // Revalidate in place: the list stays on screen; only the first load shows a loading line.
    fetchExtensions()
      .then(list => { if (live) { setExtensions(list); setLoadError(''); } })
      .catch(e => { if (live) setLoadError(errorText(e)); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [refresh]);

  const install = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError('');
    setNotice(null);
    setCopied(null);
    const parsed = parseManifest(manifestText);
    if (parsed.error) { setFormError(parsed.error); return; }
    setInstalling(true);
    setSecrets(null);
    try {
      const payload = await installExtension(parsed.value);
      if (!payload?.success || !payload.extension) throw new ExtensionError('The extension was not installed.');
      const installed = payload.extension;
      setManifestText('');
      setSecrets({name: installed.name || installed.key, apiKey: payload.apiKey || '', signingSecret: payload.signingSecret || ''});
      clearExtensionActionsCache();
      setNotice({kind: 'status', text: `Installed ${installed.name || installed.key}. Copy its credentials now — they are not shown again.`});
      setRefresh(x => x + 1);
    } catch (err) {
      setFormError(errorText(err));
    } finally {
      setInstalling(false);
    }
  };

  const copyValue = async (kind: 'apiKey' | 'signingSecret', value: string) => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(kind);
    } catch {
      setNotice({kind: 'error', text: 'Copy failed. Select the value and copy it manually.'});
    }
  };

  const startEdit = (ext: Extension) => {
    setCopied(null);
    setNotice(null);
    setConfirmId(null);
    setEditor({id: ext.id, text: JSON.stringify(ext.manifest ?? {}, null, 2), error: ''});
  };

  const saveEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editor) return;
    const parsed = parseManifest(editor.text);
    if (parsed.error) { setEditor({...editor, error: parsed.error}); return; }
    setSavingId(editor.id);
    setNotice(null);
    try {
      const payload = await updateExtension(editor.id, parsed.value);
      if (!payload?.success) throw new ExtensionError('The manifest was rejected.');
      clearExtensionActionsCache();
      setEditor(null);
      setNotice({kind: 'status', text: 'Manifest updated. Hoja reconciled the extension in one transaction.'});
      setRefresh(x => x + 1);
    } catch (err) {
      setEditor(current => (current ? {...current, error: errorText(err)} : current));
    } finally {
      setSavingId(null);
    }
  };

  const uninstall = async (ext: Extension) => {
    setBusyId(ext.id);
    setNotice(null);
    try {
      const payload = await uninstallExtension(ext.id);
      if (!payload?.success) throw new ExtensionError('The extension was not uninstalled.');
      if (editor?.id === ext.id) setEditor(null);
      setConfirmId(null);
      clearExtensionActionsCache();
      setNotice({kind: 'status', text: `Uninstalled ${ext.name || ext.key}. Its bot and credentials are revoked.`});
      setRefresh(x => x + 1);
    } catch (err) {
      setNotice({kind: 'error', text: errorText(err)});
    } finally {
      setBusyId(null);
    }
  };

  return <section className="settings-page">
    <header className="settings-page-heading">
      <span className="eyebrow">Administration</span>
      <h1>Extensions</h1>
      <p>Extensions run outside Hoja and are called over signed HTTPS. Each install gets its own bot identity, so extension writes are never made as you.</p>
    </header>

    <div className="settings-stack extensions-settings">
      {secrets && <div className="extension-secret" role="status">
        <b>Copy the credentials for “{secrets.name}” now</b>
        <p>These are shown once. Store them in the extension’s secret store; Hoja keeps only the hash of the API key.</p>
        {secrets.apiKey
          ? <div className="extension-secret-row">
              <span className="extension-secret-label">API key</span>
              <code aria-label="New extension API key">{secrets.apiKey}</code>
              <button type="button" className="outline" onClick={() => void copyValue('apiKey', secrets.apiKey)}>{copied === 'apiKey' ? 'Copied' : 'Copy key'}</button>
            </div>
          : <p className="error">The server returned no API key. Uninstall and install again to receive one.</p>}
        {secrets.signingSecret
          ? <div className="extension-secret-row">
              <span className="extension-secret-label">Signing secret</span>
              <code aria-label="New extension signing secret">{secrets.signingSecret}</code>
              <button type="button" className="outline" onClick={() => void copyValue('signingSecret', secrets.signingSecret)}>{copied === 'signingSecret' ? 'Copied' : 'Copy secret'}</button>
            </div>
          : <p className="error">The server returned no signing secret. Uninstall and install again to receive one.</p>}
        <button type="button" className="auth-switch" onClick={() => { setSecrets(null); setCopied(null); }}>I’ve saved them</button>
      </div>}

      <section className="settings-group extension-install-panel" aria-labelledby="extension-install-title">
        <span className="settings-section-kicker">New extension</span>
        <h2 id="extension-install-title">Install from a manifest</h2>
        <p className="settings-section-intro">Paste the extension manifest. Hoja validates every field before it creates the bot, hooks and actions.</p>
        <form className="extension-manifest" onSubmit={install} noValidate>
          <label>Manifest JSON
            <textarea aria-label="Extension manifest JSON" rows={12} spellCheck={false} value={manifestText}
              placeholder='{"key":"worktrees","name":"Worktree provisioner", …}'
              onChange={e => { setManifestText(e.target.value); setFormError(''); }} />
          </label>
          {formError && <p className="error" role="alert">{formError}</p>}
          <div className="extension-manifest-actions">
            <button className="primary" disabled={installing || !manifestText.trim()}>{installing ? 'Installing…' : 'Install extension'}</button>
            <button type="button" className="outline" onClick={() => { setManifestText(EXAMPLE_MANIFEST); setFormError(''); }}>Insert example manifest</button>
            {manifestText !== '' && <button type="button" className="outline" onClick={() => { setManifestText(''); setFormError(''); }}>Clear</button>}
          </div>
          <small className="extension-hint">Hook and action paths are joined to the extension URL; absolute URLs are rejected.</small>
        </form>
      </section>

      {notice && <p role={notice.kind === 'error' ? 'alert' : 'status'} className={notice.kind === 'error' ? 'error extension-notice' : 'extension-notice'}>{notice.text}</p>}

      <section className="settings-group extension-inventory" aria-labelledby="extension-list-title">
        <div className="settings-section-head">
          <div><span className="settings-section-kicker">Installed</span><h2 id="extension-list-title">Installed extensions</h2></div>
          <span className="settings-badge">{extensions.length} {extensions.length === 1 ? 'extension' : 'extensions'}</span>
        </div>

        {loading && <p role="status" className="extension-empty-state">Loading extensions…</p>}
        {!loading && loadError && <div className="extension-empty-state" role="alert">
          <b>Extensions could not be loaded</b>
          <p>{loadError}</p>
          <button type="button" className="outline" onClick={() => { setLoading(true); setRefresh(x => x + 1); }}>Retry</button>
        </div>}
        {!loading && !loadError && !extensions.length && <div className="extension-empty-state">
          <b>No extensions installed</b>
          <p>Install one from a manifest above. Its hooks and actions appear here and in the task menu.</p>
        </div>}

        {!loading && !loadError && extensions.length > 0 && <ul className="extension-list" aria-label="Installed extensions">
          {extensions.map(ext => {
            const isEditing = editor?.id === ext.id;
            const isConfirming = confirmId === ext.id;
            const saving = savingId === ext.id;
            const busy = busyId === ext.id;
            return <li className="extension-card" key={ext.id}>
              <div className="extension-card-head">
                <div className="extension-title">
                  <b>{ext.name}</b>
                  <code className="extension-key">{ext.key}</code>
                </div>
                <div className="extension-badges">
                  <span className={`settings-badge ${ext.enabled ? 'success' : ''}`}>{ext.enabled ? 'Enabled' : 'Disabled'}</span>
                  <span className="settings-badge">{ext.access === 'read_write' ? 'Read and write' : 'Read only'}</span>
                  <span className="settings-badge">v{ext.version}</span>
                </div>
              </div>

              <dl className="extension-meta">
                <dt>Endpoint</dt><dd><code>{ext.url}</code></dd>
                <dt>Bot identity</dt><dd>{ext.botUser ? (ext.botUser.name || ext.botUser.id) : 'None'}</dd>
                <dt>Updated</dt><dd>{formatDate(ext.updatedAt)}</dd>
              </dl>

              <div className="extension-section">
                <h3>Hooks <span className="settings-badge">{ext.hooks.length}</span></h3>
                {ext.hooks.length
                  ? <ul className="extension-chip-list">{ext.hooks.map(hook => <li key={`${ext.id}-hook-${hook.key}`}>
                      <code>{hook.on}</code>
                      <span className="extension-chip-key">{hook.key}</span>
                      <span className="settings-badge">{hook.failurePolicy === 'block' ? 'Blocks on failure' : 'Fails open'}</span>
                    </li>)}</ul>
                  : <p className="extension-section-empty">No hooks. Hoja never waits on this extension before writing.</p>}
              </div>

              <div className="extension-section">
                <h3>Actions <span className="settings-badge">{ext.actions.length}</span></h3>
                {ext.actions.length
                  ? <ul className="extension-chip-list">{ext.actions.map(action => <li key={action.id}>
                      <b>{action.title}</b>
                      <code>{action.key}</code>
                      <span className="extension-chip-key">{action.entity}</span>
                    </li>)}</ul>
                  : <p className="extension-section-empty">No actions. This extension cannot be run from a task.</p>}
              </div>

              {ext.failures.length > 0 && <div className="extension-section">
                <h3>Recent failures <span className="settings-badge">{ext.failures.length}</span></h3>
                <ul className="extension-failures">{ext.failures.slice(0, 5).map((failure, index) => <li key={`${ext.id}-failure-${failure.at}-${index}`}>
                  <span className="extension-failure-head">
                    <code>{failure.kind}</code>
                    {failure.key && <span>{failure.key}</span>}
                    {typeof failure.httpStatus === 'number' && <span>HTTP {failure.httpStatus}</span>}
                    <time dateTime={failure.at}>{formatDate(failure.at)}</time>
                  </span>
                  {failure.message && <p>{failure.message}</p>}
                </li>)}</ul>
              </div>}

              {isEditing && <form className="extension-manifest extension-manifest-inline" onSubmit={saveEdit} noValidate>
                <label>Manifest JSON for {ext.key}
                  <textarea aria-label={`Manifest JSON for ${ext.key}`} rows={12} spellCheck={false} value={editor.text}
                    onChange={e => setEditor({id: ext.id, text: e.target.value, error: ''})} />
                </label>
                {editor.error && <p className="error" role="alert">{editor.error}</p>}
                <div className="extension-manifest-actions">
                  <button className="primary" disabled={saving}>{saving ? 'Saving…' : 'Save manifest'}</button>
                  <button type="button" className="outline" onClick={() => setEditor(null)}>Cancel</button>
                  <small className="extension-hint">Only the difference is applied, in one transaction.</small>
                </div>
              </form>}

              <div className="extension-card-actions">
                {!isEditing && <button type="button" className="outline" onClick={() => startEdit(ext)}>Edit manifest</button>}
                {isConfirming
                  ? <div className="api-key-confirm extension-confirm" role="group" aria-label={`Confirm uninstall ${ext.key}`}>
                      <span>Uninstall “{ext.key}”? Its bot and credentials are revoked, its hooks and actions stop, and pending deliveries are deleted. Action history is kept.</span>
                      <button type="button" className="danger" disabled={busy} onClick={() => void uninstall(ext)}>{busy ? 'Uninstalling…' : 'Uninstall'}</button>
                      <button type="button" className="outline" onClick={() => setConfirmId(null)}>Cancel</button>
                    </div>
                  : <button type="button" className="outline danger" onClick={() => { setConfirmId(ext.id); setNotice(null); }}>Uninstall</button>}
              </div>
            </li>;
          })}
        </ul>}
      </section>
    </div>
  </section>;
}
