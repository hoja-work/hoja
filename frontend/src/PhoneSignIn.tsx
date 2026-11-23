/* "Sign in on your phone" (HOJA-733). The dialog shows a QR code for the Hoja app, then asks the
 * person to approve the phone that scanned it. The state machine lives in `phoneSignIn.ts`; this
 * file only renders it. Opened from the workspace menu and from Settings > Connected apps. */
import { useEffect, useRef, useState } from "react";
import { Dialog } from "radix-ui";
import { motion, useReducedMotion } from "framer-motion";
import { QRCodeSVG } from "qrcode.react";
import { CircleCheck, Smartphone, TriangleAlert, X } from "lucide-react";
import {
  PhoneSignInController,
  formatCode,
  shortUserAgent,
  type HandoffDevice,
  type PhoneSignInState,
} from "./phoneSignIn";

const primary =
  "inline-flex min-h-9 items-center justify-center rounded-md bg-accent px-4 py-2 text-sm font-semibold text-panel hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-wait disabled:opacity-60 pointer-coarse:min-h-11";
const secondary =
  "inline-flex min-h-9 items-center justify-center rounded-md border border-line bg-panel px-4 py-2 text-sm text-foreground hover:bg-subtle focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-60 pointer-coarse:min-h-11";

function platformName(platform: string) {
  if (platform === "android") return "Android";
  if (platform === "ios") return "iOS";
  return platform;
}

function announcement(state: PhoneSignInState): string {
  switch (state.kind) {
    case "loading":
      return "Creating a sign-in code.";
    case "pending":
      return `Sign-in code ${formatCode(state.code)} is ready to scan.`;
    case "claimed":
      return (
        state.notice ||
        `${state.device.name} wants to sign in. Approve or deny it.`
      );
    case "approved":
      return "Approved. Waiting for your phone.";
    case "consumed":
      return `Signed in on ${state.device?.name || "your phone"}.`;
    case "burned":
      return "Another device tried to use this code. Nothing was signed in.";
    case "expired":
      return "The code expired.";
    case "error":
      return state.message;
    default:
      return "";
  }
}

/** A thin bar under the QR code that drains until the code is replaced. */
function RotationBar({ seconds }: { seconds: number }) {
  const [drained, setDrained] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setDrained(true));
    return () => cancelAnimationFrame(frame);
  }, []);
  return (
    <div
      className="h-1 w-full overflow-hidden rounded-full bg-subtle motion-reduce:hidden"
      aria-hidden="true"
      data-phone-signin-countdown=""
    >
      <div
        className="h-full w-full origin-left rounded-full bg-accent transition-transform ease-linear motion-reduce:transition-none"
        style={{
          transform: drained ? "scaleX(0)" : "scaleX(1)",
          transitionDuration: drained ? `${seconds}s` : "0s",
        }}
      />
    </div>
  );
}

function Device({ device }: { device: HandoffDevice }) {
  const ua = shortUserAgent(device.userAgent);
  const details = [device.ip, ua].filter(Boolean).join(" · ");
  return (
    <div className="flex flex-col items-center gap-2 text-center">
      <Smartphone size={32} aria-hidden="true" className="text-muted" />
      <p className="text-base">
        Sign in <b className="font-semibold">{device.name}</b>
        {device.platform ? ` (${platformName(device.platform)})` : ""}?
      </p>
      {details && <p className="text-sm text-muted">{details}</p>}
    </div>
  );
}

function Body({
  state,
  controller,
}: {
  state: PhoneSignInState;
  controller: PhoneSignInController;
}) {
  const reduceMotion = useReducedMotion();
  const newCode = (
    <button
      type="button"
      className={primary}
      onClick={() => void controller.retry()}
    >
      Show a new code
    </button>
  );
  switch (state.kind) {
    case "loading":
      return (
        <p className="py-16 text-center text-sm text-muted">Creating a code…</p>
      );
    case "pending":
      return (
        <div className="flex flex-col items-center gap-4">
          <div className="flex w-full max-w-60 flex-col gap-2">
            <div className="rounded-lg bg-white p-4" data-phone-signin-qr="">
              <QRCodeSVG
                key={state.id}
                value={state.qrUrl}
                size={512}
                level="M"
                bgColor="#ffffff"
                fgColor="#000000"
                title="Sign-in QR code for the Hoja app"
                className="block h-auto w-full"
              />
            </div>
            <RotationBar key={state.id} seconds={state.rotateAfter} />
          </div>
          <p className="font-mono text-lg tracking-widest">
            {formatCode(state.code)}
          </p>
          <p className="text-center text-sm text-muted">
            Open the Hoja app on your phone and scan this code.
          </p>
        </div>
      );
    case "claimed":
      return (
        <div className="flex flex-col items-center gap-4 py-4">
          <Device device={state.device} />
          {state.notice && (
            <p role="alert" className="text-center text-sm text-danger">
              {state.notice}
            </p>
          )}
          <div className="flex flex-wrap justify-center gap-2">
            <button
              type="button"
              className={secondary}
              disabled={state.busy}
              onClick={() => void controller.deny()}
            >
              Deny
            </button>
            <button
              type="button"
              className={primary}
              disabled={state.busy}
              onClick={() => void controller.approve()}
            >
              {state.busy ? "Working…" : "Approve"}
            </button>
          </div>
        </div>
      );
    case "approved":
      return (
        <p className="py-16 text-center text-sm text-muted">
          Waiting for your phone…
        </p>
      );
    case "consumed":
      return (
        <div className="flex flex-col items-center gap-2 py-12">
          <motion.span
            initial={reduceMotion ? false : { scale: 0.5, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ type: "spring", stiffness: 400, damping: 22 }}
          >
            <CircleCheck size={48} aria-hidden="true" className="text-accent" />
          </motion.span>
          <p className="text-base">
            Signed in on {state.device?.name || "your phone"}
          </p>
        </div>
      );
    case "burned":
      return (
        <div className="flex flex-col items-center gap-4 py-8 text-center">
          <TriangleAlert size={32} aria-hidden="true" className="text-danger" />
          <p className="text-base">
            Another device tried to use this code. Nothing was signed in.
          </p>
          {newCode}
        </div>
      );
    case "expired":
      return (
        <div className="flex flex-col items-center gap-4 py-8 text-center">
          <p className="text-base">Code expired</p>
          {newCode}
        </div>
      );
    case "error":
      return (
        <div className="flex flex-col items-center gap-4 py-8 text-center">
          <p role="alert" className="text-sm">
            {state.message}
          </p>
          {state.reason !== "browser_session_required" &&
            state.reason !== "signin_method_disabled" && (
              <button
                type="button"
                className={primary}
                onClick={() => void controller.retry()}
              >
                Try again
              </button>
            )}
        </div>
      );
    default:
      return null;
  }
}

function PhoneSignInContent({ onClose }: { onClose: () => void }) {
  const [controller, setController] = useState<PhoneSignInController | null>(
    null,
  );
  const [state, setState] = useState<PhoneSignInState>({ kind: "loading" });
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);
  // The controller is created inside the effect so a StrictMode remount gets a fresh one;
  // unmounting closes it, which cancels a pending or claimed code.
  useEffect(() => {
    const c = new PhoneSignInController();
    setController(c);
    const off = c.subscribe(() => setState(c.state));
    void c.start();
    return () => {
      off();
      c.close();
    };
  }, []);
  useEffect(() => {
    if (state.kind === "closed") onCloseRef.current();
  }, [state.kind]);
  return (
    <>
      <div className="flex items-start justify-between gap-2">
        <div className="flex flex-col gap-1">
          <Dialog.Title className="text-base font-semibold">
            Sign in on your phone
          </Dialog.Title>
          <Dialog.Description className="text-sm text-muted">
            Your phone signs in to this workspace as you.
          </Dialog.Description>
        </div>
        <button
          type="button"
          aria-label="Close"
          onClick={() => onCloseRef.current()}
          className="grid size-8 flex-none place-items-center rounded-md text-muted hover:bg-subtle hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent pointer-coarse:size-11"
        >
          <X size={16} aria-hidden="true" />
        </button>
      </div>
      <p className="sr-only" aria-live="polite" aria-atomic="true">
        {announcement(state)}
      </p>
      {controller && <Body state={state} controller={controller} />}
    </>
  );
}

/** The "Sign in on your phone" dialog. Rendering it with `open` starts a new code. */
export function PhoneSignInDialog({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-100 bg-black/40" />
        {/* The content fills the viewport and scrolls, so a short window never clips the card;
            a press on the backdrop around the card closes it, like the overlay would. */}
        <Dialog.Content
          className="fixed inset-0 z-101 flex items-start justify-center overflow-y-auto overscroll-contain p-4 pt-16 outline-none shell-phone:pt-4"
          onPointerDown={(event) => {
            if (event.target === event.currentTarget) onClose();
          }}
        >
          <div
            className="flex w-full max-w-md flex-col gap-4 rounded-xl border border-line bg-panel p-4 text-foreground shadow-xl"
            data-phone-signin-dialog=""
          >
            {open && <PhoneSignInContent onClose={onClose} />}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
