import React, {useEffect, useMemo, useState} from 'react';
import {tr} from './i18n';

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
  {value:'365',label:tr('1 year')},{value:'never',label:tr('No expiration')},
];

function keyStatus(k:ApiKey, now:number):'active'|'expired'|'revoked'{
  if(k.revokedAt) return 'revoked';
  if(k.expiresAt && Date.parse(k.expiresAt)<=now) return 'expired';
  return 'active';
}

const STATUS_LABEL={active:tr('Active'),expired:tr('Expired'),revoked:tr('Revoked')} as const;

function formatDate(value?:string){return value?new Date(value).toLocaleString():tr('Never')}

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
    setLoading(true);
    gql('query MyApiKeys{myApiKeys{id label access hint expiresAt lastUsedAt createdAt revokedAt}}')
      .then(d=>{if(live)setKeys(d.myApiKeys)})
      .catch(e=>{if(live)setMessage({kind:'error',text:tr("Could not load API keys: {{v0}}", {v0: (e as Error).message})})})
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

  const revoke=async(k:ApiKey)=>{
    setBusy(true);setMessage(null);
    try{
      const d=await gql('mutation($id:String!){apiKeyRevoke(id:$id)}',{id:k.id});
      if(!d.apiKeyRevoke)throw Error('Could not revoke this key. It may already be revoked.');
      setMessage({kind:'status',text:tr("Revoked “{{v0}}”. Apps using it will stop working.", {v0: k.label||'API key'})});
      setConfirmId(null);
      setRefresh(x=>x+1);
    }catch(err){setMessage({kind:'error',text:(err as Error).message})}
    finally{setBusy(false)}
  };

  const copySecret=async()=>{
    if(!secret)return;
    try{await navigator.clipboard.writeText(secret.value);setCopied(true)}
    catch{setMessage({kind:'error',text:tr('Copy failed. Select the key and copy it manually.')})}
  };

  return <section className="modal workspace-settings-form api-keys-settings">
    <h2>{tr("Personal API keys")}</h2>
    <p>{tr("Use API keys to access Hoja from the CLI, scripts, and integrations. A key acts as you, with the access level you choose.")}</p>

    {secret&&<div className="api-key-secret" role="status">
      <b>{tr("Copy “{{label}}” now", {label: secret.label})}</b>
      <p>{tr("This is the only time the full key is shown. Store it in a password manager or secret store.")}</p>
      <div className="api-key-secret-row">
        <code aria-label={tr("New API key")}>{secret.value}</code>
        <button type="button" className="outline" onClick={copySecret}>{copied?tr('Copied'):tr('Copy key')}</button>
      </div>
      <button type="button" className="auth-switch" onClick={()=>{setSecret(null);setCopied(false)}}>{tr("I’ve saved it")}</button>
    </div>}

    <form className="api-key-create" onSubmit={create}>
      <label>{tr("Name")}<input aria-label={tr("API key name")} maxLength={80} required value={label} onChange={e=>setLabel(e.target.value)} placeholder={tr("e.g. Laptop CLI, CI deploys")}/></label>
      <label>{tr("Access")}
        <select aria-label={tr("API key access")} value={access} onChange={e=>setAccess(e.target.value as Access)}>
          <option value="read_write">{tr("Read and write: can view and change data")}</option>
          <option value="read">{tr("Read only: can view data, cannot change it")}</option>
        </select>
      </label>
      <label>{tr("Expires")}
        <select aria-label={tr("API key expiration")} value={expiry} onChange={e=>setExpiry(e.target.value)}>
          {EXPIRY_OPTIONS.map(o=><option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>
      <button className="primary" disabled={busy||!label.trim()}>{busy?tr('Creating…'):tr('Create API key')}</button>
    </form>

    {message&&<p role={message.kind==='error'?'alert':'status'} className={message.kind==='error'?'error':'api-key-note'}>{message.text}</p>}

    <div className="api-key-toolbar">
      <input type="search" aria-label={tr("Search API keys")} placeholder={tr("Search by name or key ending")} value={query} onChange={e=>setQuery(e.target.value)}/>
      <div role="group" aria-label={tr("Filter by status")} className="api-key-filters">
        {(['all','active','expired','revoked'] as StatusFilter[]).map(s=>
          <button key={s} type="button" aria-pressed={statusFilter===s} className={statusFilter===s?'selected':''} onClick={()=>setStatusFilter(s)}>
            {s==='all'?tr('All'):tr(STATUS_LABEL[s])} <span>{counts[s]}</span>
          </button>)}
      </div>
    </div>

    {loading&&<p role="status">{tr("Loading API keys…")}</p>}
    {!loading&&!keys.length&&<div className="api-key-empty"><b>{tr("No API keys yet")}</b><p>{tr("Create one above to connect the CLI or a script to your workspace.")}</p></div>}
    {!loading&&keys.length>0&&!visible.length&&<p role="status" className="api-key-empty">{tr("No keys match this search or filter.")}</p>}

    <ul className="api-key-list" aria-label={tr("API keys")}>
      {visible.map(k=>{
        const status=keyStatus(k,now);
        const isConfirming=confirmId===k.id;
        return <li className="api-key-row" key={k.id}>
          <div className="api-key-main">
            <b>{k.label||tr('API key')}</b>
            <span className={`api-key-badge ${status}`}>{STATUS_LABEL[status]}</span>
            <span className="api-key-badge access">{k.access==='read'?tr('Read only'):tr('Read and write')}</span>
          </div>
          <small>
            {tr("Ends in")} <code>…{k.hint||'????'}</code> {tr("· Created")} {formatDate(k.createdAt)} {tr("· Last used")} {k.lastUsedAt?formatDate(k.lastUsedAt):tr('never')}
            {' · '}{k.expiresAt?tr('Expires {{date}}', {date: formatDate(k.expiresAt)}):tr('No expiration')}
            {status==='revoked'&&k.revokedAt?` · ${tr('Revoked')} ${formatDate(k.revokedAt)}`:''}
          </small>
          {status!=='revoked'&&(isConfirming
            ? <div className="api-key-confirm" role="group" aria-label={tr("Confirm revoke {{v0}}", {v0: k.label})}>
                <span>{tr("Revoke “{{label}}”? Apps using it will stop working immediately. This cannot be undone.", {label: k.label})}</span>
                <button type="button" className="danger" disabled={busy} onClick={()=>revoke(k)}>{tr("Revoke key")}</button>
                <button type="button" className="outline" onClick={()=>setConfirmId(null)}>{tr("Cancel")}</button>
              </div>
            : <button type="button" className="outline" aria-label={tr("Revoke {{label}}", {label: k.label})} onClick={()=>setConfirmId(k.id)}>{tr("Revoke")}</button>)}
        </li>;
      })}
    </ul>
  </section>;
}
