import {useEffect, useState} from 'react';
import type {RefObject} from 'react';

/* useScrollWindow: the visible slice of a long list inside a scroll container. */

/* ---------------- windowing ---------------- */

export type ScrollWindow = {top: number; bottom: number};
const QUANTUM = 200;

/* Clipping ancestors of the anchor. Pages live in different shells, so the nominal scroll box
 * (e.g. .lin-scroll) may have grown to its content and left the scrolling to an outer element. */
function clippingAncestors(anchor: HTMLElement): HTMLElement[] {
  const out: HTMLElement[] = [];
  for (let el = anchor.parentElement; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
    const oy = getComputedStyle(el).overflowY;
    if (oy !== 'visible') out.push(el);
  }
  return out;
}

/** The visible viewport expressed in the anchor's coordinates, quantized so small scrolls don't re-render. */
export function useScrollWindow(scrollRef: RefObject<HTMLElement | null>, anchorRef: RefObject<HTMLElement | null>, enabled: boolean): ScrollWindow {
  const [win, setWin] = useState<ScrollWindow>(() => ({top: 0, bottom: typeof window === 'undefined' ? 1000 : window.innerHeight}));
  useEffect(() => {
    if (!enabled) return;
    const anchor = anchorRef.current;
    if (!anchor) return;
    let frame = 0;
    let clips = clippingAncestors(anchor);
    const measure = () => {
      frame = 0;
      let vpTop = 0, vpBottom = window.innerHeight;
      for (const el of clips) {
        const r = el.getBoundingClientRect();
        vpTop = Math.max(vpTop, r.top); vpBottom = Math.min(vpBottom, r.bottom);
      }
      const anchorTop = anchor.getBoundingClientRect().top;
      const top = Math.floor((vpTop - anchorTop) / QUANTUM) * QUANTUM;
      const bottom = Math.ceil((Math.max(vpTop, vpBottom) - anchorTop) / QUANTUM) * QUANTUM;
      setWin(w => (w.top === top && w.bottom === bottom ? w : {top, bottom}));
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
    const onResize = () => { clips = clippingAncestors(anchor); schedule(); };
    measure();
    // Capture-phase listener sees scrolls of whichever ancestor (or the document) scrolls.
    document.addEventListener('scroll', schedule, {capture: true, passive: true});
    window.addEventListener('resize', onResize);
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(schedule) : null;
    if (scrollRef.current) ro?.observe(scrollRef.current);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('scroll', schedule, {capture: true});
      window.removeEventListener('resize', onResize);
      ro?.disconnect();
    };
  }, [scrollRef, anchorRef, enabled]);
  return win;
}

/** [start, end) of fixed-height rows that intersect the window (plus overscan). */
export function visibleRange(win: ScrollWindow, rowsTop: number, rowH: number, n: number, overscan = 900): [number, number] {
  const start = Math.max(0, Math.min(n, Math.floor((win.top - overscan - rowsTop) / rowH)));
  const end = Math.max(start, Math.min(n, Math.ceil((win.bottom + overscan - rowsTop) / rowH)));
  return [start, end];
}
