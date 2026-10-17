import React, { useEffect, useState } from 'react';
import {confirmDialog} from './dialogs';

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
  const [baseUrl, setBaseUrl] = useState('https://api.openai.com/v1');
  const [apiKey, setApiKey] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let live = true;
    call(LIST)
      .then((d) => live && setProviders(d.modelProviders))
      .catch((e) => live && setMessage((e as Error).message));
    return () => {
      live = false;
    };
  }, [refresh]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage('');
    try {
      await call(CREATE, { name, provider, modelId, baseUrl: baseUrl || null, apiKey });
      setName('');
      setModelId('');
      setApiKey('');
      setMessage('Model added. The key is stored encrypted and cannot be viewed again.');
      setRefresh((x) => x + 1);
    } catch (err) {
      setMessage((err as Error).message);
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
    }
  };

  const remove = async (p: ModelProvider) => {
    if (!await confirmDialog(`Delete ${p.name}? The agent will stop using this model.`,{danger:true})) return;
    try {
      await call(DELETE, { id: p.id });
      setRefresh((x) => x + 1);
    } catch (err) {
      setMessage((err as Error).message);
    }
  };

  return (
    <section className="settings-page">
      <header className="settings-page-heading">
        <span className="eyebrow">Administration</span>
        <h1>Model providers</h1>
        <p>Models available to the workspace agent. Users never supply their own keys.</p>
      </header>
      <div className="settings-group">
        <form onSubmit={create}>
          <label>
            Display name
            <input aria-label="Model display name" required maxLength={80} value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            Provider
            <input aria-label="Provider" required value={provider} onChange={(e) => setProvider(e.target.value)} />
          </label>
          <label>
            Model ID
            <input aria-label="Model ID" required value={modelId} onChange={(e) => setModelId(e.target.value)} />
          </label>
          <label>
            Base URL (OpenAI-compatible)
            <input aria-label="Base URL" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
          </label>
          <label>
            API key
            <input aria-label="API key" type="password" required value={apiKey} onChange={(e) => setApiKey(e.target.value)} autoComplete="off" />
          </label>
          <button className="primary" disabled={busy || !name.trim() || !modelId.trim() || !apiKey}>
            {busy ? 'Saving…' : 'Add model'}
          </button>
        </form>
        {message && <p role={message.startsWith('Model added') ? 'status' : 'alert'}>{message}</p>}
        <div className="issue-card">
          {providers.map((p) => (
            <div className="issue-row" key={p.id}>
              <span className="issue-copy">
                <b>{p.name}</b>
                <small>
                  {p.provider} · {p.modelId} · {p.hasApiKey ? 'key stored' : 'no key'}
                </small>
              </span>
              <button type="button" className="outline" onClick={() => void toggle(p)}>
                {p.enabled ? 'Disable' : 'Enable'}
              </button>
              <button type="button" className="outline danger" onClick={() => void remove(p)}>
                Delete
              </button>
            </div>
          ))}
          {!providers.length && <p>No models configured. The agent will say so until one is added.</p>}
        </div>
      </div>
    </section>
  );
}
