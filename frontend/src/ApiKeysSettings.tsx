import React, {useEffect, useMemo, useState} from 'react';
import {SettingsSelect} from './SettingsSelect';

type ApiKey={id:string;label:string;access?:string;hint?:string;expiresAt?:string;lastUsedAt?:string;createdAt?:string;revokedAt?:string};
type Access='read'|'read_write';
type StatusFilter='all'|'active'|'expired'|'revoked';

async function gql(q:string, variables?:Record<string,unknown>){
  const r=await fetch('/graphql',{method:'POST',credentials:'include',headers:{'content-type':'application/json',...((():Record<string,string>=>{const t=localStorage.getItem('hoja_token');return t&&t!=='session'?{authorization:`Bearer ${t}`}:{}})())},body:JSON.stringify({query:q,variables})});
  const b=await r.json();
  if(b.errors?.length) throw Error(b.errors[0].message);
  return b.data;
}

const EXPIRY_OPTIONS=[
  {value:'7',label:'7 days'},{value:'30',label:'30 days'},{value:'90',label:'90 days'},
  {value:'365',label:'1 year'},{value:'never',label:'No expiration'},
];

function keyStatus(k:ApiKey, now:number):'active'|'expired'|'revoked'{
  if(k.revokedAt) return 'revoked';
  if(k.expiresAt && Date.parse(k.expiresAt)<=now) return 'expired';
  return 'active';
}

const STATUS_LABEL={active:'Active',expired:'Expired',revoked:'Revoked'} as const;
const ACCESS_OPTIONS=[
  {value:'read_write',label:'Read and write',description:'View and change workspace data',meta:'Full access'},
  {value:'read',label:'Read only',description:'View data without making changes',meta:'Restricted'},
];

function formatDate(value?:string){return value?new Date(value).toLocaleString():'Never'}

export function ApiKeysSettings(){
  const [keys,setKeys]=useState<ApiKey[]>([]);
  const [loading,setLoading]=useState(true);
  const [label,setLabel]=useState('');
  const [access,setAccess]=useState<Access>('read_write');
  const [expiry,setExpiry]=useState('90');
  const [secret,setSecret]=useState<{label:string;value:string}|null>(null);
  const [copied,setCopied]=useState(false);
  const [message,setMessage]=useState<{kind:'error'|'status';text:string}|null>(null);
  const [busy,setBusy]=useState(false);
  const [query,setQuery]=useState('');
  const [statusFilter,setStatusFilter]=useState<StatusFilter>('all');
  const [confirmId,setConfirmId]=useState<string|null>(null);
  const [refresh,setRefresh]=useState(0);
  const now=Date.now();

  useEffect(()=>{
    let live=true;
    // Revalidate in place: the list stays on screen; only the first load shows a loading line.
    gql('query MyApiKeys{myApiKeys{id label access hint expiresAt lastUsedAt createdAt revokedAt}}')
      .then(d=>{if(live)setKeys(d.myApiKeys)})
      .catch(e=>{if(live)setMessage({kind:'error',text:`Could not load API keys: ${(e as Error).message}`})})
      .finally(()=>{if(live)setLoading(false)});
    return()=>{live=false};
  },[refresh]);

  const counts=useMemo(()=>{
    const c={all:keys.length,active:0,expired:0,revoked:0};
    keys.forEach(k=>{c[keyStatus(k,now)]++});
    return c;
  },[keys,now]);

  const visible=keys.filter(k=>{
    if(statusFilter!=='all'&&keyStatus(k,now)!==statusFilter)return false;
    const q=query.trim().toLowerCase();
    return !q||(k.label||'').toLowerCase().includes(q)||(k.hint||'').toLowerCase().includes(q);
  });

  const create=async(e:React.FormEvent)=>{
    e.preventDefault();
    setBusy(true);setMessage(null);setCopied(false);
    try{
      const expiresAt=expiry==='never'?null:new Date(Date.now()+Number(expiry)*86400000).toISOString();
      const d=await gql(
        'mutation($label:String!,$access:String!,$expiresAt:DateTime){apiKeyCreate(label:$label,access:$access,expiresAt:$expiresAt){success apiKey{id} secret}}',
        {label:label.trim(),access,expiresAt});
      if(!d.apiKeyCreate?.success)throw Error('Could not create API key. Check the name and try again.');
      setSecret({label:label.trim(),value:d.apiKeyCreate.secret});
      setLabel('');setAccess('read_write');setExpiry('90');
      setRefresh(x=>x+1);
    }catch(err){setMessage({kind:'error',text:(err as Error).message})}
    finally{setBusy(false)}
  };

  // Shown as revoked at once; restored with an error if the server refuses.
  const revoke=async(k:ApiKey)=>{
    setMessage(null);setConfirmId(null);
    const revokedAt=new Date().toISOString();
    setKeys(list=>list.map(x=>x.id===k.id?{...x,revokedAt}:x));
    try{
      const d=await gql('mutation($id:String!){apiKeyRevoke(id:$id)}',{id:k.id});
      if(!d.apiKeyRevoke)throw Error('Could not revoke this key. It may already be revoked.');
      setMessage({kind:'status',text:`Revoked “${k.label||'API key'}”. Apps using it will stop working.`});
      setRefresh(x=>x+1);
    }catch(err){
      setKeys(list=>list.map(x=>x.id===k.id?{...x,revokedAt:k.revokedAt}:x));
      setMessage({kind:'error',text:(err as Error).message});
    }
  };

  const copySecret=async()=>{
    if(!secret)return;
    try{await navigator.clipboard.writeText(secret.value);setCopied(true)}
    catch{setMessage({kind:'error',text:'Copy failed. Select the key and copy it manually.'})}
  };

  return <div className="settings-stack api-keys-settings">
    <p className="settings-leading-note">A key acts as you, with the access level you choose. Full secrets are shown only once.</p>

    {secret&&<div className="api-key-secret" role="status">
      <b>Copy “{secret.label}” now</b>
      <p>This is the only time the full key is shown. Store it in a password manager or secret store.</p>
      <div className="api-key-secret-row">
        <code aria-label="New API key">{secret.value}</code>
        <button type="button" className="outline" onClick={copySecret}>{copied?'Copied':'Copy key'}</button>
      </div>
      <button type="button" className="auth-switch" onClick={()=>{setSecret(null);setCopied(false)}}>I’ve saved it</button>
    </div>}

    <section className="settings-group api-key-create-panel" aria-labelledby="api-key-create-title">
      <span className="settings-section-kicker">New credential</span>
      <h2 id="api-key-create-title">Create an API key</h2>
      <p className="settings-section-intro">Name the key by its purpose so it is easy to recognize and revoke later.</p>
    <form className="api-key-create" onSubmit={create}>
      <label>Name<input aria-label="API key name" maxLength={80} required value={label} onChange={e=>setLabel(e.target.value)} placeholder="e.g. Laptop CLI, CI deploys"/></label>
      <div className="settings-field"><span className="settings-field-label">Access</span><SettingsSelect label="API key access" value={access} options={ACCESS_OPTIONS} onChange={value=>setAccess(value as Access)}/></div>
      <div className="settings-field"><span className="settings-field-label">Expires</span><SettingsSelect label="API key expiration" value={expiry} options={EXPIRY_OPTIONS.map(o=>({value:o.value,label:o.label,description:o.value==='never'?'Stays valid until you revoke it':`Valid until ${formatDate(new Date(Date.now()+Number(o.value)*86400000).toISOString())}`,meta:o.value==='never'?'No expiry':`${o.value} days`}))} onChange={setExpiry}/></div>
      <button className="primary" disabled={busy||!label.trim()}>{busy?'Creating…':'Create API key'}</button>
    </form>
    </section>

    {message&&<p role={message.kind==='error'?'alert':'status'} className={message.kind==='error'?'error':'api-key-note'}>{message.text}</p>}

    <section className="settings-group api-key-inventory" aria-labelledby="api-key-list-title">
    <div className="settings-section-head"><div><span className="settings-section-kicker">Inventory</span><h2 id="api-key-list-title">Your API keys</h2></div><span className="settings-badge">{keys.length} {keys.length===1?'key':'keys'}</span></div>
    <div className="api-key-toolbar">
      <input type="search" aria-label="Search API keys" placeholder="Search by name or key ending" value={query} onChange={e=>setQuery(e.target.value)}/>
      <div role="group" aria-label="Filter by status" className="api-key-filters">
        {(['all','active','expired','revoked'] as StatusFilter[]).map(s=>
          <button key={s} type="button" aria-pressed={statusFilter===s} className={statusFilter===s?'selected':''} onClick={()=>setStatusFilter(s)}>
            {s==='all'?'All':STATUS_LABEL[s]} <span>{counts[s]}</span>
          </button>)}
      </div>
    </div>

    {loading&&<p role="status">Loading API keys…</p>}
    {!loading&&!keys.length&&<div className="api-key-empty"><b>No API keys yet</b><p>Create one above to connect the CLI or a script to your workspace.</p></div>}
    {!loading&&keys.length>0&&!visible.length&&<p role="status" className="api-key-empty">No keys match this search or filter.</p>}

    <ul className="api-key-list" aria-label="API keys">
      {visible.map(k=>{
        const status=keyStatus(k,now);
        const isConfirming=confirmId===k.id;
        return <li className="api-key-row" key={k.id}>
          <div className="api-key-main">
            <b>{k.label||'API key'}</b>
            <span className={`api-key-badge ${status}`}>{STATUS_LABEL[status]}</span>
            <span className="api-key-badge access">{k.access==='read'?'Read only':'Read and write'}</span>
          </div>
          <small>
            {k.hint ? <>Ends in <code>…{k.hint}</code></> : 'Key ending unavailable'} · Created {formatDate(k.createdAt)} · Last used {k.lastUsedAt?formatDate(k.lastUsedAt):'never'}
            {' · '}{k.expiresAt?`Expires ${formatDate(k.expiresAt)}`:'No expiration'}
            {status==='revoked'&&k.revokedAt?` · Revoked ${formatDate(k.revokedAt)}`:''}
          </small>
          {status!=='revoked'&&(isConfirming
            ? <div className="api-key-confirm" role="group" aria-label={`Confirm revoke ${k.label}`}>
                <span>Revoke “{k.label}”? Apps using it will stop working immediately. This cannot be undone.</span>
                <button type="button" className="danger" disabled={busy} onClick={()=>revoke(k)}>Revoke key</button>
                <button type="button" className="outline" onClick={()=>setConfirmId(null)}>Cancel</button>
              </div>
            : <button type="button" className="outline" aria-label={`Revoke ${k.label}`} onClick={()=>setConfirmId(k.id)}>Revoke</button>)}
        </li>;
      })}
    </ul>
    </section>
  </div>;
}
