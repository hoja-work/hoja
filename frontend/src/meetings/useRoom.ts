/* Everything stateful about being in a call: the room socket, the media engine, local devices.
 * The view (MeetRoom.tsx) only renders what this hook returns. */
import {useCallback, useEffect, useRef, useState} from 'react';
import {apiToken, meetingsApi, type ChatMessage, type Meeting, type Participant} from './api';
import {CAM_CONSTRAINTS, MIC_CONSTRAINTS, MeetingMedia, type MediaKind, type MediaStats, type Rid} from './rtc';

export type Phase = 'connecting' | 'waiting' | 'in' | 'ended' | 'removed' | 'denied' | 'left' | 'lost';
export type Streams = Record<string, Partial<Record<MediaKind, MediaStream>>>;
export type Notice = {id: number; text: string};
export type Reaction = {id: number; emoji: string; from: string};

export type RoomAuth = {kind: 'member'} | {kind: 'guest'; token: string};
export type LocalSeed = {mic: MediaStreamTrack | null; cam: MediaStreamTrack | null; micOn: boolean; camOn: boolean};

const CLOSE_PHASE: Record<number, Phase> = {4410: 'ended', 4411: 'removed', 4412: 'denied', 4403: 'denied', 4404: 'lost', 4401: 'lost'};
let nextId = 1;

export function useRoom(code: string, auth: RoomAuth, seed: LocalSeed, initialMeeting: Meeting, initialYou: Participant) {
  const [phase, setPhase] = useState<Phase>('connecting');
  const [online, setOnline] = useState(false);
  const [meeting, setMeeting] = useState<Meeting>(initialMeeting);
  const [you, setYou] = useState<Participant>(initialYou);
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [waiting, setWaiting] = useState<Participant[]>([]);
  const [chat, setChat] = useState<ChatMessage[]>([]);
  const [streams, setStreams] = useState<Streams>({});
  const [localCam, setLocalCam] = useState<MediaStream | null>(null);
  const [localScreen, setLocalScreen] = useState<MediaStream | null>(null);
  const [micOn, setMicOn] = useState(seed.micOn && !!seed.mic);
  const [camOn, setCamOn] = useState(seed.camOn && !!seed.cam);
  const [sharing, setSharing] = useState(false);
  const [reactions, setReactions] = useState<Reaction[]>([]);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [stats, setStats] = useState<MediaStats | null>(null);
  const [recording, setRecording] = useState(initialMeeting.settings.recording === 'recording');

  const ws = useRef<WebSocket | null>(null);
  const engine = useRef<MeetingMedia | null>(null);
  const starting = useRef(false);
  const tracks = useRef({mic: seed.mic, cam: seed.cam, screen: null as MediaStreamTrack | null});
  const acks = useRef(new Map<number, (m: any) => void>());
  const stateRef = useRef({you: initialYou, meeting: initialMeeting, micOn, camOn, sharing: false, phase: 'connecting' as Phase});
  const known = useRef(new Set<string>());
  const closing = useRef(false);
  const carry = useRef({in: 0, out: 0});

  stateRef.current = {...stateRef.current, you, meeting, micOn, camOn, sharing, phase};

  const notify = useCallback((text: string) => {
    const id = nextId++;
    setNotices((n) => [...n.slice(-2), {id, text}]);
    window.setTimeout(() => setNotices((n) => n.filter((x) => x.id !== id)), 4500);
  }, []);

  // ---------------------------------------------------------------- sending
  const send = useCallback((msg: Record<string, unknown>): Promise<any> => {
    const sock = ws.current;
    if (!sock || sock.readyState !== 1) return Promise.reject(new Error('Not connected.'));
    const id = nextId++;
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => { acks.current.delete(id); reject(new Error('The server did not answer.')); }, 10000);
      acks.current.set(id, (ack) => { window.clearTimeout(timer); ack.ok ? resolve(ack) : reject(new Error(ack.error || 'Not allowed.')); });
      sock.send(JSON.stringify({...msg, id}));
    });
  }, []);
  const act = useCallback((a: string, p: Record<string, unknown> = {}) => send({t: 'act', a, p}), [send]);
  const reportMedia = useCallback((audio: boolean, video: boolean, screen: boolean) => {
    void act('media', {audio, video, screen}).catch(() => undefined);
  }, [act]);

  // ---------------------------------------------------------------- media engine
  const startEngine = useCallback(async (me: Participant) => {
    if (starting.current || engine.current) return;
    starting.current = true;
    try {
      const eng = new MeetingMedia(code, me.id, {
        onRemote: (pid, kind, stream) => setStreams((s) => {
          const next = {...s, [pid]: {...s[pid]}};
          if (stream) next[pid][kind] = stream; else delete next[pid][kind];
          return next;
        }),
        onStats: setStats,
        onFailure: (why) => { notify(`Reconnecting media (${why})…`); void rebuild(); },
      });
      eng.baseIn = carry.current.in;
      eng.baseOut = carry.current.out;
      await eng.start();
      engine.current = eng;
      const t = tracks.current;
      if (t.mic) await eng.setMic(t.mic);
      if (t.cam && stateRef.current.camOn) await eng.setCam(t.cam);
      if (t.screen) await eng.setScreen(t.screen);
      reportMedia(stateRef.current.micOn, stateRef.current.camOn, !!t.screen);
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Could not start audio and video.');
      engine.current = null;
    } finally {
      starting.current = false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [code, notify, reportMedia]);

  const rebuild = useCallback(async () => {
    const old = engine.current;
    engine.current = null;
    setStreams({});
    known.current.clear();
    if (old) {
      carry.current = await old.counters().catch(() => carry.current);
      await old.stop().catch(() => undefined);
    }
    if (stateRef.current.phase === 'in') await startEngine(stateRef.current.you);
  }, [startEngine]);

  // Roster changes -> pull any newly published tracks, forget people who left.
  useEffect(() => {
    const eng = engine.current;
    if (!eng || phase !== 'in') return;
    const ids = new Set(participants.map((p) => p.id));
    for (const id of [...known.current]) if (!ids.has(id)) { eng.forget(id); known.current.delete(id); }
    participants.forEach((p) => known.current.add(p.id));
    void eng.syncRemote(participants).catch((e) => notify(e instanceof Error ? e.message : 'Could not receive video.'));
  }, [participants, phase, notify, online, you.state]);

  useEffect(() => {
    if (phase === 'in' && you.state === 'admitted' && !engine.current) void startEngine(you);
  }, [phase, you, startEngine]);

  // ---------------------------------------------------------------- socket
  useEffect(() => {
    let retry = 0;
    let timer: number | undefined;
    closing.current = false;

    const apply = (m: any) => {
      switch (m.t) {
        case 'snapshot':
          setMeeting(m.meeting); setYou(m.you); setParticipants(m.participants); setWaiting(m.waiting); setChat(m.chat);
          setRecording(m.meeting.settings.recording === 'recording');
          setPhase(m.you.state === 'admitted' ? 'in' : 'waiting');
          break;
        case 'joined':
          setParticipants((p) => [...p.filter((x) => x.id !== m.participant.id), m.participant]);
          if (m.participant.id !== stateRef.current.you.id) notify(`${m.participant.name} joined`);
          break;
        case 'left':
          setParticipants((p) => {
            const gone = p.find((x) => x.id === m.id);
            if (gone) notify(`${gone.name} left`);
            return p.filter((x) => x.id !== m.id);
          });
          setStreams((s) => { const { [m.id]: _gone, ...rest } = s; return rest; });
          break;
        case 'participant':
          setParticipants((p) => p.some((x) => x.id === m.participant.id) ? p.map((x) => (x.id === m.participant.id ? m.participant : x)) : [...p, m.participant]);
          if (m.participant.id === stateRef.current.you.id) setYou(m.participant);
          break;
        case 'waiting':
          setWaiting((w) => [...w.filter((x) => x.id !== m.participant.id), m.participant]);
          notify(`${m.participant.name} is waiting to join`);
          break;
        case 'waiting_done':
          setWaiting((w) => (m.id === '*' ? [] : w.filter((x) => x.id !== m.id)));
          break;
        case 'chat':
          setChat((c) => [...c, m.message]);
          break;
        case 'react': {
          const id = nextId++;
          setReactions((r) => [...r, {id, emoji: m.emoji, from: m.from}]);
          window.setTimeout(() => setReactions((r) => r.filter((x) => x.id !== id)), 3800);
          break;
        }
        case 'mute':
          if (m.muted) {
            const t = tracks.current.mic;
            if (t) t.enabled = false;
            setMicOn(false);
            notify('The host muted you');
          } else notify('The host asked you to unmute');
          break;
        case 'mute_all': {
          const t = tracks.current.mic;
          if (t) t.enabled = false;
          setMicOn(false);
          notify('The host muted everyone');
          break;
        }
        case 'meeting':
          setMeeting(m.meeting);
          break;
        case 'recording':
          setRecording(m.state === 'recording');
          notify(m.state === 'recording' ? `${m.by} started recording` : 'Recording stopped');
          break;
        case 'ack':
          acks.current.get(m.id)?.(m);
          acks.current.delete(m.id);
          break;
        case 'ended': setPhase('ended'); break;
        case 'removed': setPhase('removed'); break;
        case 'denied': setPhase('denied'); break;
        case 'emptied': notify('Everyone left the meeting'); break;
      }
    };

    const open = () => {
      const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
      const token = apiToken();
      const protocols = auth.kind === 'guest' ? ['guest', auth.token] : token ? ['bearer', token] : undefined;
      const url = `${scheme}://${location.host}/ws/meetings/${encodeURIComponent(code)}`;
      const sock = protocols ? new WebSocket(url, protocols) : new WebSocket(url);
      ws.current = sock;
      sock.onopen = () => { retry = 0; setOnline(true); };
      sock.onmessage = (e) => { try { apply(JSON.parse(e.data)); } catch { /* ignore */ } };
      sock.onclose = (e) => {
        setOnline(false);
        ws.current = null;
        if (closing.current) return;
        const terminal = CLOSE_PHASE[e.code];
        if (terminal) { setPhase(terminal); return; }
        timer = window.setTimeout(open, Math.min(8000, 400 * 2 ** retry++));
      };
    };
    open();
    const beat = window.setInterval(() => { if (ws.current?.readyState === 1) ws.current.send('{"t":"ping"}'); }, 20000);
    return () => {
      closing.current = true;
      window.clearTimeout(timer);
      window.clearInterval(beat);
      ws.current?.close();
    };
  }, [code, auth, notify]);

  // ---------------------------------------------------------------- local devices
  const toggleMic = useCallback(async () => {
    const s = stateRef.current;
    if (!s.micOn && s.you.muted && !s.meeting.settings.allowUnmute) { notify('The host has disabled unmuting'); return; }
    let track = tracks.current.mic;
    try {
      if (!track) {
        const stream = await navigator.mediaDevices.getUserMedia({audio: MIC_CONSTRAINTS});
        track = stream.getAudioTracks()[0];
        tracks.current.mic = track;
        await engine.current?.setMic(track);
      }
      track.enabled = !s.micOn;
      setMicOn(track.enabled);
      reportMedia(track.enabled, s.camOn, s.sharing);
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Microphone unavailable.');
    }
  }, [notify, reportMedia]);

  const toggleCam = useCallback(async () => {
    const s = stateRef.current;
    try {
      if (s.camOn) {
        tracks.current.cam?.stop();
        tracks.current.cam = null;
        await engine.current?.setCam(null);
        setLocalCam(null); setCamOn(false);
        reportMedia(s.micOn, false, s.sharing);
      } else {
        const stream = await navigator.mediaDevices.getUserMedia({video: CAM_CONSTRAINTS});
        const track = stream.getVideoTracks()[0];
        tracks.current.cam = track;
        await engine.current?.setCam(track);
        setLocalCam(new MediaStream([track])); setCamOn(true);
        reportMedia(s.micOn, true, s.sharing);
      }
    } catch (err) {
      notify(err instanceof Error ? err.message : 'Camera unavailable.');
    }
  }, [notify, reportMedia]);

  const stopShare = useCallback(async () => {
    tracks.current.screen?.stop();
    tracks.current.screen = null;
    await engine.current?.setScreen(null).catch(() => undefined);
    setLocalScreen(null); setSharing(false);
    reportMedia(stateRef.current.micOn, stateRef.current.camOn, false);
  }, [reportMedia]);

  const toggleShare = useCallback(async () => {
    if (stateRef.current.sharing) { await stopShare(); return; }
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({video: {frameRate: {ideal: 15, max: 30}}, audio: false});
      const track = stream.getVideoTracks()[0];
      track.contentHint = 'detail';
      track.onended = () => void stopShare();
      tracks.current.screen = track;
      await engine.current?.setScreen(track);
      setLocalScreen(new MediaStream([track])); setSharing(true);
      reportMedia(stateRef.current.micOn, stateRef.current.camOn, true);
    } catch (err) {
      if ((err as DOMException)?.name !== 'NotAllowedError') notify(err instanceof Error ? err.message : 'Could not share the screen.');
    }
  }, [notify, reportMedia, stopShare]);

  // Show the camera I already hold (from the lobby) as my own tile.
  useEffect(() => { if (seed.cam && seed.camOn) setLocalCam(new MediaStream([seed.cam])); }, [seed.cam, seed.camOn]);

  // ---------------------------------------------------------------- leaving
  const shutdown = useCallback(async () => {
    closing.current = true;
    const eng = engine.current;
    engine.current = null;
    await eng?.stop().catch(() => undefined);
    Object.values(tracks.current).forEach((t) => t?.stop());
    tracks.current = {mic: null, cam: null, screen: null};
  }, []);

  const leave = useCallback(async () => {
    await act('leave').catch(() => undefined);
    await shutdown();
    ws.current?.close();
    setPhase('left');
  }, [act, shutdown]);

  const endForAll = useCallback(async () => {
    await act('end').catch(() => undefined);
    await shutdown();
    setPhase('ended');
  }, [act, shutdown]);

  // Tear media down whenever the call is over for any reason.
  useEffect(() => {
    if (['ended', 'removed', 'denied', 'left', 'lost'].includes(phase)) void shutdown();
  }, [phase, shutdown]);
  useEffect(() => () => { void shutdown(); }, [shutdown]);

  const setLayer = useCallback((pid: string, rid: Rid) => { void engine.current?.setLayer(pid, rid); }, []);

  return {
    phase, online, meeting, you, participants, waiting, chat, streams, localCam, localScreen, micOn, camOn, sharing,
    reactions, notices, stats, recording,
    act, send, toggleMic, toggleCam, toggleShare, leave, endForAll, setLayer, notify,
    chatSend: (body: string, to?: string) => send({t: 'chat', body, ...(to ? {to} : {})}),
    hand: (raised: boolean) => send({t: 'hand', raised}),
    react: (emoji: string) => send({t: 'react', emoji}),
    ring: (ids: string[]) => meetingsApi.ring(code, ids),
  };
}
