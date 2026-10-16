import {useEffect, useMemo, useRef, useState} from 'react';
import {CalendarDays, Check, Copy, Search, X} from 'lucide-react';
import {googleCalendarUrl, joinUrl, meetingsApi, type Access, type CreateInput, type Meeting, type Person} from './api';
import './meetings-page.css';

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const DURATIONS = [15, 30, 45, 60, 90, 120];

/** Chips + search over workspace members; with `allowEmail`, any valid address can be added too. */
export function PeoplePicker({people, value, onChange, excludeId, allowEmail, placeholder, autoFocus}: {
  people: Person[]; value: string[]; onChange: (next: string[]) => void; excludeId?: string;
  allowEmail?: boolean; placeholder?: string; autoFocus?: boolean;
}) {
  const [query, setQuery] = useState('');
  const [error, setError] = useState('');
  const byId = useMemo(() => new Map(people.map((p) => [p.id, p])), [people]);
  const needle = query.trim().toLowerCase();
  const matches = useMemo(() => people
    .filter((p) => p.id !== excludeId && !value.includes(p.id))
    .filter((p) => !needle || `${p.name} ${p.email || ''}`.toLowerCase().includes(needle))
    .slice(0, 6), [people, value, excludeId, needle]);

  const add = (token: string) => { onChange([...value, token]); setQuery(''); setError(''); };
  const addEmail = () => {
    const email = query.trim().toLowerCase();
    if (!email) return;
    if (!EMAIL.test(email)) { setError('Enter a valid email address.'); return; }
    if (value.includes(email)) { setQuery(''); return; }
    add(email);
  };

  return (
    <div className="mt-people">
      {value.length > 0 && (
        <ul className="mt-chips" aria-label="Selected people">
          {value.map((token) => (
            <li key={token} className="mt-chip">
              <span className="mt-chip-text">{byId.get(token)?.name || token}</span>
              <button type="button" aria-label={`Remove ${byId.get(token)?.name || token}`} onClick={() => onChange(value.filter((v) => v !== token))}><X size={12} /></button>
            </li>
          ))}
        </ul>
      )}
      <label className="mt-search">
        <Search size={14} aria-hidden="true" />
        <input
          value={query} autoFocus={autoFocus} aria-label="Search people"
          placeholder={placeholder || (allowEmail ? 'Search people or type an email' : 'Search people')}
          onChange={(e) => { setQuery(e.target.value); setError(''); }}
          onKeyDown={(e) => {
            if (e.key !== 'Enter') return;
            e.preventDefault();
            if (matches[0] && !query.includes('@')) add(matches[0].id);
            else if (allowEmail) addEmail();
          }}
        />
      </label>
      {error && <p className="mt-field-error" role="alert">{error}</p>}
      {(needle || matches.length > 0) && (
        <ul className="mt-suggest" role="listbox" aria-label="People">
          {matches.map((p) => (
            <li key={p.id}>
              <button type="button" role="option" aria-selected={false} onClick={() => add(p.id)}>
                <span className="mt-avatar" aria-hidden="true">{(p.name || '?')[0]?.toUpperCase()}</span>
                <span className="mt-suggest-text"><b>{p.name}</b><small>{p.email}</small></span>
              </button>
            </li>
          ))}
          {allowEmail && needle.includes('@') && (
            <li><button type="button" onClick={addEmail}><span className="mt-avatar" aria-hidden="true">@</span><span className="mt-suggest-text"><b>Invite {needle}</b><small>By email</small></span></button></li>
          )}
          {!matches.length && !(allowEmail && needle.includes('@')) && <li className="mt-suggest-empty">No matching people</li>}
        </ul>
      )}
    </div>
  );
}

const pad = (n: number) => String(n).padStart(2, '0');
const dateValue = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const timeValue = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

function defaultStart(): Date {
  const d = new Date(Date.now() + 3600000);
  d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0);
  return d;
}

export function ScheduleDialog({meeting, viewerId, initialDate, onClose, onSaved}: {
  meeting?: Meeting; viewerId?: string; initialDate?: Date; onClose: () => void; onSaved?: (m: Meeting) => void;
}) {
  const editing = !!meeting;
  const start = meeting?.scheduledStart ? new Date(meeting.scheduledStart) : initialDate ? (() => { const d = new Date(initialDate); d.setHours(defaultStart().getHours(), 0, 0, 0); return d; })() : defaultStart();
  const [title, setTitle] = useState(meeting?.title || '');
  const [description, setDescription] = useState(meeting?.description || '');
  const [date, setDate] = useState(dateValue(start));
  const [time, setTime] = useState(timeValue(start));
  const [duration, setDuration] = useState(meeting?.durationMinutes || 30);
  const [access, setAccess] = useState<Access>(meeting?.access === 'org' ? 'org' : 'invited');
  const [waitingRoom, setWaitingRoom] = useState(meeting?.settings.waitingRoom ?? true);
  const [linkEnabled, setLinkEnabled] = useState(meeting?.linkEnabled ?? false);
  const [muteOnEntry, setMuteOnEntry] = useState(meeting?.settings.muteOnEntry ?? true);
  const [screenAll, setScreenAll] = useState((meeting?.settings.screenShare ?? 'all') === 'all');
  const [chat, setChat] = useState(meeting?.settings.chatEnabled ?? true);
  const [invitees, setInvitees] = useState<string[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState<{meeting: Meeting; passcode: string | null} | null>(null);
  const [copied, setCopied] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => { void meetingsApi.people().then(setPeople).catch(() => setPeople([])); }, []);
  useEffect(() => { titleRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const startDate = useMemo(() => new Date(`${date}T${time}`), [date, time]);
  const validStart = !Number.isNaN(startDate.getTime());
  const inPast = validStart && startDate.getTime() < Date.now() - 60000;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) { setError('Give the meeting a title.'); return; }
    if (!validStart) { setError('Choose a valid date and time.'); return; }
    setBusy(true);
    setError('');
    try {
      const settings = {
        waitingRoom, linkEnabled, muteOnEntry, chatEnabled: chat, access,
        screenShare: screenAll ? 'all' as const : 'host' as const,
      };
      if (!editing) {
        const input: CreateInput = {
          title: title.trim(), description: description.trim() || undefined, ...settings,
          scheduledStart: startDate.toISOString(), durationMinutes: duration, invitees,
        };
        const res = await meetingsApi.create(input);
        setDone({meeting: res.meeting, passcode: res.passcode});
        onSaved?.(res.meeting);
      } else {
        const patch: Record<string, unknown> = {};
        const before = meeting!;
        if (title.trim() !== before.title) patch.title = title.trim();
        if ((description.trim() || null) !== (before.description || null)) patch.description = description.trim() || null;
        if (startDate.toISOString() !== (before.scheduledStart ? new Date(before.scheduledStart).toISOString() : '')) patch.scheduledStart = startDate.toISOString();
        if (duration !== before.durationMinutes) patch.durationMinutes = duration;
        if (access !== before.access) patch.access = access;
        if (waitingRoom !== before.settings.waitingRoom) patch.waitingRoom = waitingRoom;
        if (linkEnabled !== before.linkEnabled) patch.linkEnabled = linkEnabled;
        if (muteOnEntry !== before.settings.muteOnEntry) patch.muteOnEntry = muteOnEntry;
        if (settings.screenShare !== before.settings.screenShare) patch.screenShare = settings.screenShare;
        if (chat !== before.settings.chatEnabled) patch.chatEnabled = chat;
        let current = before;
        let passcode: string | null = null;
        if (Object.keys(patch).length) {
          const res = await meetingsApi.patch(before.code, patch);
          current = res.meeting;
          passcode = res.passcode;
        }
        if (invitees.length) await meetingsApi.invite(before.code, invitees);
        setDone({meeting: current, passcode});
        onSaved?.(current);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the meeting.');
    } finally {
      setBusy(false);
    }
  }

  async function copy(text: string) {
    try { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 1800); } catch { /* clipboard blocked */ }
  }

  return (
    <div className="mt-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="mt-dialog" role="dialog" aria-modal="true" aria-labelledby="mt-schedule-title">
        <header className="mt-dialog-head">
          <h2 id="mt-schedule-title">{done ? (editing ? 'Meeting updated' : 'Meeting scheduled') : editing ? 'Edit meeting' : 'Schedule a meeting'}</h2>
          <button type="button" className="mt-icon-btn" aria-label="Close" onClick={onClose}><X size={16} /></button>
        </header>

        {done ? (
          <div className="mt-dialog-body">
            <p className="mt-done-title"><Check size={16} aria-hidden="true" /> {done.meeting.title}</p>
            <div className="mt-copy-row">
              <input readOnly value={joinUrl(done.meeting.code)} aria-label="Meeting link" onFocus={(e) => e.currentTarget.select()} />
              <button type="button" className="mt-btn" onClick={() => void copy(joinUrl(done.meeting.code))}><Copy size={14} aria-hidden="true" />{copied ? 'Copied' : 'Copy'}</button>
            </div>
            {done.passcode && <p className="mt-note">Passcode for guests: <b className="mt-code">{done.passcode}</b> (the link above works without it only for invited members).</p>}
            {done.meeting.scheduledStart && <a className="mt-link" href={googleCalendarUrl(done.meeting)} target="_blank" rel="noreferrer"><CalendarDays size={14} aria-hidden="true" /> Add to Google Calendar</a>}
            <footer className="mt-dialog-foot"><button type="button" className="mt-btn mt-btn-primary" onClick={onClose}>Done</button></footer>
          </div>
        ) : (
          <form className="mt-dialog-body" onSubmit={(e) => void submit(e)}>
            <label className="mt-field"><span>Title</span>
              <input ref={titleRef} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} placeholder="Weekly sync" />
            </label>
            <div className="mt-row3">
              <label className="mt-field"><span>Date</span><input type="date" value={date} onChange={(e) => setDate(e.target.value)} required /></label>
              <label className="mt-field"><span>Start</span><input type="time" value={time} onChange={(e) => setTime(e.target.value)} required /></label>
              <label className="mt-field"><span>Duration</span>
                <select value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
                  {[...new Set([...DURATIONS, duration])].sort((a, b) => a - b).map((d) => <option key={d} value={d}>{d < 60 ? `${d} min` : `${d / 60} h`}</option>)}
                </select>
              </label>
            </div>
            {inPast && <p className="mt-note mt-note-warn">That time is in the past. The meeting will still be created.</p>}
            <label className="mt-field"><span>Description</span>
              <textarea rows={3} value={description} maxLength={5000} onChange={(e) => setDescription(e.target.value)} placeholder="Agenda or notes" />
            </label>
            <div className="mt-field"><span>{editing ? 'Invite more people' : 'People'}</span>
              <PeoplePicker people={people} value={invitees} onChange={setInvitees} excludeId={viewerId} allowEmail />
            </div>
            <label className="mt-field"><span>Who can join</span>
              <select value={access} onChange={(e) => setAccess(e.target.value as Access)}>
                <option value="invited">Only invited people</option>
                <option value="org">Everyone in the workspace</option>
              </select>
            </label>
            <fieldset className="mt-options">
              <legend>Options</legend>
              <label><input type="checkbox" checked={waitingRoom} onChange={(e) => setWaitingRoom(e.target.checked)} /> Waiting room — the host admits people</label>
              <label><input type="checkbox" checked={linkEnabled} onChange={(e) => setLinkEnabled(e.target.checked)} /> Public guest link — anyone with the link can ask to join</label>
              {linkEnabled && !waitingRoom && <p className="mt-note">Without a waiting room, guests need a passcode. One is generated for you.</p>}
              <label><input type="checkbox" checked={muteOnEntry} onChange={(e) => setMuteOnEntry(e.target.checked)} /> Mute people when they join</label>
              <label><input type="checkbox" checked={screenAll} onChange={(e) => setScreenAll(e.target.checked)} /> Everyone can share their screen</label>
              <label><input type="checkbox" checked={chat} onChange={(e) => setChat(e.target.checked)} /> Chat enabled</label>
            </fieldset>
            {error && <p className="mt-field-error" role="alert">{error}</p>}
            <footer className="mt-dialog-foot">
              <button type="button" className="mt-btn" onClick={onClose}>Cancel</button>
              <button type="submit" className="mt-btn mt-btn-primary" disabled={busy}>{busy ? 'Saving…' : editing ? 'Save changes' : 'Schedule'}</button>
            </footer>
          </form>
        )}
      </div>
    </div>
  );
}
