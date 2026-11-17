// In-app replacements for window.confirm and window.prompt. Native dialogs are unstyled, block the whole page, and
// behave differently across phone browsers, so the app asks through these instead (enforced by local/no-native-dialog).
//   const ok = await confirmDialog('Archive this issue?', {danger: true});
//   const name = await promptDialog('Name this view:');   // string, or null if cancelled
//   await alertDialog('Could not save.');                // resolves when dismissed
import { useEffect, useState, type FormEvent } from "react";

type Request = {
  message: string;
  danger: boolean;
  input: boolean;
  alert: boolean;
  value: string;
  resolve: (v: string | boolean | null) => void;
};

let current: Request | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function ask(
  message: string,
  opts: { danger?: boolean; input?: boolean; alert?: boolean; value?: string },
): Promise<string | boolean | null> {
  return new Promise((resolve) => {
    // A second request while one is open cancels the first, so no caller waits forever.
    current?.resolve(current.input ? null : false);
    current = {
      message,
      danger: !!opts.danger,
      input: !!opts.input,
      alert: !!opts.alert,
      value: opts.value ?? "",
      resolve,
    };
    emit();
  });
}

export function confirmDialog(
  message: string,
  opts: { danger?: boolean } = {},
): Promise<boolean> {
  return ask(message, opts).then((v) => v === true);
}

export function alertDialog(message: string): Promise<void> {
  return ask(message, { alert: true }).then(() => undefined);
}

export function promptDialog(
  message: string,
  value = "",
): Promise<string | null> {
  return ask(message, { input: true, value }).then((v) =>
    typeof v === "string" ? v : null,
  );
}

// Mounted once at the app root.
export function DialogHost() {
  const [, bump] = useState(0);
  const [value, setValue] = useState("");
  useEffect(() => {
    const l = () => {
      setValue(current?.value ?? "");
      bump((n) => n + 1);
    };
    listeners.add(l);
    return () => {
      listeners.delete(l);
    };
  }, []);
  const req = current;
  if (!req) return null;
  const close = (result: string | boolean | null) => {
    current = null;
    req.resolve(result);
    emit();
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = value.trim();
    if (trimmed) close(trimmed);
  };
  return (
    <div
      className="fixed inset-0 flex items-start justify-center p-4 z-10 bg-[#0008]"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close(req.input ? null : false);
      }}
    >
      <form
        className="mt-16 max-w-xl border border-line text-foreground absolute top-[12%] left-[50%] transform-[translateX(-50%)] w-[min(570px,calc(100%-32px))] pt-6.25 pr-6.25 pb-6.25 pl-6.25 border-solid rounded-[10px] bg-panel shadow-(--shadow)"
        role="dialog"
        aria-modal="true"
        aria-label={req.message}
        onSubmit={submit}
      >
        <p className="mt-0 mr-0 mb-3 ml-0 font-[550]">{req.message}</p>
        {req.input && (
          <input
            ref={(el) => el?.focus()}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-label={req.message}
            className="border border-line shell-phone:min-h-11 pointer-coarse:min-h-11 w-full border-solid rounded-md bg-bg text-foreground pt-2.25 pr-2.5 pb-2.25 pl-2.5 resize-y mb-3"
          />
        )}
        <div className="flex justify-end gap-2">
          {req.alert ? (
            <button
              type="button"
              className="inline-flex items-center justify-center hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-1.75 rounded-none bg-accent text-white pt-2.25 pr-3.5 pb-2.25 pl-3.5 font-[650] shadow-none hover:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] hover:not-disabled:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] hover:not-disabled:filter-none active:not-disabled:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 [html[data-theme=dark]_&]:text-[#171719]"
              onClick={() => close(true)}
            >
              OK
            </button>
          ) : (
            <>
              <button
                type="button"
                onClick={() => close(req.input ? null : false)}
                className="shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              >
                Cancel
              </button>
              {req.input ? (
                <button
                  type="submit"
                  className="inline-flex items-center justify-center hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60 shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11 gap-1.75 rounded-none bg-accent text-white pt-2.25 pr-3.5 pb-2.25 pl-3.5 font-[650] shadow-none hover:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] hover:not-disabled:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] hover:not-disabled:filter-none active:not-disabled:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 [html[data-theme=dark]_&]:text-[#171719]"
                  disabled={!value.trim()}
                >
                  Create
                </button>
              ) : (
                <button
                  type="button"
                  className={
                    `inline-flex items-center justify-center hover:brightness-110 ${req.danger ? "text-white" : ""}` +
                    "shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11" +
                    " inline-flex items-center justify-center gap-1.75 rounded-none bg-accent text-white pt-2.25 pr-3.5 pb-2.25 pl-3.5 font-[650] shadow-none hover:filter-[brightness(1.08)] min-h-11 [transition:background-color_.15s_ease,color_.15s_ease,transform_.15s_ease] tracking-[.01em] hover:not-disabled:bg-[color-mix(in_srgb,var(--accent)_84%,var(--text))] hover:not-disabled:filter-none active:not-disabled:transform-[translateY(1px)] disabled:cursor-wait disabled:opacity-65 [html[data-theme=dark]_&]:text-[#171719]"
                  }
                  onClick={() => close(true)}
                >
                  Confirm
                </button>
              )}
            </>
          )}
        </div>
      </form>
    </div>
  );
}
