// In-app replacements for window.confirm and window.prompt. Native dialogs are unstyled, block the whole page, and
// behave differently across phone browsers, so the app asks through these instead (enforced by local/no-native-dialog).
//   const ok = await confirmDialog('Archive this issue?', {danger: true});
//   const name = await promptDialog('Name this view:');   // string, or null if cancelled
//   await alertDialog('Could not save.');                // resolves when dismissed
import {useEffect, useState, type FormEvent} from 'react';

type Request = {message: string; danger: boolean; input: boolean; alert: boolean; value: string; resolve: (v: string | boolean | null) => void};

let current: Request | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach(l => l());

function ask(message: string, opts: {danger?: boolean; input?: boolean; alert?: boolean; value?: string}): Promise<string | boolean | null> {
  return new Promise(resolve => {
    // A second request while one is open cancels the first, so no caller waits forever.
    current?.resolve(current.input ? null : false);
    current = {message, danger: !!opts.danger, input: !!opts.input, alert: !!opts.alert, value: opts.value ?? '', resolve};
    emit();
  });
}

export function confirmDialog(message: string, opts: {danger?: boolean} = {}): Promise<boolean> {
  return ask(message, opts).then(v => v === true);
}

export function alertDialog(message: string): Promise<void> {
  return ask(message, {alert: true}).then(() => undefined);
}

export function promptDialog(message: string, value = ''): Promise<string | null> {
  return ask(message, {input: true, value}).then(v => (typeof v === 'string' ? v : null));
}

// Mounted once at the app root.
export function DialogHost() {
  const [, bump] = useState(0);
  const [value, setValue] = useState('');
  useEffect(() => {
    const l = () => { setValue(current?.value ?? ''); bump(n => n + 1); };
    listeners.add(l);
    return () => { listeners.delete(l); };
  }, []);
  const req = current;
  if (!req) return null;
  const close = (result: string | boolean | null) => {
    current = null;
    req.resolve(result);
    emit();
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = value.trim();
    if (trimmed) close(trimmed);
  };
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={e => { if (e.target === e.currentTarget) close(req.input ? null : false); }}>
      <form className="modal" role="dialog" aria-modal="true" aria-label={req.message} onSubmit={submit}>
        <p style={{margin: '0 0 12px', fontWeight: 550}}>{req.message}</p>
        {req.input && <input ref={el => el?.focus()} value={value} onChange={e => setValue(e.target.value)} aria-label={req.message} style={{width: '100%', marginBottom: 12}} />}
        <div style={{display: 'flex', gap: 8, justifyContent: 'flex-end'}}>
          {req.alert
            ? <button type="button" className="primary" onClick={() => close(true)}>OK</button>
            : <>
                <button type="button" onClick={() => close(req.input ? null : false)}>Cancel</button>
                {req.input
                  ? <button type="submit" className="primary" disabled={!value.trim()}>Create</button>
                  : <button type="button" className={req.danger ? 'danger' : 'primary'} onClick={() => close(true)}>Confirm</button>}
              </>}
        </div>
      </form>
    </div>
  );
}
