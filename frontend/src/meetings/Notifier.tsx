import { useEffect, useRef, useState } from "react";
import {
  Bell,
  CalendarDays,
  Check,
  Clock,
  Info,
  Phone,
  PhoneOff,
  UserCheck,
  Video,
  X,
} from "lucide-react";
import { meetingsApi } from "./api";
import {
  askNotificationPermission,
  connectInbox,
  useInbox,
  type Toast,
} from "./inbox";
import "./meetings-page.css";

/** Pending invitations plus calls that are ringing right now. */
export function useMeetingBadge(): number {
  const invitations = useInbox((s) => s.invitations.length);
  const rings = useInbox((s) => s.rings.length);
  return invitations + rings;
}

export function MeetingBadge() {
  const n = useMeetingBadge();
  if (!n) return null;
  return (
    <em className="" aria-label={`${n} pending`}>
      {n > 9 ? "9+" : n}
    </em>
  );
}

const ICONS: Record<Toast["kind"], typeof Bell> = {
  invited: CalendarDays,
  starting: Clock,
  info: Info,
  cancelled: X,
  rsvp: UserCheck,
};

function ToastCard({ toast }: { toast: Toast }) {
  const dismiss = useInbox((s) => s.dismissToast);
  const removeInvitation = useInbox((s) => s.removeInvitation);
  const [busy, setBusy] = useState(false);
  const Icon = ICONS[toast.kind] || Bell;

  const accept = async () => {
    if (!toast.code) return;
    setBusy(true);
    try {
      await meetingsApi.rsvp(toast.code, "accepted");
      removeInvitation(toast.code);
      useInbox.setState((s) => ({ version: s.version + 1 }));
      dismiss(toast.id);
    } catch {
      setBusy(false);
    }
  };

  return (
    <div
      data-mt-toast=""
      className="flex items-start gap-2.5 py-2.75 px-3 border border-solid border-line rounded-[9px] bg-panel shadow-(--shadow) animate-[mt-rise_.2s_ease] motion-reduce:animate-none"
      data-fidelity-state={`mt-toast mt-toast-${toast.kind}`}
      role="status"
    >
      <span
        className="grid place-items-center flex-none w-7 h-7 rounded-[50%] bg-accent-soft text-accent"
        data-fidelity-state="mt-toast-icon"
        aria-hidden="true"
      >
        <Icon size={16} />
      </span>
      <div
        className="grid flex-1 gap-0.75 min-w-0"
        data-fidelity-state="mt-toast-text"
      >
        <b className="data-[fidelity-state~=mt-toast-text]:wrap-anywhere">
          {toast.title}
        </b>
        {toast.body && (
          <span className="data-[fidelity-state~=mt-toast-text]:wrap-anywhere data-[fidelity-state~=mt-toast-text]:text-muted">
            {toast.body}
          </span>
        )}
        {toast.actions && toast.code && (
          <div
            className="flex flex-wrap gap-1.5 mt-1.25"
            data-fidelity-state="mt-toast-actions"
          >
            {toast.kind === "invited" && (
              <button
                type="button"
                className="inline-flex items-center justify-center gap-1.5 min-h-7 py-1 px-2.25 border border-solid border-line rounded-md bg-accent text-white text-[12px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-accent disabled:opacity-55 disabled:cursor-default border-accent [&:hover]:filter-[brightness(1.1)] [:root[data-theme=dark]_&]:bg-accent [:root[data-theme=dark]_&]:text-[#171719] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                data-fidelity-state="mt-btn mt-btn-small mt-btn-primary"
                disabled={busy}
                onClick={() => void accept()}
              >
                <Check size={13} aria-hidden="true" />
                Accept
              </button>
            )}
            {toast.kind === "starting" && (
              <a
                className="inline-flex items-center justify-center gap-1.5 min-h-7 py-1 px-2.25 border border-solid border-line rounded-md bg-accent text-white text-[12px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-accent disabled:opacity-55 disabled:cursor-default border-accent [&:hover]:filter-[brightness(1.1)] [:root[data-theme=dark]_&]:bg-accent [:root[data-theme=dark]_&]:text-[#171719]"
                data-fidelity-state="mt-btn mt-btn-small mt-btn-primary"
                href={`/meet/${toast.code}`}
              >
                <Video size={13} aria-hidden="true" />
                Join now
              </a>
            )}
            {toast.kind === "invited" && (
              <a
                className="inline-flex items-center justify-center gap-1.5 min-h-7 py-1 px-2.25 border border-solid border-line rounded-md bg-panel text-foreground text-[12px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default"
                data-fidelity-state="mt-btn mt-btn-small"
                href="/workspace/meetings"
              >
                View
              </a>
            )}
          </div>
        )}
      </div>
      <button
        type="button"
        className="inline-grid place-items-center flex-none w-7.5 h-7.5 border-0 rounded-md bg-transparent text-muted cursor-pointer [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-50 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
        data-fidelity-state="mt-icon-btn"
        aria-label="Dismiss notification"
        onClick={() => dismiss(toast.id)}
      >
        <X size={14} />
      </button>
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
    const tick = () =>
      setLeft(
        Math.max(
          0,
          Math.ceil((new Date(ring.expiresAt).getTime() - Date.now()) / 1000),
        ),
      );
    tick();
    const t = window.setInterval(() => {
      tick();
      if (new Date(ring.expiresAt).getTime() <= Date.now()) removeRing(ring.id);
    }, 500);
    return () => window.clearInterval(t);
  }, [ring, removeRing]);

  useEffect(() => {
    if (ring) acceptRef.current?.focus();
  }, [ring?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const decline = async () => {
    if (!ring) return;
    const id = ring.id;
    removeRing(id);
    try {
      await meetingsApi.respondRing(id, "decline");
    } catch {
      /* already ended */
    }
  };

  useEffect(() => {
    if (!ring) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") void decline();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (!ring) return null;

  const accept = async () => {
    setBusy(true);
    try {
      const res = await meetingsApi.respondRing(ring.id, "accept");
      removeRing(ring.id);
      location.assign(`${res.joinPath}?call=1`);
    } catch {
      removeRing(ring.id);
    }
  };

  const caller = ring.from.name || "Someone";
  return (
    <div
      data-mt-call=""
      className="fixed top-3.5 left-[50%] z-80 flex items-center gap-3 w-[min(480px,calc(100vw-24px))] py-3 px-3.5 border border-solid border-line rounded-[14px] bg-panel shadow-[0_18px_60px_#0005] transform-[translateX(-50%)] animate-[mt-drop_.25s_ease] [@media(max-width:600px)]:top-2 [@media(max-width:600px)]:gap-2 [@media(max-width:600px)]:p-2.5 motion-reduce:animate-none"
      data-fidelity-state="mt-call"
      role="alertdialog"
      aria-labelledby="mt-call-title"
      aria-describedby="mt-call-sub"
    >
      <span
        data-mt-call-pulse=""
        className="grid place-items-center flex-none w-10 h-10 rounded-[50%] bg-[#16a34a] text-white animate-[mt-pulse_1.4s_ease-out_infinite] motion-reduce:animate-none"
        data-fidelity-state="mt-call-pulse"
        aria-hidden="true"
      >
        <Phone size={18} />
      </span>
      <div
        className="grid flex-1 gap-0.5 min-w-0"
        data-fidelity-state="mt-call-text"
      >
        <b
          className="overflow-hidden text-ellipsis whitespace-nowrap"
          id="mt-call-title"
        >
          {caller} is calling
        </b>
        <span
          className="overflow-hidden text-ellipsis whitespace-nowrap text-muted"
          id="mt-call-sub"
        >
          {ring.meeting.title} · {left}s
        </span>
      </div>
      <button
        type="button"
        className="inline-flex items-center justify-center gap-1.5 flex-none min-h-10 py-0 px-3.5 rounded-[20px] font-semibold cursor-pointer bg-danger text-white [&:hover]:filter-[brightness(1.08)] disabled:opacity-60 disabled:cursor-default [@media(max-width:600px)]:w-11 [@media(max-width:600px)]:p-0 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
        data-fidelity-state="mt-call-btn mt-call-decline"
        onClick={() => void decline()}
        aria-label={`Decline call from ${caller}`}
      >
        <PhoneOff size={18} aria-hidden="true" />
        <span className="[@media(max-width:600px)]:hidden">Decline</span>
      </button>
      <button
        ref={acceptRef}
        type="button"
        className="inline-flex items-center justify-center gap-1.5 flex-none min-h-10 py-0 px-3.5 rounded-[20px] font-semibold cursor-pointer bg-[#16a34a] text-white [&:hover]:filter-[brightness(1.08)] disabled:opacity-60 disabled:cursor-default [@media(max-width:600px)]:w-11 [@media(max-width:600px)]:p-0 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
        data-fidelity-state="mt-call-btn mt-call-accept"
        disabled={busy}
        onClick={() => void accept()}
        aria-label={`Accept call from ${caller}`}
      >
        <Phone size={18} aria-hidden="true" />
        <span className="[@media(max-width:600px)]:hidden">Accept</span>
      </button>
    </div>
  );
}

/** Mount once in the app shell: opens the personal socket, shows toasts and the incoming-call card. */
export function MeetingNotifier() {
  const toasts = useInbox((s) => s.toasts);

  useEffect(() => connectInbox(), []);
  useEffect(() => {
    const onFirstClick = () => askNotificationPermission();
    window.addEventListener("click", onFirstClick, { once: true });
    return () => window.removeEventListener("click", onFirstClick);
  }, []);

  return (
    <>
      <IncomingCall />
      {toasts.length > 0 && (
        <div
          className="fixed right-4 bottom-4 z-55 grid gap-2 w-[min(360px,calc(100vw-32px))] [@media(max-width:600px)]:right-3 [@media(max-width:600px)]:bottom-3"
          data-fidelity-state="mt-toasts"
          aria-live="polite"
        >
          {toasts.map((t) => (
            <ToastCard key={t.id} toast={t} />
          ))}
        </div>
      )}
    </>
  );
}
