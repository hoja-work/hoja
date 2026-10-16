/* A soft two-tone ring generated with WebAudio (no asset to ship). Browsers only allow audio after the
 * user has interacted with the page; when blocked the incoming-call card is still shown. */
let ctx: AudioContext | null = null;
let timer: number | undefined;

function beep(freq: number, at: number, len: number) {
  if (!ctx) return;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = 'sine';
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0, at);
  gain.gain.linearRampToValueAtTime(0.12, at + 0.02);
  gain.gain.linearRampToValueAtTime(0, at + len);
  osc.connect(gain).connect(ctx.destination);
  osc.start(at);
  osc.stop(at + len + 0.05);
}

export function startRingtone() {
  if (timer !== undefined) return;
  try {
    ctx = ctx || new AudioContext();
    void ctx.resume();
  } catch { return; }
  const cycle = () => {
    if (!ctx) return;
    const t = ctx.currentTime;
    beep(660, t, 0.35);
    beep(880, t + 0.4, 0.35);
  };
  cycle();
  timer = window.setInterval(cycle, 2000);
}

export function stopRingtone() {
  if (timer !== undefined) { window.clearInterval(timer); timer = undefined; }
}
