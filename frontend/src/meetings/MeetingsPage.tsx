import {useCallback, useEffect, useState} from 'react';
import {Calendar as CalendarIcon, Check, ChevronRight, Phone, Plus, Video} from 'lucide-react';
import {fmtWhen, meetingsApi, type Meeting, type Person, fmtDuration} from './api';
import {Calendar} from './Calendar';
import {useInbox} from './inbox';
import {MeetingDrawer} from './MeetingDrawer';
import {PeoplePicker, ScheduleDialog} from './ScheduleDialog';
import './meetings-page.css';

type Tab = 'calendar' | 'upcoming' | 'past';
type Viewer = {id?: string; name?: string; email?: string; admin?: boolean};

const CODE = /[a-z]{3}-[a-z]{4}-[a-z]{4}/i;

function MeetingRow({meeting, onOpen}: {meeting: Meeting; onOpen: () => void}) {
  const pending = meeting.myResponse === 'needsAction' && !meeting.isHost;
  return (
    <li className="mt-row">
      <button type="button" className="mt-row-main" onClick={onOpen}>
        <span className={`mt-status mt-status-${meeting.status}`}>{meeting.status === 'live' ? 'Live' : meeting.status}</span>
        <span className="mt-row-text">
          <b>{meeting.title}</b>
          <small>{meeting.status === 'ended' ? `Ended${meeting.durationSeconds ? ` · lasted ${fmtDuration(meeting.durationSeconds)}` : ''} · ` : ''}{fmtWhen(meeting)}{meeting.hostName ? ` · ${meeting.isHost ? 'You' : meeting.hostName}` : ''}{pending ? ' · awaiting your reply' : ''}</small>
        </span>
        <ChevronRight size={14} aria-hidden="true" />
      </button>
      {(meeting.status === 'live' || meeting.status === 'scheduled') && (
        <a className="mt-btn mt-btn-small" href={`/meet/${meeting.code}`}>{meeting.isHost && meeting.status !== 'live' ? 'Start' : 'Join'}</a>
      )}
    </li>
  );
}

function Invitations({viewer: _viewer, onOpen, onChanged}: {viewer: Viewer; onOpen: (m: Meeting) => void; onChanged: () => void}) {
  const invitations = useInbox((s) => s.invitations);
  const removeInvitation = useInbox((s) => s.removeInvitation);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const answer = async (m: Meeting, response: 'accepted' | 'tentative' | 'declined') => {
    setBusy(m.code); setError('');
    try {
      await meetingsApi.rsvp(m.code, response);
      removeInvitation(m.code);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not send your reply.');
    } finally {
      setBusy('');
    }
  };

  if (!invitations.length) return null;
  return (
    <section className="mt-invites" aria-label="Invitations">
      <h2>Invitations <span className="mt-count">{invitations.length}</span></h2>
      {error && <p className="mt-field-error" role="alert">{error}</p>}
      <ul>
        {invitations.map((m) => (
          <li key={m.code} className="mt-invite">
            <button type="button" className="mt-invite-info" onClick={() => onOpen(m)}>
              <b>{m.title}</b>
              <small>{m.invitedBy ? `${m.invitedBy} · ` : ''}{fmtWhen(m)}</small>
            </button>
            <div className="mt-invite-actions">
              <button type="button" className="mt-btn mt-btn-small mt-btn-primary" disabled={busy === m.code} onClick={() => void answer(m, 'accepted')}><Check size={13} aria-hidden="true" />Accept</button>
              <button type="button" className="mt-btn mt-btn-small" disabled={busy === m.code} onClick={() => void answer(m, 'tentative')}>Maybe</button>
              <button type="button" className="mt-btn mt-btn-small" disabled={busy === m.code} onClick={() => void answer(m, 'declined')}>Decline</button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function CallPopover({viewer, onClose}: {viewer: Viewer; onClose: () => void}) {
  const [people, setPeople] = useState<Person[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => { void meetingsApi.people().then(setPeople).catch(() => setPeople([])); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const call = async () => {
    setBusy(true); setError('');
    try {
      const {meeting} = await meetingsApi.create({
        title: picked.length === 1 ? `Call with ${people.find((p) => p.id === picked[0])?.name || 'a colleague'}` : `${viewer.name || 'Group'} call`,
        waitingRoom: false, joinBeforeHost: true, access: 'invited', invitees: picked,
      });
      await meetingsApi.ring(meeting.code, picked);
      location.assign(`/meet/${meeting.code}?call=1`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not place the call.');
      setBusy(false);
    }
  };

  return (
    <div className="mt-popover" role="dialog" aria-label="Call someone">
      <p className="mt-popover-title">Call someone</p>
      <PeoplePicker people={people} value={picked} onChange={setPicked} excludeId={viewer.id} autoFocus />
      {error && <p className="mt-field-error" role="alert">{error}</p>}
      <div className="mt-popover-foot">
        <button type="button" className="mt-btn" onClick={onClose}>Close</button>
        <button type="button" className="mt-btn mt-btn-primary" disabled={!picked.length || busy} onClick={() => void call()}><Phone size={14} aria-hidden="true" />{busy ? 'Calling…' : 'Call'}</button>
      </div>
    </div>
  );
}

export function MeetingsPage({viewer}: {viewer: Viewer; onNavigate?: (view: string, path?: string) => void}) {
  const [tab, setTab] = useState<Tab>('calendar');
  const [list, setList] = useState<Meeting[]>([]);
  const [listError, setListError] = useState('');
  const [listLoading, setListLoading] = useState(false);
  const [open, setOpen] = useState<Meeting | null>(null);
  const [scheduling, setScheduling] = useState<{day?: Date} | null>(null);
  const [calling, setCalling] = useState(false);
  const [code, setCode] = useState('');
  const [codeError, setCodeError] = useState('');
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState('');
  const version = useInbox((s) => s.version);
  const [local, setLocal] = useState(0);
  const refresh = useCallback(() => { setLocal((n) => n + 1); useInbox.setState((s) => ({version: s.version + 1})); }, []);
  const reload = version + local;

  useEffect(() => {
    let live = true;
    void meetingsApi.invitations().then((rows) => { if (live) useInbox.setState({invitations: rows}); }).catch(() => undefined);
    return () => { live = false; };
  }, [reload]);

  useEffect(() => {
    if (tab === 'calendar') return;
    let live = true;
    setListLoading(true);
    meetingsApi.list(tab)
      .then((rows) => { if (live) { setList(rows); setListError(''); } })
      .catch((e) => { if (live) setListError(e instanceof Error ? e.message : 'Could not load meetings.'); })
      .finally(() => { if (live) setListLoading(false); });
    return () => { live = false; };
  }, [tab, reload]);

  const startNow = async () => {
    setStarting(true); setStartError('');
    try {
      const {meeting} = await meetingsApi.create({title: `${viewer.name || 'My'} meeting`, waitingRoom: false, access: 'invited'});
      location.assign(`/meet/${meeting.code}`);
    } catch (e) {
      setStartError(e instanceof Error ? e.message : 'Could not start a meeting.');
      setStarting(false);
    }
  };

  const joinWithCode = (e: React.FormEvent) => {
    e.preventDefault();
    const match = code.match(CODE);
    if (!match) { setCodeError('Enter a code like abc-defg-hij or paste a meeting link.'); return; }
    location.assign(`/meet/${match[0].toLowerCase()}`);
  };

  return (
    <div className="mt-page">
      <header className="mt-page-head">
        <div>
          <h1>Meetings</h1>
          <p className="mt-muted">Video calls with your team, guests and customers.</p>
        </div>
        <div className="mt-head-actions">
          <button type="button" className="mt-btn mt-btn-primary" disabled={starting} onClick={() => void startNow()}><Video size={14} aria-hidden="true" />{starting ? 'Starting…' : 'New meeting'}</button>
          <button type="button" className="mt-btn" onClick={() => setScheduling({})}><Plus size={14} aria-hidden="true" />Schedule</button>
          <div className="mt-menu-wrap">
            <button type="button" className="mt-btn" aria-expanded={calling} onClick={() => setCalling((v) => !v)}><Phone size={14} aria-hidden="true" />Call someone</button>
            {calling && <CallPopover viewer={viewer} onClose={() => setCalling(false)} />}
          </div>
        </div>
      </header>
      {startError && <p className="mt-field-error" role="alert">{startError}</p>}

      <form className="mt-join" onSubmit={joinWithCode}>
        <label className="mt-join-field">
          <span className="mt-sr">Meeting code or link</span>
          <input value={code} onChange={(e) => { setCode(e.target.value); setCodeError(''); }} placeholder="Enter a code or meeting link" aria-invalid={!!codeError} />
        </label>
        <button type="submit" className="mt-btn" disabled={!code.trim()}>Join</button>
        {codeError && <p className="mt-field-error" role="alert">{codeError}</p>}
      </form>

      <Invitations viewer={viewer} onOpen={setOpen} onChanged={refresh} />

      <div className="mt-tabs" role="tablist" aria-label="Meeting views">
        {([['calendar', 'Calendar'], ['upcoming', 'Upcoming'], ['past', 'Past']] as const).map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} className={tab === id ? 'mt-on' : ''} onClick={() => setTab(id)}>
            {id === 'calendar' && <CalendarIcon size={13} aria-hidden="true" />}{label}
          </button>
        ))}
      </div>

      {tab === 'calendar' ? (
        <Calendar version={reload} onOpen={setOpen} onCreateOn={(day) => setScheduling({day})} />
      ) : (
        <section className="mt-list" aria-label={tab === 'upcoming' ? 'Upcoming meetings' : 'Past meetings'}>
          {listError && <p className="mt-field-error" role="alert">{listError}</p>}
          {listLoading && !list.length && <p className="mt-muted" role="status">Loading…</p>}
          {!listLoading && !listError && !list.length && (
            <div className="mt-empty">
              <CalendarIcon size={22} aria-hidden="true" />
              <p>{tab === 'upcoming' ? 'No upcoming meetings.' : 'No past meetings yet.'}</p>
              {tab === 'upcoming' && <button type="button" className="mt-btn" onClick={() => setScheduling({})}>Schedule one</button>}
            </div>
          )}
          <ul>{list.map((m) => <MeetingRow key={m.id} meeting={m} onOpen={() => setOpen(m)} />)}</ul>
        </section>
      )}

      {open && <MeetingDrawer key={open.code} meeting={open} viewer={viewer} onClose={() => setOpen(null)} onChanged={refresh} />}
      {scheduling && <ScheduleDialog viewerId={viewer.id} initialDate={scheduling.day} onClose={() => setScheduling(null)} onSaved={refresh} />}
    </div>
  );
}
