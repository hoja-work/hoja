import {useEffect, useRef, useState} from 'react';
import {Bell, CalendarDays, Check, Clock, Info, Phone, PhoneOff, UserCheck, Video, X} from 'lucide-react';
import {meetingsApi} from './api';
import {askNotificationPermission, connectInbox, useInbox, type Toast} from './inbox';
import './meetings-page.css';

/** Pending invitations plus calls that are ringing right now. */
export function useMeetingBadge(): number {
  const invitations = useInbox((s) => s.invitations.length);
  const rings = useInbox((s) => s.rings.length);
  return invitations + rings;
}

export function MeetingBadge() {
  const n = useMeetingBadge();
  if (!n) return null;
  return <em className="meeting-badge" aria-label={`${n} pending`}>{n > 9 ? '9+' : n}</em>;
}

const ICONS: Record<Toast['kind'], typeof Bell> = {
  invited: CalendarDays, starting: Clock, info: Info, cancelled: X, rsvp: UserCheck,
};

function ToastCard({toast}: {toast: Toast}) {
  const dismiss = useInbox((s) => s.dismissToast);
  const removeInvitation = useInbox((s) => s.removeInvitation);
  const [busy, setBusy] = useState(false);
  const Icon = ICONS[toast.kind] || Bell;

  const accept = async () => {
    if (!toast.code) return;
    setBusy(true);
    try {
      await meetingsApi.rsvp(toast.code, 'accepted');
      removeInvitation(toast.code);
      useInbox.setState((s) => ({version: s.version + 1}));
      dismiss(toast.id);
    } catch {
      setBusy(false);
    }
  };

  return (
    <div className={`mt-toast mt-toast-${toast.kind}`} role="status">
      <span className="mt-toast-icon" aria-hidden="true"><Icon size={16} /></span>
      <div className="mt-toast-text">
        <b>{toast.title}</b>
        {toast.body && <span>{toast.body}</span>}
        {toast.actions && toast.code && (
          <div className="mt-toast-actions">
            {toast.kind === 'invited' && <button type="button" className="mt-btn mt-btn-small mt-btn-primary" disabled={busy} onClick={() => void accept()}><Check size={13} aria-hidden="true" />Accept</button>}
            {toast.kind === 'starting' && <a className="mt-btn mt-btn-small mt-btn-primary" href={`/meet/${toast.code}`}><Video size={13} aria-hidden="true" />Join now</a>}
            {toast.kind === 'invited' && <a className="mt-btn mt-btn-small" href="/workspace/meetings">View</a>}
          </div>
        )}
      </div>
      <button type="button" className="mt-icon-btn" aria-label="Dismiss notification" onClick={() => dismiss(toast.id)}><X size={14} /></button>
    </div>
  );
}

function IncomingCall() {
  const ring = useInbox((s) => s.rings[0]);
  const removeRing = useInbox((s) => s.removeRing);
  const [left, setLeft] = useState(0);
  const [busy, setBusy] = useState(false);
  const acceptRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!ring) return;
    const tick = () => setLeft(Math.max(0, Math.ceil((new Date(ring.expiresAt).getTime() - Date.now()) / 1000)));
    tick();
    const t = window.setInterval(() => {
      tick();
      if (new Date(ring.expiresAt).getTime() <= Date.now()) removeRing(ring.id);
    }, 500);
    return () => window.clearInterval(t);
  }, [ring, removeRing]);

  useEffect(() => { if (ring) acceptRef.current?.focus(); }, [ring?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const decline = async () => {
    if (!ring) return;
    const id = ring.id;
    removeRing(id);
    try { await meetingsApi.respondRing(id, 'decline'); } catch { /* already ended */ }
  };

  useEffect(() => {
    if (!ring) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') void decline(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  if (!ring) return null;

  const accept = async () => {
    setBusy(true);
    try {
      const res = await meetingsApi.respondRing(ring.id, 'accept');
      removeRing(ring.id);
      location.assign(`${res.joinPath}?call=1`);
    } catch {
      removeRing(ring.id);
    }
  };

  const caller = ring.from.name || 'Someone';
  return (
    <div className="mt-call" role="alertdialog" aria-labelledby="mt-call-title" aria-describedby="mt-call-sub">
      <span className="mt-call-pulse" aria-hidden="true"><Phone size={18} /></span>
      <div className="mt-call-text">
        <b id="mt-call-title">{caller} is calling</b>
        <span id="mt-call-sub">{ring.meeting.title} · {left}s</span>
      </div>
      <button type="button" className="mt-call-btn mt-call-decline" onClick={() => void decline()} aria-label={`Decline call from ${caller}`}><PhoneOff size={18} aria-hidden="true" /><span>Decline</span></button>
      <button ref={acceptRef} type="button" className="mt-call-btn mt-call-accept" disabled={busy} onClick={() => void accept()} aria-label={`Accept call from ${caller}`}><Phone size={18} aria-hidden="true" /><span>Accept</span></button>
    </div>
  );
}

/** Mount once in the app shell: opens the personal socket, shows toasts and the incoming-call card. */
export function MeetingNotifier() {
  const toasts = useInbox((s) => s.toasts);

  useEffect(() => connectInbox(), []);
  useEffect(() => {
    const onFirstClick = () => askNotificationPermission();
    window.addEventListener('click', onFirstClick, {once: true});
    return () => window.removeEventListener('click', onFirstClick);
  }, []);

  return (
    <>
      <IncomingCall />
      {toasts.length > 0 && (
        <div className="mt-toasts" aria-live="polite">
          {toasts.map((t) => <ToastCard key={t.id} toast={t} />)}
        </div>
      )}
    </>
  );
}
