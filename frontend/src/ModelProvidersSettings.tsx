import React, { useEffect, useState } from 'react';
import {tr} from './i18n';

type ModelProvider = {
  id: string;
  name: string;
  provider: string;
  modelId: string;
  baseUrl: string | null;
  enabled: boolean;
  hasApiKey: boolean;
};

const LIST = 'query ModelProviders { modelProviders { id name provider modelId baseUrl enabled hasApiKey } }';
const CREATE =
  'mutation($name:String!,$provider:String!,$modelId:String!,$baseUrl:String,$apiKey:String!){modelProviderCreate(name:$name,provider:$provider,modelId:$modelId,baseUrl:$baseUrl,apiKey:$apiKey){id}}';
const UPDATE = 'mutation($id:String!,$enabled:Boolean){modelProviderUpdate(id:$id,enabled:$enabled){id enabled}}';
const DELETE = 'mutation($id:String!){modelProviderDelete(id:$id)}';

async function call(query: string, variables: Record<string, unknown> = {}) {
  const token = localStorage.getItem('hoja_token');
  const res = await fetch('/graphql', {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...(token && token !== 'session' ? { authorization: `Bearer ${token}` } : {}) },
    credentials: 'include',
    body: JSON.stringify({ query, variables }),
  });
  const body = await res.json();
  if (body.errors?.length) throw new Error(body.errors[0].message);
  return body.data;
}

export function ModelProvidersSettings() {
  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [name, setName] = useState('');
  const [provider, setProvider] = useState('openai');
  const [modelId, setModelId] = useState('');
  // eslint-disable-next-line i18next/no-literal-string -- default API endpoint URL, not UI text
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1');
  const [apiKey, setApiKey] = useState('');
  const [message, setMessage] = useState('');
  const [messageIsError, setMessageIsError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let live = true;
    call(LIST)
      .then((d) => live && setProviders(d.modelProviders))
      .catch((e) => { if (live) { setMessage((e as Error).message); setMessageIsError(true); } });
    return () => {
      live = false;
    };
  }, [refresh]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage('');
    setMessageIsError(false);
    try {
      await call(CREATE, { name, provider, modelId, baseUrl: baseUrl || null, apiKey });
      setName('');
      setModelId('');
      setApiKey('');
      setMessage(tr('Model added. The key is stored encrypted and cannot be viewed again.'));
      setMessageIsError(false);
      setRefresh((x) => x + 1);
    } catch (err) {
      setMessage((err as Error).message);
      setMessageIsError(true);
    } finally {
      setBusy(false);
    }
  };

  const toggle = async (p: ModelProvider) => {
    try {
      await call(UPDATE, { id: p.id, enabled: !p.enabled });
      setRefresh((x) => x + 1);
    } catch (err) {
      setMessage((err as Error).message);
      setMessageIsError(true);
    }
  };

  const remove = async (p: ModelProvider) => {
    if (!window.confirm(tr("Delete {{v0}}? The agent will stop using this model.", {v0: p.name}))) return;
    try {
      await call(DELETE, { id: p.id });
      setRefresh((x) => x + 1);
    } catch (err) {
      setMessage((err as Error).message);
      setMessageIsError(true);
    }
  };

  return (
    <section className="settings-page">
      <header className="settings-page-heading">
        <span className="eyebrow">{tr("Administration")}</span>
        <h1>{tr("Model providers")}</h1>
        <p>{tr("Models available to the workspace agent. Users never supply their own keys.")}</p>
      </header>
      <div className="settings-group">
        <form onSubmit={create}>
          <label>
            {tr("Display name")}
            <input aria-label={tr("Model display name")} required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            {tr("Provider")}
            <input aria-label={tr("Provider")} required value={provider} onChange={(e) => setProvider(e.target.value)} />
          </label>
          <label>
            {tr("Model ID")}
            <input aria-label={tr("Model ID")} required value={modelId} onChange={(e) => setModelId(e.target.value)} />
          </label>
          <label>
            {tr("Base URL (OpenAI-compatible)")}
            <input aria-label={tr("Base URL")} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          </label>
          <label>
            {tr("API key")}
            <input aria-label={tr("API key")} type="password" required value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="off" />
          </label>
          <button className="primary" disabled={busy || !name.trim() || !modelId.trim() || !apiKey}>
            {busy ? tr('Saving…') : tr('Add model')}
          </button>
        </form>
        {message && <p role={messageIsError ? 'alert' : 'status'}>{message}</p>}
        <div className="issue-card">
          {providers.map((p) => (
            <div className="issue-row" key={p.id}>
              <span className="issue-copy">
                <b>{p.name}</b>
                <small>
                  {p.provider} · {p.modelId} · {p.hasApiKey ? tr('key stored') : tr('no key')}
                </small>
              </span>
              <button type="button" className="outline" onClick={() => void toggle(p)}>
                {p.enabled ? tr('Disable') : tr('Enable')}
              </button>
              <button type="button" className="outline danger" onClick={() => void remove(p)}>
                {tr("Delete")}
              </button>
            </div>
          ))}
          {!providers.length && <p>{tr("No models configured. The agent will say so until one is added.")}</p>}
        </div>
      </div>
    </section>
  );
}
