export type ChangeEvent = {seq: number; entity: string; entityId: string; action: string; teamId: string | null};

const CURSOR_KEY = 'hoja_change_cursor';

export function connectRealtime(token: string | null, onChange: (event: ChangeEvent) => void): () => void {
  let socket: WebSocket | null = null;
  let retry = 0;
  let timer: number | undefined;
  let closed = false;

  const url = () => {
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
    const after = Number(localStorage.getItem(CURSOR_KEY) || '0');
    const protocols = token && token !== 'session' ? ['bearer', token] : undefined;
    return {href: `${scheme}://${location.host}/ws?after=${after}`, protocols};
  };

  const open = () => {
    const target = url();
    socket = target.protocols ? new WebSocket(target.href, target.protocols) : new WebSocket(target.href);
    socket.onopen = () => { retry = 0; };
    socket.onmessage = (message) => {
      const event: ChangeEvent = JSON.parse(message.data);
      localStorage.setItem(CURSOR_KEY, String(event.seq));
      onChange(event);
    };
    socket.onclose = (close) => {
      socket = null;
      if (closed || close.code === 4401) return;
      const delay = Math.min(30000, 500 * 2 ** retry) * (0.5 + Math.random() / 2);
      retry += 1;
      timer = window.setTimeout(open, delay);
    };
  };

  open();
  return () => {
    closed = true;
    window.clearTimeout(timer);
    socket?.close();
  };
}
