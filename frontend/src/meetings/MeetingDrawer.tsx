import {useCallback, useEffect, useRef, useState} from 'react';
import {CalendarDays, Copy, Download, Link2, Pencil, Phone, Trash2, Users, Video, X} from 'lucide-react';
import {fmtWhen, googleCalendarUrl, joinUrl, meetingsApi, type Invitee, type Meeting, type Person, type Rsvp} from './api';
import {PeoplePicker, ScheduleDialog} from './ScheduleDialog';
import './meetings-page.css';

const RSVP_LABEL: Record<Rsvp, string> = {needsAction: 'No reply', accepted: 'Going', declined: 'Declined', tentative: 'Maybe'};

type Viewer = {id?: string; name?: string; email?: string; admin?: boolean};

export function MeetingDrawer({meeting: initial, viewer, onClose, onChanged}: {
  meeting: Meeting; viewer: Viewer; onClose: () => void; onChanged: () => void;
}) {
  const [meeting, setMeeting] = useState<Meeting>(initial);
  const [invitees, setInvitees] = useState<Invitee[] | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [adding, setAdding] = useState(false);
  const [toInvite, setToInvite] = useState<string[]>([]);
  const [editing, setEditing] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [calMenu, setCalMenu] = useState(false);
  const [passcode, setPasscode] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const closeRef = useRef<HTMLButtonElement>(null);

  const isHost = meeting.isHost ?? meeting.hostId === viewer.id;
  const canManage = isHost || !!viewer.admin;
  const over = meeting.status === 'ended' || meeting.status === 'cancelled';

  const reload = useCallback(async () => {
    try {
      const {meeting: fresh} = await meetingsApi.get(initial.code);
      setMeeting((m) => ({...m, ...fresh, isHost: m.isHost, myResponse: fresh.myResponse ?? m.myResponse}));
    } catch { /* keep what the list gave us */ }
    try { setInvitees(await meetingsApi.invitees(initial.code)); } catch { setInvitees(null); }
  }, [initial.code]);

  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => { void meetingsApi.people().then(setPeople).catch(() => setPeople([])); }, []);
  useEffect(() => { closeRef.current?.focus(); }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !editing) onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, editing]);

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true); setError(''); setNotice('');
    try { await fn(); if (ok) setNotice(ok); } catch (e) { setError(e instanceof Error ? e.message : 'Something went wrong.'); } finally { setBusy(false); }
  };

  const copy = async () => {
    try { await navigator.clipboard.writeText(joinUrl(meeting.code)); setCopied(true); window.setTimeout(() => setCopied(false), 1800); } catch { /* blocked */ }
  };

  const answer = (response: Exclude<Rsvp, 'needsAction'>) => run(async () => {
    await meetingsApi.rsvp(meeting.code, response);
    setMeeting((m) => ({...m, myResponse: response}));
    onChanged();
  });

  const invite = () => run(async () => {
    await meetingsApi.invite(meeting.code, toInvite);
    setToInvite([]); setAdding(false);
    await reload();
    onChanged();
  }, 'Invitations sent.');

  const ring = (userId: string) => run(async () => { await meetingsApi.ring(meeting.code, [userId]); }, 'Ringing…');

  const toggleLink = () => run(async () => {
    const res = await meetingsApi.patch(meeting.code, {linkEnabled: !meeting.linkEnabled});
    setMeeting((m) => ({...m, ...res.meeting, isHost: m.isHost}));
    setPasscode(res.passcode);
    onChanged();
  });

  const cancel = () => run(async () => {
    await meetingsApi.cancel(meeting.code);
    onChanged();
    onClose();
  });

  const gb = meeting.usage ? meeting.usage.egressBytes / 1e9 : 0;

  return (
    <div className="mt-backdrop mt-backdrop-right" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <aside className="mt-drawer" role="dialog" aria-modal="true" aria-labelledby="mt-drawer-title">
        <header className="mt-drawer-head">
          <div className="mt-drawer-titles">
            <span className={`mt-status mt-status-${meeting.status}`}>{meeting.status === 'live' ? 'Live now' : meeting.status}</span>
            <h2 id="mt-drawer-title" title={meeting.title}>{meeting.title}</h2>
            <p className="mt-muted">{fmtWhen(meeting)}</p>
          </div>
          <button ref={closeRef} type="button" className="mt-icon-btn" aria-label="Close details" onClick={onClose}><X size={16} /></button>
        </header>

        <div className="mt-drawer-body">
          {!over && (
            <div className="mt-actions">
              <a className="mt-btn mt-btn-primary" href={`/meet/${meeting.code}`}><Video size={14} aria-hidden="true" />{isHost ? (meeting.status === 'live' ? 'Join' : 'Start') : 'Join'}</a>
              <div className="mt-menu-wrap">
                <button type="button" className="mt-btn" aria-expanded={calMenu} onClick={() => setCalMenu((v) => !v)}><CalendarDays size={14} aria-hidden="true" />Add to calendar</button>
                {calMenu && (
                  <div className="mt-menu" role="menu">
                    <a role="menuitem" href={googleCalendarUrl(meeting)} target="_blank" rel="noreferrer" onClick={() => setCalMenu(false)}>Google Calendar</a>
                    <a role="menuitem" href={meetingsApi.icsUrl(meeting.code)} download={`${meeting.code}.ics`} onClick={() => setCalMenu(false)}><Download size={13} aria-hidden="true" />Download .ics</a>
                  </div>
                )}
              </div>
            </div>
          )}

          {meeting.myResponse && !isHost && !over && (
            <section className="mt-section" aria-label="Your response">
              <h3>Your response</h3>
              <div className="mt-segment" role="group" aria-label="RSVP">
                {(['accepted', 'tentative', 'declined'] as const).map((r) => (
                  <button key={r} type="button" disabled={busy} aria-pressed={meeting.myResponse === r} className={meeting.myResponse === r ? 'mt-on' : ''} onClick={() => void answer(r)}>{RSVP_LABEL[r]}</button>
                ))}
              </div>
            </section>
          )}

          <dl className="mt-facts">
            <div><dt>Host</dt><dd>{meeting.hostName || 'Unknown'}</dd></div>
            <div><dt>Who can join</dt><dd>{meeting.access === 'org' ? 'Everyone in the workspace' : meeting.access === 'team' ? 'Team members' : 'Invited people'}</dd></div>
            {meeting.hasPasscode && <div><dt>Passcode</dt><dd>Required for guests</dd></div>}
          </dl>

          {meeting.description && <p className="mt-description">{meeting.description}</p>}

          <section className="mt-section">
            <h3>Join link</h3>
            <div className="mt-copy-row">
              <input readOnly value={joinUrl(meeting.code)} aria-label="Meeting link" onFocus={(e) => e.currentTarget.select()} />
              <button type="button" className="mt-btn" onClick={() => void copy()}><Copy size={14} aria-hidden="true" />{copied ? 'Copied' : 'Copy'}</button>
            </div>
            {canManage && !over && (
              <label className="mt-switch">
                <input type="checkbox" checked={meeting.linkEnabled} disabled={busy} onChange={() => void toggleLink()} />
                <span><Link2 size={13} aria-hidden="true" /> Anyone with the link can ask to join</span>
              </label>
            )}
            {passcode && <p className="mt-note">New passcode: <b className="mt-code">{passcode}</b>. Share it with guests; it is not shown again.</p>}
          </section>

          <section className="mt-section">
            <div className="mt-section-head">
              <h3><Users size={14} aria-hidden="true" /> People{invitees ? ` · ${invitees.length}` : ''}</h3>
              {canManage && !over && <button type="button" className="mt-btn mt-btn-small" onClick={() => setAdding((v) => !v)}>{adding ? 'Close' : 'Invite people'}</button>}
            </div>
            {adding && (
              <div className="mt-invite-box">
                <PeoplePicker people={people} value={toInvite} onChange={setToInvite} excludeId={viewer.id} allowEmail autoFocus />
                <button type="button" className="mt-btn mt-btn-primary" disabled={busy || !toInvite.length} onClick={() => void invite()}>Send invitations</button>
              </div>
            )}
            {invitees === null ? (
              <p className="mt-muted">{canManage ? 'Loading people…' : 'The guest list is visible to the host.'}</p>
            ) : invitees.length === 0 ? (
              <p className="mt-muted">No one invited yet.</p>
            ) : (
              <ul className="mt-people-list">
                {invitees.map((i) => (
                  <li key={i.id}>
                    <span className="mt-avatar" aria-hidden="true">{(i.name || i.email || '?')[0]?.toUpperCase()}</span>
                    <span className="mt-person"><b>{i.name || i.email}</b>{i.name && i.email && <small>{i.email}</small>}</span>
                    {i.response && <span className={`mt-rsvp mt-rsvp-${i.response}`}>{RSVP_LABEL[i.response]}</span>}
                    {i.role === 'cohost' && <span className="mt-rsvp">Co-host</span>}
                    {canManage && i.userId && !over && i.userId !== viewer.id && (
                      <button type="button" className="mt-icon-btn" aria-label={`Call ${i.name || i.email}`} title="Call" disabled={busy} onClick={() => void ring(i.userId!)}><Phone size={14} /></button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {canManage && meeting.usage && (
            <p className="mt-muted mt-usage">~${meeting.usage.estimatedCostUsd.toFixed(2)} · {gb < 0.1 ? `${(gb * 1000).toFixed(0)} MB` : `${gb.toFixed(1)} GB`} streamed</p>
          )}

          {error && <p className="mt-field-error" role="alert">{error}</p>}
          {notice && <p className="mt-note" role="status">{notice}</p>}
        </div>

        {canManage && !over && (
          <footer className="mt-drawer-foot">
            {confirmCancel ? (
              <>
                <span className="mt-confirm-text">Cancel this meeting for everyone?</span>
                <button type="button" className="mt-btn" onClick={() => setConfirmCancel(false)}>Keep it</button>
                <button type="button" className="mt-btn mt-btn-danger" disabled={busy} onClick={() => void cancel()}>Cancel meeting</button>
              </>
            ) : (
              <>
                <button type="button" className="mt-btn" onClick={() => setEditing(true)}><Pencil size={14} aria-hidden="true" />Edit</button>
                <button type="button" className="mt-btn mt-btn-danger-ghost" onClick={() => setConfirmCancel(true)}><Trash2 size={14} aria-hidden="true" />Cancel meeting</button>
              </>
            )}
          </footer>
        )}
      </aside>
      {editing && (
        <ScheduleDialog
          meeting={meeting} viewerId={viewer.id} onClose={() => { setEditing(false); void reload(); }}
          onSaved={(m) => { setMeeting((prev) => ({...prev, ...m, isHost: prev.isHost, myResponse: prev.myResponse})); onChanged(); }}
        />
      )}
    </div>
  );
}
