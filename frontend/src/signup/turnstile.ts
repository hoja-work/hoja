// The Turnstile widget: script loaded exactly once, explicit render, token out.
//
// Cloudflare's implicit mode scans the page on script load, which would race the
// React mount and could leave an invisible widget attached to a node that is
// about to unmount. So the script is always requested with `render=explicit`,
// loaded through one module-level promise (a second mount reuses it rather than
// injecting a second <script>), and the widget is created by `render()` on the
// element this component owns.
export type TurnstileWidgetOptions = {
  sitekey: string;
  action?: string;
  theme?: 'light' | 'dark' | 'auto';
  size?: 'normal' | 'compact' | 'flexible';
  callback?: (token: string) => void;
  'expired-callback'?: () => void;
  'error-callback'?: () => void;
};

export type TurnstileApi = {
  render: (container: HTMLElement, options: TurnstileWidgetOptions) => string | undefined;
  reset: (widgetId?: string) => void;
  remove: (widgetId?: string) => void;
};

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

/** The script the page falls back to when the config endpoint sends nothing. */
export const TURNSTILE_SCRIPT_URL =
  'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

let pending: Promise<TurnstileApi> | null = null;

/**
 * Resolve Cloudflare's API, injecting the script at most once per page load.
 *
 * The promise is cached on success *and* on failure-with-retry: a blocked script
 * (ad blocker, offline) rejects, and the next attempt would otherwise be queued
 * behind a permanently rejected promise. The caller renders the failure as
 * "the challenge could not load", which is the fail-closed answer.
 */
export function loadTurnstile(src: string = TURNSTILE_SCRIPT_URL): Promise<TurnstileApi> {
  if (typeof window === 'undefined' || typeof document === 'undefined') {
    return Promise.reject(new Error('no browser to load Turnstile into'));
  }
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (!pending) {
    const attempt = new Promise<TurnstileApi>((resolve, reject) => {
      const script = document.createElement('script');
      script.src = src;
      script.async = true;
      script.defer = true;
      script.dataset.hojaTurnstile = 'once';
      script.addEventListener('load', () => {
        if (window.turnstile) resolve(window.turnstile);
        else reject(new Error('the Turnstile script loaded without the API'));
      }, {once: true});
      script.addEventListener('error', () => {
        reject(new Error('the Turnstile script could not be loaded'));
      }, {once: true});
      document.head.appendChild(script);
    });
    attempt.catch(() => {
      if (pending === attempt) pending = null;
    });
    pending = attempt;
  }
  return pending;
}
