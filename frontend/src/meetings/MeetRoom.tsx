import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {
  Camera, CameraOff, Check, Copy, Hand, Info, Lock, LockOpen, MessageSquare, Mic, MicOff, MonitorUp, MoreVertical,
  PhoneOff, Pin, Radio, Send, Smile, UserPlus, Users, X,
} from 'lucide-react';
import {
  ApiError, apiToken, deviceId, deviceLabel, fmtDuration, fmtTime, fmtWhen, initials, joinUrl, loadGuest, loadSeat, meetingsApi,
  saveGuest, saveSeat, useGuestToken, useSeatToken,
  type Meeting, type Participant, type Person, type Preview, type Recap,
} from './api';
import {connectInbox, useInbox} from './inbox';
import {CAM_CONSTRAINTS, MIC_CONSTRAINTS, ridForWidth} from './rtc';
import {useRoom, type LocalSeed, type RoomAuth, type Streams} from './useRoom';
import './meet-room.css';

const EMOJI = ['👍', '👏', '❤️', '😂', '🎉', '😮', '🙏'];
const codeFromPath = () => (location.pathname.match(/^\/meet\/([^/?#]+)/)?.[1] || '').toLowerCase();

// ============================================================================ entry point
type Boot =
  | {kind: 'loading'}
  | {kind: 'error'; message: string; signIn?: boolean}
  | {kind: 'ready'; member: boolean; meeting?: Meeting; preview?: Preview; note?: string};

/** Standalone page for `/meet/<code>`: works for signed-in members and for anyone holding the link. */
export function MeetStandalone() {
  const code = codeFromPath();
  const [boot, setBoot] = useState<Boot>({kind: 'loading'});
  const [session, setSession] = useState<{seed: LocalSeed; auth: RoomAuth; meeting: Meeting; you: Participant} | null>(null);
  const [leftTitle, setLeftTitle] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const headers: Record<string, string> = {};
        const token = apiToken();
        if (token) headers.authorization = `Bearer ${token}`;
        const me = await fetch('/auth/me', {credentials: 'include', headers}).then((r) => r.ok).catch(() => false);
        if (me) {
          try {
            const {meeting} = await meetingsApi.get(code);
            if (live) setBoot({kind: 'ready', member: true, meeting});
            return;
          } catch (err) {
            if (!(err instanceof ApiError) || ![403, 404].includes(err.status)) throw err;
          }
        }
        const preview = await meetingsApi.preview(code);
        if (live) setBoot({kind: 'ready', member: false, preview, note: me ? 'You are signed in but not on the invite list, so you will join as a guest.' : undefined});
      } catch (err) {
        if (!live) return;
        const notFound = err instanceof ApiError && err.status === 404;
        setBoot({kind: 'error', signIn: notFound,
          message: notFound ? 'This meeting link is not valid, the meeting has ended, or guest links are turned off.' : err instanceof Error ? err.message : 'Could not load the meeting.'});
      }
    })();
    return () => { live = false; };
  }, [code]);

  useEffect(() => {
    document.title = session ? `${session.meeting.title} · Hoja Meet` : 'Hoja Meet';
  }, [session]);

  if (!code) return <Screen title="No meeting code" body="Open a meeting link like /meet/abc-defg-hij." home />;
  if (boot.kind === 'loading') return <Screen title="Loading meeting…" busy />;
  if (boot.kind === 'error') return <Screen title="Can't open this meeting" body={boot.message} home signIn={boot.signIn} />;
  if (session) {
    return <Room code={code} {...session} member={boot.member} onExit={(title) => { setLeftTitle(title); setSession(null); }} />;
  }
  return (
    <Lobby
      code={code} boot={boot} leftTitle={leftTitle}
      onJoined={(seed, auth, meeting, you) => { setLeftTitle(null); setSession({seed, auth, meeting, you}); }}
    />
  );
}

function Screen({title, body, busy, home, signIn}: {title: string; body?: string; busy?: boolean; home?: boolean; signIn?: boolean}) {
  return (
    <main className="mr-screen">
      <div className="mr-card mr-center" role={busy ? 'status' : undefined}>
        <h1>{title}</h1>
        {body && <p>{body}</p>}
        <div className="mr-row-actions">
          {signIn && <a className="mr-btn" href="/">Sign in to Hoja</a>}
          {home && <a className="mr-btn mr-ghost" href="/workspace/meetings">Back to Hoja</a>}
        </div>
      </div>
    </main>
  );
}

// ============================================================================ lobby (pre-join)
function Lobby({code, boot, leftTitle, onJoined}: {
  code: string; boot: Extract<Boot, {kind: 'ready'}>; leftTitle: string | null;
  onJoined: (seed: LocalSeed, auth: RoomAuth, meeting: Meeting, you: Participant) => void;
}) {
  const status = boot.meeting?.status || boot.preview?.status;
  const title = boot.meeting?.title || boot.preview?.title || 'Meeting';
  const hostName = boot.meeting?.hostName || boot.preview?.hostName;
  const needsPasscode = boot.member ? false : !!boot.preview?.passcodeRequired;
  const waitingRoom = boot.meeting?.settings.waitingRoom ?? boot.preview?.waitingRoom ?? true;
  const seat = useMemo(() => loadGuest(code), [code]);
  const url = useMemo(() => new URLSearchParams(location.search), []);
  const [name, setName] = useState(seat?.name || '');
  const [passcode, setPasscode] = useState(url.get('pwd') || '');
  const [mic, setMic] = useState<MediaStreamTrack | null>(null);
  const [cam, setCam] = useState<MediaStreamTrack | null>(null);
  const [micOn, setMicOn] = useState(true);
  const [camOn, setCamOn] = useState(true);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [mediaNote, setMediaNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [conflict, setConflict] = useState<{device: string; canCompanion: boolean} | null>(null);
  const video = useRef<HTMLVideoElement>(null);
  const held = useRef<{mic: MediaStreamTrack | null; cam: MediaStreamTrack | null; handedOff: boolean}>({mic: null, cam: null, handedOff: false});

  const acquire = useCallback(async () => {
    const tracks: MediaStreamTrack[] = [];
    const notes: string[] = [];
    try {
      tracks.push(...(await navigator.mediaDevices.getUserMedia({audio: MIC_CONSTRAINTS, video: CAM_CONSTRAINTS})).getTracks());
    } catch {
      for (const [label, constraints] of [['microphone', {audio: MIC_CONSTRAINTS}], ['camera', {video: CAM_CONSTRAINTS}]] as const) {
        try { tracks.push(...(await navigator.mediaDevices.getUserMedia(constraints)).getTracks()); } catch { notes.push(label); }
      }
    }
    const m = tracks.find((t) => t.kind === 'audio') || null;
    const c = tracks.find((t) => t.kind === 'video') || null;
    held.current.mic = m;
    held.current.cam = c;
    setMic(m); setCam(c); setCamOn(!!c); setMicOn(!!m);
    setMediaNote(notes.length ? `Allow ${notes.join(' and ')} access in your browser to be seen and heard. You can still join and listen.` : '');
    setDevices(await navigator.mediaDevices.enumerateDevices().catch(() => []));
  }, []);

  useEffect(() => {
    void acquire();
    const hold = held.current;
    return () => {
      if (!hold.handedOff) { hold.mic?.stop(); hold.cam?.stop(); }
    };
  }, [acquire]);

  useEffect(() => {
    if (video.current) video.current.srcObject = cam && camOn ? new MediaStream([cam]) : null;
  }, [cam, camOn]);

  const toggleCam = async () => {
    if (camOn && cam) { cam.stop(); held.current.cam = null; setCam(null); setCamOn(false); return; }
    try {
      const track = (await navigator.mediaDevices.getUserMedia({video: CAM_CONSTRAINTS})).getVideoTracks()[0];
      held.current.cam = track; setCam(track); setCamOn(true);
    } catch { setMediaNote('Allow camera access in your browser to turn your camera on.'); }
  };
  const toggleMic = () => { if (mic) { mic.enabled = !micOn; setMicOn(!micOn); } };
  const pick = async (kind: 'audioinput' | 'videoinput', deviceId: string) => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia(kind === 'audioinput'
        ? {audio: {...MIC_CONSTRAINTS, deviceId: {exact: deviceId}}} : {video: {...CAM_CONSTRAINTS, deviceId: {exact: deviceId}}});
      if (kind === 'audioinput') { mic?.stop(); const t = stream.getAudioTracks()[0]; t.enabled = micOn; held.current.mic = t; setMic(t); }
      else { cam?.stop(); const t = stream.getVideoTracks()[0]; held.current.cam = t; setCam(t); setCamOn(true); }
    } catch { setMediaNote('That device is not available.'); }
  };

  const join = async (mode?: 'switch' | 'companion') => {
    setError('');
    setConflict(null);
    if (!boot.member && !name.trim()) { setError('Enter your name so others know who you are.'); return; }
    setBusy(true);
    try {
      if (!boot.member && seat) useGuestToken(seat.token);
      // A refreshed companion device presents the seat it was given; a fresh choice starts clean.
      const savedSeat = boot.member && !mode ? loadSeat(code) : null;
      useSeatToken(savedSeat);
      const res = await meetingsApi.join(code, {name: name.trim() || undefined, passcode: passcode || undefined,
        deviceId: deviceId(), deviceLabel: deviceLabel(), mode});
      let auth: RoomAuth = {kind: 'member'};
      if (res.participant.kind === 'guest') {
        const token = res.guestToken || seat?.token;
        if (!token) throw new Error('Could not start a guest session.');
        saveGuest(code, {token, name: name.trim()});
        useGuestToken(token);
        auth = {kind: 'guest', token};
      } else if (res.seatToken || (res.participant.deviceOf && savedSeat)) {
        const token = res.seatToken || savedSeat!;
        saveSeat(code, token);
        useSeatToken(token);
        auth = {kind: 'member', seat: token};
      } else {
        saveSeat(code, null);
        useSeatToken(null);
      }
      held.current.handedOff = true;
      if (mic) mic.enabled = micOn;
      onJoined({mic, cam, micOn, camOn}, auth, res.meeting, res.participant);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'already_joined') {
        setConflict({device: String(err.data.device || 'another device'), canCompanion: !!err.data.canCompanion});
      } else {
        setError(err instanceof Error ? err.message : 'Could not join.');
      }
    } finally {
      setBusy(false);
    }
  };

  const verb = boot.meeting?.isHost ? 'Start meeting' : waitingRoom && !boot.member ? 'Ask to join' : 'Join now';
  const mics = devices.filter((d) => d.kind === 'audioinput');
  const cams = devices.filter((d) => d.kind === 'videoinput');

  if (status === 'ended' || status === 'cancelled') {
    return (
      <EndedView
        title={title} status={status} endedAt={boot.meeting?.endedAt ?? boot.preview?.endedAt ?? null}
        durationSeconds={boot.meeting?.durationSeconds ?? boot.preview?.durationSeconds ?? null}
        recap={boot.meeting?.recap} member={boot.member}
      />
    );
  }

  return (
    <main className="mr-screen mr-lobby">
      <section className="mr-lobby-preview" aria-label="Camera preview">
        <div className="mr-preview-frame">
          {cam && camOn ? <video ref={video} autoPlay playsInline muted className="mr-mirror" /> : (
            <div className="mr-avatar-big" aria-hidden="true">{initials(name || 'You')}</div>
          )}
          <div className="mr-preview-controls">
            <button type="button" className={`mr-round ${micOn && mic ? '' : 'off'}`} onClick={toggleMic} disabled={!mic} aria-pressed={micOn && !!mic} aria-label={micOn ? 'Mute microphone' : 'Unmute microphone'}>
              {micOn && mic ? <Mic size={18} /> : <MicOff size={18} />}
            </button>
            <button type="button" className={`mr-round ${camOn && cam ? '' : 'off'}`} onClick={toggleCam} aria-pressed={camOn && !!cam} aria-label={camOn ? 'Turn camera off' : 'Turn camera on'}>
              {camOn && cam ? <Camera size={18} /> : <CameraOff size={18} />}
            </button>
          </div>
        </div>
        {mediaNote && <p className="mr-note" role="status"><Info size={14} /> {mediaNote}</p>}
        <div className="mr-devices">
          {mics.length > 1 && (
            <label>Microphone<select onChange={(e) => void pick('audioinput', e.target.value)} defaultValue="">
              <option value="" disabled>Default</option>{mics.map((d) => <option key={d.deviceId} value={d.deviceId}>{d.label || 'Microphone'}</option>)}
            </select></label>
          )}
          {cams.length > 1 && (
            <label>Camera<select onChange={(e) => void pick('videoinput', e.target.value)} defaultValue="">
              <option value="" disabled>Default</option>{cams.map((d) => <option key={d.deviceId} value={d.deviceId}>{d.label || 'Camera'}</option>)}
            </select></label>
          )}
        </div>
      </section>

      <section className="mr-card mr-lobby-form">
        {leftTitle && <p className="mr-note">You left “{leftTitle}”. Rejoin below.</p>}
        <p className="mr-eyebrow">Ready to join?</p>
        <h1>{title}</h1>
        <p className="mr-sub">
          {hostName ? `Hosted by ${hostName}` : ''}
          {boot.meeting ? ` · ${fmtWhen(boot.meeting)}` : boot.preview?.scheduledStart ? ` · ${fmtWhen({scheduledStart: boot.preview.scheduledStart, durationMinutes: null, status: 'scheduled'})}` : ''}
        </p>
        {boot.note && <p className="mr-note"><Info size={14} /> {boot.note}</p>}
        {!boot.member && (
          <label className="mr-field">Your name
            <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="Full name" autoComplete="name" onKeyDown={(e) => e.key === 'Enter' && void join()} />
          </label>
        )}
        {needsPasscode && (
          <label className="mr-field">Passcode
            <input value={passcode} onChange={(e) => setPasscode(e.target.value)} maxLength={10} autoComplete="off" onKeyDown={(e) => e.key === 'Enter' && void join()} />
          </label>
        )}
        {error && <p className="mr-error" role="alert">{error}</p>}
        <button type="button" className="mr-btn mr-primary mr-wide" onClick={() => void join()} disabled={busy}>{busy ? 'Joining…' : verb}</button>
        {conflict && (
          <div className="mr-dialog-backdrop" role="presentation">
            <div className="mr-dialog" role="alertdialog" aria-modal="true" aria-labelledby="mr-conflict-title">
              <h2 id="mr-conflict-title">You're already in this meeting</h2>
              <p>You joined from your <b>{conflict.device}</b>. What should this device do?</p>
              <button type="button" className="mr-btn mr-primary mr-wide" disabled={busy} onClick={() => void join('switch')}>Use this device instead</button>
              <small>Moves the call here. Your {conflict.device} leaves the meeting.</small>
              <button type="button" className="mr-btn mr-wide" disabled={busy || !conflict.canCompanion} onClick={() => void join('companion')}>Join also with this device</button>
              <small>Keeps your {conflict.device} in the call and adds this one, handy to talk from your phone when your computer has no microphone, or to share your screen from one device while speaking from the other.</small>
              <button type="button" className="mr-btn mr-ghost mr-wide" onClick={() => setConflict(null)}>Cancel</button>
            </div>
          </div>
        )}
        {waitingRoom && !boot.meeting?.isHost && <p className="mr-hint">The host may need to let you in.</p>}
        {boot.member && <a className="mr-link" href="/workspace/meetings">Back to Hoja</a>}
      </section>
    </main>
  );
}

// ============================================================================ in-call
function Room({code, seed, auth, meeting: m0, you: y0, member, onExit}: {
  code: string; seed: LocalSeed; auth: RoomAuth; meeting: Meeting; you: Participant; member: boolean; onExit: (title: string) => void;
}) {
  const room = useRoom(code, auth, seed, m0, y0);
  const {phase, meeting, you, participants, waiting, chat, streams, localCam, localScreen, micOn, camOn, sharing} = room;
  const isMod = you.role === 'host' || you.role === 'cohost';
  const [panel, setPanel] = useState<'chat' | 'people' | null>(null);
  const [popover, setPopover] = useState<'react' | 'more' | 'invite' | 'leave' | null>(null);
  const [view, setView] = useState<'gallery' | 'speaker'>('gallery');
  const [pin, setPin] = useState<string | null>(null);
  const [seen, setSeen] = useState(0);
  const called = useMemo(() => new URLSearchParams(location.search).get('call') === '1', []);
  const answer = useInbox((s) => s.lastAnswer);

  useEffect(() => (member ? connectInbox() : undefined), [member]);
  useEffect(() => { if (panel === 'chat') setSeen(chat.length); }, [panel, chat.length]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { setPopover(null); } };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const admitted = useMemo(() => participants.filter((p) => p.state === 'admitted'), [participants]);
  const speaking = useSpeaking(streams, admitted, you.id);
  const loudest = useMemo(() => [...speaking.entries()].sort((a, b) => b[1] - a[1])[0]?.[0], [speaking]);

  // ---- pick a simulcast layer per remote tile from its rendered width
  const widths = useRef(new Map<string, number>());
  const layerTimer = useRef<number | undefined>(undefined);
  const applyLayers = useCallback(() => {
    window.clearTimeout(layerTimer.current);
    layerTimer.current = window.setTimeout(() => {
      widths.current.forEach((w, pid) => room.setLayer(pid, ridForWidth(w, document.hidden)));
    }, 350);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [room.setLayer]);
  const onWidth = useCallback((pid: string, w: number) => { widths.current.set(pid, w); applyLayers(); }, [applyLayers]);
  useEffect(() => {
    document.addEventListener('visibilitychange', applyLayers);
    return () => document.removeEventListener('visibilitychange', applyLayers);
  }, [applyLayers]);

  // ---- terminal phases
  useEffect(() => {
    if (phase === 'left') onExit(meeting.title);
  }, [phase, meeting.title, onExit]);

  const copyLink = async () => {
    try { await navigator.clipboard.writeText(joinUrl(code)); room.notify('Invite link copied'); } catch { room.notify(joinUrl(code)); }
  };

  if (phase === 'ended') return <EndedRoom code={code} title={meeting.title} member={member} />;
  if (phase === 'removed' || phase === 'denied' || phase === 'lost' || phase === 'moved') {
    const text = {
      moved: ['You moved this meeting to another device', 'This device left the call. You can bring it back here at any time.'],
      removed: ['You were removed', 'The host removed you from this meeting.'],
      denied: ['Entry was declined', 'The host did not let you in.'],
      lost: ['Connection lost', 'We could not stay connected to the meeting.'],
    }[phase];
    return (
      <main className="mr-screen"><div className="mr-card mr-center"><h1>{text[0]}</h1><p>{text[1]}</p>
        {(phase === 'lost' || phase === 'moved') && <button type="button" className="mr-btn mr-primary" onClick={() => location.reload()}>{phase === 'moved' ? 'Use this device again' : 'Reconnect'}</button>}
        <a className="mr-btn mr-ghost" href={member ? '/workspace/meetings' : '/'}>{member ? 'Back to Hoja' : 'Done'}</a></div></main>
    );
  }

  if (phase === 'connecting' || phase === 'waiting') {
    return (
      <main className="mr-screen"><div className="mr-card mr-center" role="status">
        <h1>{phase === 'waiting' ? 'Waiting for the host to let you in' : 'Connecting…'}</h1>
        <p>{phase === 'waiting' ? `“${meeting.title}” — you'll join automatically as soon as you're admitted.` : 'Setting up your connection.'}</p>
        {phase === 'waiting' && <button type="button" className="mr-btn mr-ghost" onClick={() => void room.leave()}>Cancel</button>}
      </div></main>
    );
  }

  // ---- layout model
  const shares = admitted.filter((p) => p.screen && (p.id === you.id ? !!localScreen : !!streams[p.id]?.screen));
  const stripFirst = [...admitted].sort((a, b) => (a.id === you.id ? -1 : b.id === you.id ? 1 : 0));
  const mainId = shares.length ? `screen:${shares[0].id}` : view === 'speaker' ? (pin || loudest || stripFirst.find((p) => p.id !== you.id)?.id || you.id) : null;
  const tiles = stripFirst.map((p) => tileFor(p, you.id, streams, localCam, camOn, micOn, speaking, pin));
  const screenTiles = shares.map((p) => ({...tileFor(p, you.id, streams, localCam, camOn, micOn, speaking, pin), key: `screen:${p.id}`, screen: true,
    stream: p.id === you.id ? localScreen : streams[p.id]?.screen || null, label: p.id === you.id ? 'Your screen' : `${p.name}'s screen`}));
  const all = [...screenTiles, ...tiles];
  const main = mainId ? all.find((t) => t.key === mainId) : null;
  const rest = main ? all.filter((t) => t.key !== main.key) : all;
  const cols = main ? 1 : rest.length <= 1 ? 1 : rest.length <= 4 ? 2 : rest.length <= 9 ? 3 : rest.length <= 16 ? 4 : 5;
  const alone = admitted.length <= 1;
  const lastCallAnswer = called && answer && answer.code === code ? answer : null;
  const unread = Math.max(0, chat.length - seen);
  const tileProps = (t: ReturnType<typeof tileFor>, big: boolean) => ({
    tile: t, big, onWidth: t.self ? undefined : (w: number) => onWidth(t.pid, big ? Math.max(w, 900) : w),
    onPin: () => { setPin(pin === t.pid ? null : t.pid); setView('speaker'); },
  });

  return (
    <main className="mr-room" data-sharing={shares.length ? 'true' : 'false'} onClick={() => popover && setPopover(null)}>
      {/* hidden audio sinks for everyone else */}
      {admitted.filter((p) => p.id !== you.id).map((p) => streams[p.id]?.mic && <RemoteAudio key={p.id} stream={streams[p.id].mic!} />)}

      <header className="mr-top">
        <div className="mr-top-left">
          <strong className="mr-title" title={meeting.title}>{meeting.title}</strong>
          {you.deviceOf && <span className="mr-chip" title="This device joined as a second device for you">{you.device || 'Companion'} companion</span>}
          {meeting.settings.locked && <span className="mr-chip" title="The meeting is locked"><Lock size={12} /> Locked</span>}
          {room.recording && <span className="mr-chip mr-rec"><Radio size={12} /> Recording</span>}
          {!room.online && <span className="mr-chip mr-warn">Reconnecting…</span>}
        </div>
        <div className="mr-top-right">
          {room.stats && <span className="mr-stats" title="Current bandwidth: download / upload">↓ {fmtKbps(room.stats.inKbps)} ↑ {fmtKbps(room.stats.outKbps)}</span>}
          <button type="button" className="mr-chip mr-chip-btn" onClick={(e) => { e.stopPropagation(); void copyLink(); }} title="Copy invite link"><Copy size={12} /> {code}</button>
        </div>
      </header>

      {alone && called && (
        <div className="mr-banner" role="status">
          {lastCallAnswer ? (lastCallAnswer.answer === 'decline' ? `${lastCallAnswer.by || 'They'} declined the call.` : `${lastCallAnswer.by || 'They'} didn't answer.`) : 'Calling… waiting for them to pick up.'}
          <button type="button" className="mr-btn mr-ghost mr-small" onClick={() => setPopover('invite')}>Call someone else</button>
        </div>
      )}
      {alone && !called && admitted.length === 1 && (
        <div className="mr-banner" role="status">You're the only one here. <button type="button" className="mr-btn mr-ghost mr-small" onClick={(e) => { e.stopPropagation(); setPopover('invite'); }}>Invite people</button></div>
      )}

      <div className={`mr-body ${panel ? 'with-panel' : ''}`}>
        <section className="mr-stage" aria-label="Participants" data-mode={main ? 'focus' : 'grid'}>
          {main ? (
            <>
              <div className="mr-main"><Tile {...tileProps(main, true)} /></div>
              <div className="mr-strip">{rest.map((t) => <Tile key={t.key} {...tileProps(t, false)} />)}</div>
            </>
          ) : (
            <div className="mr-grid" style={{gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`}} data-count={rest.length}>
              {rest.map((t) => <Tile key={t.key} {...tileProps(t, false)} />)}
            </div>
          )}
          <div className="mr-reactions" aria-hidden="true">
            {room.reactions.map((r) => <span key={r.id} style={{left: `${10 + ((r.id * 37) % 80)}%`}}>{r.emoji}</span>)}
          </div>
          <div className="mr-notices" role="status" aria-live="polite">{room.notices.map((n) => <div key={n.id}>{n.text}</div>)}</div>
        </section>

        {panel && (
          <aside className="mr-panel" aria-label={panel === 'chat' ? 'Chat' : 'People'} onClick={(e) => e.stopPropagation()}>
            <div className="mr-panel-head">
              <div role="tablist">
                <button type="button" role="tab" aria-selected={panel === 'people'} onClick={() => setPanel('people')}>People ({admitted.length})</button>
                <button type="button" role="tab" aria-selected={panel === 'chat'} onClick={() => setPanel('chat')}>Chat</button>
              </div>
              <button type="button" className="mr-icon" onClick={() => setPanel(null)} aria-label="Close panel"><X size={16} /></button>
            </div>
            {panel === 'chat' ? <ChatPanel room={room} admitted={admitted} /> : <PeoplePanel room={room} admitted={admitted} waiting={waiting} isMod={isMod} />}
          </aside>
        )}
      </div>

      <footer className="mr-controls" onClick={(e) => e.stopPropagation()}>
        <div className="mr-controls-main">
          <Ctl icon={micOn ? <Mic size={18} /> : <MicOff size={18} />} label={micOn ? 'Mute' : 'Unmute'} off={!micOn} onClick={() => void room.toggleMic()} />
          <Ctl icon={camOn ? <Camera size={18} /> : <CameraOff size={18} />} label={camOn ? 'Stop video' : 'Start video'} off={!camOn} onClick={() => void room.toggleCam()} />
          <Ctl icon={<MonitorUp size={18} />} label={sharing ? 'Stop sharing' : 'Share'} active={sharing} onClick={() => void room.toggleShare()} hidden={(meeting.settings.screenShare === 'host' && !isMod) || !navigator.mediaDevices?.getDisplayMedia} />
          <Ctl icon={<Hand size={18} />} label={you.handRaised ? 'Lower hand' : 'Raise hand'} active={you.handRaised} onClick={() => void room.hand(!you.handRaised).catch(() => undefined)} />
          <div className="mr-pop-wrap">
            <Ctl icon={<Smile size={18} />} label="React" onClick={() => setPopover(popover === 'react' ? null : 'react')} />
            {popover === 'react' && (
              <div className="mr-pop mr-emoji" role="menu">
                {EMOJI.map((e) => <button key={e} type="button" role="menuitem" onClick={() => { void room.react(e).catch(() => undefined); setPopover(null); }}>{e}</button>)}
              </div>
            )}
          </div>
          <Ctl icon={<MessageSquare size={18} />} label="Chat" active={panel === 'chat'} badge={panel === 'chat' ? 0 : unread} onClick={() => setPanel(panel === 'chat' ? null : 'chat')} />
          <Ctl icon={<Users size={18} />} label="People" active={panel === 'people'} badge={isMod ? waiting.length : 0} onClick={() => setPanel(panel === 'people' ? null : 'people')} />
          {member && (
            <div className="mr-pop-wrap">
              <Ctl icon={<UserPlus size={18} />} label="Invite" onClick={() => setPopover(popover === 'invite' ? null : 'invite')} />
              {popover === 'invite' && <InvitePopover code={code} onCopy={() => void copyLink()} notify={room.notify} />}
            </div>
          )}
          <div className="mr-pop-wrap">
            <Ctl icon={<MoreVertical size={18} />} label="More" onClick={() => setPopover(popover === 'more' ? null : 'more')} />
            {popover === 'more' && (
              <div className="mr-pop mr-menu" role="menu">
                <button type="button" role="menuitem" onClick={() => { setView(view === 'gallery' ? 'speaker' : 'gallery'); setPin(null); setPopover(null); }}>{view === 'gallery' ? 'Speaker view' : 'Gallery view'}</button>
                <button type="button" role="menuitem" onClick={() => void copyLink().then(() => setPopover(null))}>Copy invite link</button>
                {isMod && <>
                  <hr />
                  <button type="button" role="menuitem" onClick={() => { void room.act('lock', {locked: !meeting.settings.locked}).catch((e) => room.notify(e.message)); setPopover(null); }}>
                    {meeting.settings.locked ? <><LockOpen size={14} /> Unlock meeting</> : <><Lock size={14} /> Lock meeting</>}
                  </button>
                  <button type="button" role="menuitem" onClick={() => { void room.act('mute_all', {allowUnmute: true}).catch((e) => room.notify(e.message)); setPopover(null); }}>Mute everyone</button>
                  <button type="button" role="menuitem" onClick={() => { void room.act('settings', {chatEnabled: !meeting.settings.chatEnabled}).catch((e) => room.notify(e.message)); setPopover(null); }}>
                    {meeting.settings.chatEnabled ? 'Disable chat' : 'Enable chat'}
                  </button>
                  <button type="button" role="menuitem" onClick={() => { void room.act('settings', {screenShare: meeting.settings.screenShare === 'all' ? 'host' : 'all'}).catch((e) => room.notify(e.message)); setPopover(null); }}>
                    {meeting.settings.screenShare === 'all' ? 'Only hosts can share' : 'Everyone can share'}
                  </button>
                  <button type="button" role="menuitem" onClick={() => { void room.act('settings', {waitingRoom: !meeting.settings.waitingRoom}).catch((e) => room.notify(e.message)); setPopover(null); }}>
                    {meeting.settings.waitingRoom ? 'Turn waiting room off' : 'Turn waiting room on'}
                  </button>
                </>}
              </div>
            )}
          </div>
        </div>
        <div className="mr-pop-wrap">
          <button type="button" className="mr-leave" onClick={() => (you.role === 'host' ? setPopover(popover === 'leave' ? null : 'leave') : void room.leave())}>
            <PhoneOff size={16} /> Leave
          </button>
          {popover === 'leave' && (
            <div className="mr-pop mr-menu mr-leave-menu" role="menu">
              <button type="button" role="menuitem" onClick={() => void room.leave()}>Leave meeting</button>
              <button type="button" role="menuitem" className="danger" onClick={() => void room.endForAll()}>End for everyone</button>
            </div>
          )}
        </div>
      </footer>
    </main>
  );
}

const fmtKbps = (k: number) => (k >= 1000 ? `${(k / 1000).toFixed(1)} Mbps` : `${k} kbps`);

type TileModel = ReturnType<typeof tileFor>;
function tileFor(p: Participant, me: string, streams: Streams, localCam: MediaStream | null, camOn: boolean, micOn: boolean,
  speaking: Map<string, number>, pin: string | null) {
  const self = p.id === me;
  return {
    key: p.id, pid: p.id, self, name: self ? `${p.name} (You)` : p.name, label: self ? `${p.name} (You)` : p.name,
    stream: (self ? localCam : streams[p.id]?.cam) || null,
    videoOn: self ? camOn : p.video,
    audioOn: self ? micOn : p.audio,
    hand: p.handRaised, role: p.role, speaking: speaking.has(p.id), pinned: pin === p.id, screen: false,
  };
}

function Tile({tile, big, onWidth, onPin}: {tile: TileModel; big: boolean; onWidth?: (w: number) => void; onPin: () => void}) {
  const ref = useRef<HTMLDivElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  useEffect(() => {
    if (video.current) video.current.srcObject = tile.stream;
  }, [tile.stream]);
  useEffect(() => {
    if (!onWidth || !ref.current || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([entry]) => onWidth(Math.round(entry.contentRect.width * (window.devicePixelRatio > 1 ? 1.5 : 1))));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, [onWidth]);
  const showVideo = tile.stream && (tile.screen || tile.videoOn);
  return (
    <div ref={ref} className={`mr-tile ${tile.speaking ? 'speaking' : ''} ${big ? 'big' : ''} ${tile.screen ? 'screen' : ''}`}>
      {showVideo ? (
        tile.screen && tile.self ? <div className="mr-sharing-self"><MonitorUp size={22} /> You are sharing your screen</div>
          : <video ref={video} autoPlay playsInline muted={tile.self} className={tile.self && !tile.screen ? 'mr-mirror' : tile.screen ? 'contain' : ''} />
      ) : <div className="mr-avatar-big" aria-hidden="true">{initials(tile.name)}</div>}
      <div className="mr-tile-label">
        {!tile.screen && (tile.audioOn ? null : <MicOff size={12} aria-label="Muted" />)}
        <span title={tile.label}>{tile.label}</span>
        {tile.role !== 'attendee' && !tile.screen && <small>{tile.role === 'host' ? 'Host' : 'Co-host'}</small>}
      </div>
      {tile.hand && <span className="mr-hand" title="Hand raised">✋</span>}
      <button type="button" className="mr-tile-pin" onClick={onPin} aria-label={tile.pinned ? 'Unpin' : `Pin ${tile.label}`}><Pin size={13} /></button>
    </div>
  );
}

function RemoteAudio({stream}: {stream: MediaStream}) {
  const ref = useRef<HTMLAudioElement>(null);
  useEffect(() => { if (ref.current) ref.current.srcObject = stream; }, [stream]);
  return <audio ref={ref} autoPlay />;
}

function Ctl({icon, label, onClick, off, active, badge, hidden}: {icon: React.ReactNode; label: string; onClick: () => void; off?: boolean; active?: boolean; badge?: number; hidden?: boolean}) {
  if (hidden) return null;
  return (
    <button type="button" className={`mr-ctl ${off ? 'off' : ''} ${active ? 'active' : ''}`} onClick={onClick} aria-label={label} aria-pressed={active || off ? true : undefined}>
      <span className="mr-ctl-icon">{icon}{!!badge && <em>{badge > 9 ? '9+' : badge}</em>}</span>
      <span className="mr-ctl-label">{label}</span>
    </button>
  );
}

// ============================================================================ panels
function ChatPanel({room, admitted}: {room: ReturnType<typeof useRoom>; admitted: Participant[]}) {
  const [text, setText] = useState('');
  const [to, setTo] = useState('');
  const end = useRef<HTMLDivElement>(null);
  const names = useMemo(() => new Map(admitted.map((p) => [p.id, p.name])), [admitted]);
  useEffect(() => { end.current?.scrollIntoView({block: 'end'}); }, [room.chat.length]);
  const submit = async () => {
    const body = text.trim();
    if (!body) return;
    setText('');
    try { await room.chatSend(body, to || undefined); } catch (err) { room.notify(err instanceof Error ? err.message : 'Message not sent.'); setText(body); }
  };
  const disabled = !room.meeting.settings.chatEnabled && room.you.role === 'attendee';
  return (
    <div className="mr-chat">
      <div className="mr-chat-log" role="log" aria-live="polite">
        {room.chat.length === 0 && <p className="mr-empty">Messages are visible to everyone in the meeting unless you pick a person.</p>}
        {room.chat.map((c) => (
          <div key={c.id} className={`mr-msg ${c.from === room.you.id ? 'mine' : ''}`}>
            <b>{c.from === room.you.id ? 'You' : names.get(c.from) || 'Someone'}{c.to ? ` → ${c.to === room.you.id ? 'you' : names.get(c.to) || 'someone'} (private)` : ''}</b>
            <p>{c.body}</p>
          </div>
        ))}
        <div ref={end} />
      </div>
      <form className="mr-chat-form" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        <select value={to} onChange={(e) => setTo(e.target.value)} aria-label="Send to">
          <option value="">Everyone</option>
          {admitted.filter((p) => p.id !== room.you.id).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <div className="mr-chat-row">
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder={disabled ? 'Chat is turned off' : 'Send a message'} maxLength={2000} disabled={disabled} aria-label="Message" />
          <button type="submit" className="mr-icon" disabled={disabled || !text.trim()} aria-label="Send"><Send size={16} /></button>
        </div>
      </form>
    </div>
  );
}

function PeoplePanel({room, admitted, waiting, isMod}: {room: ReturnType<typeof useRoom>; admitted: Participant[]; waiting: Participant[]; isMod: boolean}) {
  const [open, setOpen] = useState<string | null>(null);
  const run = (a: string, p: Record<string, unknown>) => { setOpen(null); void room.act(a, p).catch((e) => room.notify(e.message)); };
  const me = room.you;
  return (
    <div className="mr-people">
      {isMod && waiting.length > 0 && (
        <section className="mr-waiting" aria-label="Waiting room">
          <div className="mr-sec-head"><b>Waiting to join ({waiting.length})</b><button type="button" className="mr-btn mr-small" onClick={() => run('admit_all', {})}>Admit all</button></div>
          {waiting.map((p) => (
            <div key={p.id} className="mr-person">
              <span className="mr-av">{initials(p.name)}</span><span className="mr-pname" title={p.name}>{p.name}</span>
              <button type="button" className="mr-btn mr-small mr-primary" onClick={() => run('admit', {target: p.id})}><Check size={13} /> Admit</button>
              <button type="button" className="mr-btn mr-small mr-ghost" onClick={() => run('deny', {target: p.id})}>Deny</button>
            </div>
          ))}
        </section>
      )}
      <ul className="mr-plist">
        {admitted.map((p) => {
          const self = p.id === me.id;
          const canAct = isMod && !self && p.role !== 'host' && !(me.role === 'cohost' && p.role === 'cohost');
          return (
            <li key={p.id} className="mr-person">
              <span className="mr-av">{initials(p.name)}</span>
              <span className="mr-pname" title={p.name}>{p.name}{self ? ' (You)' : ''}{p.role !== 'attendee' && <small> · {p.role === 'host' ? 'Host' : 'Co-host'}</small>}{p.kind === 'guest' && <small> · Guest</small>}</span>
              {p.handRaised && <span title="Hand raised">✋</span>}
              {p.screen && <MonitorUp size={14} aria-label="Sharing screen" />}
              {p.audio ? <Mic size={14} aria-label="Microphone on" /> : <MicOff size={14} aria-label="Muted" />}
              {p.video ? <Camera size={14} aria-label="Camera on" /> : <CameraOff size={14} aria-label="Camera off" />}
              {(canAct || (me.role === 'host' && !self)) && (
                <div className="mr-pop-wrap">
                  <button type="button" className="mr-icon" onClick={() => setOpen(open === p.id ? null : p.id)} aria-label={`Actions for ${p.name}`}><MoreVertical size={15} /></button>
                  {open === p.id && (
                    <div className="mr-pop mr-menu" role="menu">
                      {canAct && <button type="button" role="menuitem" onClick={() => run('mute', {target: p.id, muted: !p.muted})}>{p.muted ? 'Ask to unmute' : 'Mute'}</button>}
                      {canAct && p.handRaised && <button type="button" role="menuitem" onClick={() => run('lower_hand', {target: p.id})}>Lower hand</button>}
                      {me.role === 'host' && p.kind === 'member' && <button type="button" role="menuitem" onClick={() => run('promote', {target: p.id, role: p.role === 'cohost' ? 'attendee' : 'cohost'})}>{p.role === 'cohost' ? 'Remove co-host' : 'Make co-host'}</button>}
                      {me.role === 'host' && p.kind === 'member' && <button type="button" role="menuitem" onClick={() => run('transfer_host', {target: p.id})}>Make host</button>}
                      {canAct && <button type="button" role="menuitem" className="danger" onClick={() => run('remove', {target: p.id})}>Remove</button>}
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function InvitePopover({code, onCopy, notify}: {code: string; onCopy: () => void; notify: (t: string) => void}) {
  const [people, setPeople] = useState<Person[]>([]);
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState('');
  useEffect(() => { void meetingsApi.people().then(setPeople).catch(() => undefined); }, []);
  const shown = people.filter((p) => `${p.name} ${p.email || ''}`.toLowerCase().includes(q.toLowerCase())).slice(0, 8);
  const call = async (p: Person) => {
    setBusy(p.id);
    try { await meetingsApi.ring(code, [p.id]); notify(`Calling ${p.name}…`); } catch (err) { notify(err instanceof Error ? err.message : 'Could not call.'); }
    setBusy('');
  };
  return (
    <div className="mr-pop mr-invite" role="dialog" aria-label="Invite people" onClick={(e) => e.stopPropagation()}>
      <button type="button" className="mr-btn mr-wide" onClick={onCopy}><Copy size={14} /> Copy invite link</button>
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a teammate to call" aria-label="Find a teammate" />
      <ul>
        {shown.map((p) => (
          <li key={p.id}><span className="mr-av">{initials(p.name)}</span><span className="mr-pname" title={p.email || p.name}>{p.name}</span>
            <button type="button" className="mr-btn mr-small mr-primary" disabled={busy === p.id} onClick={() => void call(p)}>Call</button></li>
        ))}
        {!shown.length && <li className="mr-empty">No one found</li>}
      </ul>
    </div>
  );
}

// ============================================================================ active speaker
/** Loudness per participant (only those currently speaking), from WebAudio analysers on their mic streams. */
function useSpeaking(streams: Streams, admitted: Participant[], me: string): Map<string, number> {
  const [levels, setLevels] = useState<Map<string, number>>(new Map());
  const nodes = useRef(new Map<string, {stream: MediaStream; source: MediaStreamAudioSourceNode; analyser: AnalyserNode}>());
  const ctx = useRef<AudioContext | null>(null);
  useEffect(() => {
    const live = new Set(admitted.filter((p) => p.id !== me).map((p) => p.id));
    for (const [pid, n] of nodes.current) {
      if (!live.has(pid) || streams[pid]?.mic !== n.stream) { n.source.disconnect(); nodes.current.delete(pid); }
    }
    for (const pid of live) {
      const s = streams[pid]?.mic;
      if (!s || nodes.current.has(pid)) continue;
      try {
        ctx.current = ctx.current || new AudioContext();
        const source = ctx.current.createMediaStreamSource(s);
        const analyser = ctx.current.createAnalyser();
        analyser.fftSize = 512;
        source.connect(analyser);
        nodes.current.set(pid, {stream: s, source, analyser});
      } catch { /* analysis is cosmetic */ }
    }
  }, [streams, admitted, me]);
  useEffect(() => {
    const buf = new Uint8Array(256);
    const timer = window.setInterval(() => {
      const next = new Map<string, number>();
      for (const [pid, n] of nodes.current) {
        n.analyser.getByteTimeDomainData(buf);
        let sum = 0;
        for (const v of buf) sum += ((v - 128) / 128) ** 2;
        const rms = Math.sqrt(sum / buf.length);
        if (rms > 0.04) next.set(pid, rms);
      }
      setLevels((prev) => (prev.size === next.size && [...next.keys()].every((k) => prev.has(k)) ? prev : next));
    }, 200);
    return () => { window.clearInterval(timer); ctx.current?.close().catch(() => undefined); ctx.current = null; nodes.current.clear(); };
  }, []);
  return levels;
}


// ============================================================================ after the call
/** What a finished meeting looks like to anyone who opens its link: it is over, how long it ran, who came. */
function EndedView({title, status, endedAt, durationSeconds, recap, member}: {
  title: string; status: string; endedAt: string | null; durationSeconds: number | null; recap?: Recap; member: boolean;
}) {
  const cancelled = status === 'cancelled';
  const people = recap?.attendees ?? [];
  return (
    <main className="mr-screen">
      <div className="mr-card mr-ended" role="status">
        <p className="mr-eyebrow">{cancelled ? 'Cancelled' : 'Meeting ended'}</p>
        <h1>{title}</h1>
        {cancelled ? <p>This meeting was cancelled, so there is nothing to join.</p> : (
          <p className="mr-sub">
            {endedAt ? `Ended ${fmtTime(endedAt, {weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'})}` : 'This meeting is over'}
            {durationSeconds != null ? ` · lasted ${fmtDuration(durationSeconds)}` : ''}
          </p>
        )}
        {!cancelled && recap && (
          people.length === 0 ? <p>Nobody joined.</p> : (
            <>
              <p className="mr-sec-title">Who joined · {recap.peopleCount}</p>
              <ul className="mr-recap">
                {people.map((p, i) => (
                  <li key={`${p.name}-${p.joinedAt}-${i}`}>
                    <span className="mr-av">{initials(p.name)}</span>
                    <span className="mr-pname" title={p.name}>{p.name}{p.device ? ` · ${p.device}` : ''}{p.kind === 'guest' ? ' · Guest' : ''}</span>
                    <small>{fmtTime(p.joinedAt)}{p.seconds != null ? ` · ${fmtDuration(p.seconds)}` : ''}</small>
                  </li>
                ))}
              </ul>
            </>
          )
        )}
        <p className="mr-hint">This link can't be used to rejoin.</p>
        <div className="mr-row-actions">
          <a className="mr-btn" href={member ? '/workspace/meetings' : '/'}>{member ? 'Back to Hoja' : 'Done'}</a>
        </div>
      </div>
    </main>
  );
}

/** The in-call "meeting ended" screen: same facts as the link shows, fetched fresh. */
function EndedRoom({code, title, member}: {code: string; title: string; member: boolean}) {
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  useEffect(() => { void meetingsApi.get(code).then((r) => setMeeting(r.meeting)).catch(() => undefined); }, [code]);
  return (
    <EndedView
      title={meeting?.title || title} status={meeting?.status || 'ended'} endedAt={meeting?.endedAt ?? null}
      durationSeconds={meeting?.durationSeconds ?? null} recap={meeting?.recap} member={member}
    />
  );
}
