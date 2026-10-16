/* Browser side of the Cloudflare SFU protocol (see docs/meetings.md).
 *
 * Two peer connections, each with its own SFU session, so server-driven offers never collide
 * with ours: `pub` only sends my tracks, `sub` only receives everyone else's.
 *
 * Cost levers live here: the camera is sent as three simulcast layers and each receiver picks
 * the layer that fits its tile (egress is what Cloudflare bills), mic runs at 32 kbps mono,
 * muting a camera stops sending entirely, and nothing is pulled for tracks nobody publishes. */
import {meetingsApi, type Participant} from './api';

export type MediaKind = 'mic' | 'cam' | 'screen';
export type Rid = 'f' | 'h' | 'q';

type Slot = {tr: RTCRtpTransceiver; name: string};
type Sub = {pid: string; kind: MediaKind; name: string; sessionId: string; mid?: string; rid?: Rid};

const CAM_LAYERS: RTCRtpEncodingParameters[] = [
  {rid: 'f', maxBitrate: 3_000_000, maxFramerate: 30},
  {rid: 'h', scaleResolutionDownBy: 2, maxBitrate: 700_000, maxFramerate: 30},
  {rid: 'q', scaleResolutionDownBy: 4, maxBitrate: 200_000, maxFramerate: 20},
];
const MIC_BITRATE = 32_000;
const SCREEN_BITRATE = 2_500_000;
const STATS_EVERY = 10_000;
const USAGE_EVERY = 30_000;

export type MediaStats = {inKbps: number; outKbps: number; bytesIn: number; bytesOut: number};

export type MediaEvents = {
  /** A remote participant's stream appeared (stream) or went away (null). */
  onRemote: (pid: string, kind: MediaKind, stream: MediaStream | null) => void;
  onStats?: (stats: MediaStats) => void;
  /** Connection died and could not recover; the room should rebuild the engine. */
  onFailure?: (why: string) => void;
};

const tag = () => Array.from(crypto.getRandomValues(new Uint8Array(5)), (b) => b.toString(16).padStart(2, '0')).join('');

export class MeetingMedia {
  private pub!: RTCPeerConnection;
  private sub!: RTCPeerConnection;
  private pubApi: ReturnType<typeof meetingsApi.sfu>;
  private subApi: ReturnType<typeof meetingsApi.sfu>;
  private queue: Promise<unknown> = Promise.resolve();
  private slots: Partial<Record<MediaKind, Slot>> = {};
  private subs = new Map<string, Sub>();
  private byMid = new Map<string, Sub>();
  private id = tag();
  private closed = false;
  private timers: number[] = [];
  private last = {at: 0, in: 0, out: 0};
  private sent = {in: 0, out: 0};
  private bad: number | undefined;
  /** Counters carried over from a previous engine so usage never goes backwards after a rebuild. */
  baseIn = 0;
  baseOut = 0;

  constructor(private code: string, private me: string, private events: MediaEvents) {
    this.pubApi = meetingsApi.sfu(code, 'pub');
    this.subApi = meetingsApi.sfu(code, 'sub');
  }

  // ------------------------------------------------------------------ lifecycle
  async start(): Promise<void> {
    const [pub, sub] = await Promise.all([this.pubApi.session(), this.subApi.session()]);
    if (this.closed) return;
    const config = (s: {iceServers: RTCIceServer[]}): RTCConfiguration => ({iceServers: s.iceServers, bundlePolicy: 'max-bundle'});
    this.pub = new RTCPeerConnection(config(pub));
    this.sub = new RTCPeerConnection(config(sub));
    this.sub.ontrack = (ev) => {
      const info = ev.transceiver.mid ? this.byMid.get(ev.transceiver.mid) : undefined;
      if (info) this.events.onRemote(info.pid, info.kind, new MediaStream([ev.track]));
    };
    for (const pc of [this.pub, this.sub]) {
      pc.onconnectionstatechange = () => this.watch(pc);
    }
    this.timers.push(window.setInterval(() => void this.poll(), STATS_EVERY));
    this.timers.push(window.setInterval(() => void this.report(), USAGE_EVERY));
  }

  private watch(pc: RTCPeerConnection) {
    window.clearTimeout(this.bad);
    if (pc.connectionState === 'failed') {
      this.events.onFailure?.('connection failed');
    } else if (pc.connectionState === 'disconnected') {
      this.bad = window.setTimeout(() => {
        if (!this.closed && pc.connectionState !== 'connected') this.events.onFailure?.('connection lost');
      }, 8000);
    }
  }

  async stop(): Promise<void> {
    this.closed = true;
    window.clearTimeout(this.bad);
    this.timers.forEach((t) => window.clearInterval(t));
    await this.report().catch(() => undefined);
    this.pub?.close();
    this.sub?.close();
  }

  /** Serialize every negotiation: offers and answers must not interleave on one connection. */
  private enqueue<T>(job: () => Promise<T>): Promise<T> {
    const run = this.queue.then(job, job);
    this.queue = run.catch(() => undefined);
    return run;
  }

  // ------------------------------------------------------------------ publishing
  private async pushTracks(items: Slot[]): Promise<void> {
    const pc = this.pub;
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);
    const res = await this.pubApi.tracks({
      sessionDescription: {type: 'offer', sdp: offer.sdp},
      tracks: items.map((i) => ({location: 'local', mid: i.tr.mid, trackName: i.name})),
    });
    const failed = (res.tracks || []).find((t: any) => t.errorCode);
    if (failed) throw new Error(failed.errorDescription || 'The call service rejected a track.');
    await pc.setRemoteDescription(res.sessionDescription);
  }

  private async publish(kind: MediaKind, track: MediaStreamTrack | null): Promise<void> {
    const slot = this.slots[kind];
    if (slot) {
      await slot.tr.sender.replaceTrack(track);
      return;
    }
    if (!track) return;
    const name = `${kind}-${this.id}`;
    const init: RTCRtpTransceiverInit = {direction: 'sendonly'};
    if (kind === 'cam') init.sendEncodings = CAM_LAYERS;
    if (kind === 'screen') init.sendEncodings = [{maxBitrate: SCREEN_BITRATE, maxFramerate: 15}];
    let tr: RTCRtpTransceiver;
    try {
      tr = this.pub.addTransceiver(track, init);
    } catch {
      tr = this.pub.addTransceiver(track, {direction: 'sendonly'}); // browser without simulcast: single layer
    }
    const created = {tr, name};
    this.slots[kind] = created;
    await this.pushTracks([created]);
    if (kind === 'mic') {
      const params = tr.sender.getParameters();
      if (!params.encodings?.length) params.encodings = [{}];
      params.encodings[0].maxBitrate = MIC_BITRATE;
      await tr.sender.setParameters(params).catch(() => undefined);
    }
  }

  /** Pass null to stop sending (the connection and its negotiation stay in place). */
  setMic(track: MediaStreamTrack | null) { return this.enqueue(() => this.publish('mic', track)); }
  setCam(track: MediaStreamTrack | null) { return this.enqueue(() => this.publish('cam', track)); }
  setScreen(track: MediaStreamTrack | null) { return this.enqueue(() => this.publish('screen', track)); }

  // ------------------------------------------------------------------ subscribing
  /** Pull every published track we are not receiving yet; call whenever the roster changes. */
  syncRemote(participants: Participant[]): Promise<void> {
    return this.enqueue(async () => {
      if (this.closed) return;
      const want: Sub[] = [];
      for (const p of participants) {
        if (p.id === this.me || p.state !== 'admitted' || !p.sessionId) continue;
        for (const t of p.tracks) {
          const key = `${p.sessionId}:${t.name}`;
          if (!this.subs.has(key)) {
            const sub: Sub = {pid: p.id, kind: t.kind, name: t.name, sessionId: p.sessionId};
            this.subs.set(key, sub);
            want.push(sub);
          }
        }
      }
      if (!want.length) return;
      const drop = (s: Sub) => this.subs.delete(`${s.sessionId}:${s.name}`);
      let res: any;
      try {
        res = await this.subApi.tracks({
          tracks: want.map((w) => ({
            location: 'remote', sessionId: w.sessionId, trackName: w.name,
            ...(w.kind === 'cam' ? {simulcast: {preferredRid: 'h', ridNotAvailable: 'asciibetical'}} : {}),
          })),
        });
      } catch (err) {
        want.forEach(drop);
        throw err;
      }
      for (const t of res.tracks || []) {
        const sub = want.find((w) => w.name === t.trackName);
        if (!sub) continue;
        if (t.errorCode || !t.mid) drop(sub);
        else { sub.mid = t.mid; sub.rid = sub.kind === 'cam' ? 'h' : undefined; this.byMid.set(t.mid, sub); }
      }
      if (res.sessionDescription) {
        await this.sub.setRemoteDescription(res.sessionDescription);
        const answer = await this.sub.createAnswer();
        await this.sub.setLocalDescription(answer);
        await this.subApi.renegotiate({sessionDescription: {type: 'answer', sdp: answer.sdp}});
      }
    });
  }

  /** Ask for a different simulcast layer of one participant's camera (tile size changed). */
  setLayer(pid: string, rid: Rid): Promise<void> {
    const sub = [...this.subs.values()].find((s) => s.pid === pid && s.kind === 'cam' && s.mid);
    if (!sub || sub.rid === rid) return Promise.resolve();
    sub.rid = rid;
    return this.enqueue(async () => {
      if (this.closed) return;
      await this.subApi.update({tracks: [{mid: sub.mid, simulcast: {preferredRid: rid, ridNotAvailable: 'asciibetical'}}]});
    }).catch(() => { sub.rid = undefined; });
  }

  /** A participant left: drop the bookkeeping so a rejoin (new session) is pulled fresh. */
  forget(pid: string) {
    for (const [key, sub] of this.subs) {
      if (sub.pid !== pid) continue;
      this.subs.delete(key);
      if (sub.mid) this.byMid.delete(sub.mid);
      this.events.onRemote(pid, sub.kind, null);
    }
  }

  // ------------------------------------------------------------------ stats
  private async totals(): Promise<{in: number; out: number}> {
    let down = 0;
    let up = 0;
    const [rx, tx] = await Promise.all([this.sub?.getStats(), this.pub?.getStats()]);
    rx?.forEach((r: any) => { if (r.type === 'inbound-rtp') down += r.bytesReceived || 0; });
    tx?.forEach((r: any) => { if (r.type === 'outbound-rtp') up += r.bytesSent || 0; });
    return {in: down + this.baseIn, out: up + this.baseOut};
  }

  private async poll() {
    if (this.closed) return;
    const t = await this.totals().catch(() => null);
    if (!t) return;
    const now = performance.now();
    if (this.last.at) {
      const secs = (now - this.last.at) / 1000 || 1;
      this.events.onStats?.({
        inKbps: Math.round(((t.in - this.last.in) * 8) / 1000 / secs), outKbps: Math.round(((t.out - this.last.out) * 8) / 1000 / secs),
        bytesIn: t.in, bytesOut: t.out,
      });
    }
    this.last = {at: now, in: t.in, out: t.out};
  }

  /** Cumulative counters, used to total what a meeting cost (Cloudflare bills what it sends to us). */
  async counters(): Promise<{in: number; out: number}> { return this.totals(); }

  private async report() {
    const t = await this.totals();
    if (t.in === this.sent.in && t.out === this.sent.out) return;
    this.sent = t;
    await meetingsApi.usage(this.code, Math.round(t.in), Math.round(t.out));
  }
}

/** Layer that matches a tile: bigger tiles get more pixels, background tabs get the smallest. */
export function ridForWidth(width: number, hidden = false): Rid {
  if (hidden) return 'q';
  if (width >= 900) return 'f';
  if (width >= 360) return 'h';
  return 'q';
}

/** Mic constraints tuned for voice: mono, cancelled echo, steady level. */
export const MIC_CONSTRAINTS: MediaTrackConstraints = {
  echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1,
};
/** 1080p30 ceiling; the simulcast ladder below it keeps small tiles cheap. */
export const CAM_CONSTRAINTS: MediaTrackConstraints = {
  width: {ideal: 1920}, height: {ideal: 1080}, frameRate: {ideal: 30},
};
