import React, {useEffect, useRef, useState} from 'react';
import type {IssueLabel} from '../main';

/* Shared Linear-style primitives for the 1:1 Issues/Cycles rebuild. */

export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ');
}

export function initials(name?: string | null): string {
  if (!name) return '?';
  const words = name.trim().split(/\s+/);
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

/** Deterministic pastel avatar background from a name. */
export function avatarColor(name?: string | null): string {
  const palette = ['#e5a8c9', '#c9a8e5', '#a8c9e5', '#a8e5c9', '#e5d3a8', '#e5a8a8', '#a8d3e5', '#d3a8e5'];
  let h = 0;
  for (const c of (name || '?')) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return palette[h % palette.length];
}

export function Avatar({name, size = 20, title}: {name?: string | null; size?: number; title?: string}) {
  return (
    <span
      className="lin-avatar"
      title={title || name || 'Unassigned'}
      style={{width: size, height: size, fontSize: Math.max(8, size * 0.42), background: name ? avatarColor(name) : 'var(--subtle)', color: name ? '#5b4a52' : 'var(--muted)'}}
    >
      {name ? initials(name) : '?'}
    </span>
  );
}

/** Linear-style priority icon. 0 none, 1 urgent, 2 high, 3 medium, 4 low. */
export function PriorityIcon({priority, size = 14}: {priority?: number | null; size?: number}) {
  const p = priority || 0;
  if (p === 1) {
    return (
      <svg width={size} height={size} viewBox="0 0 16 16" aria-label="Urgent" className="lin-priority">
        <rect x="1.5" y="1.5" width="13" height="13" rx="3.5" fill="#f2994a" />
        <path d="M8 4.2v4.4" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" />
        <circle cx="8" cy="11.4" r="1.05" fill="#fff" />
      </svg>
    );
  }
  const color = p === 2 ? '#f2994a' : p === 3 ? '#e5b800' : p === 4 ? '#8a94a6' : '#c4c4c9';
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" aria-label={['No priority', 'Urgent', 'High', 'Medium', 'Low'][p] || 'No priority'} className="lin-priority">
      <rect x="2" y="9.5" width="2.6" height="4.5" rx="1" fill={color} opacity={p === 0 ? 0.45 : 1} />
      <rect x="6.7" y="6" width="2.6" height="8" rx="1" fill={color} opacity={p === 0 ? 0.45 : p >= 3 ? 1 : 0.35} />
      <rect x="11.4" y="2.5" width="2.6" height="11.5" rx="1" fill={color} opacity={p === 0 ? 0.45 : p >= 2 && p !== 3 ? 1 : p === 3 ? 0.35 : 0.35} />
    </svg>
  );
}

/** Linear-style workflow state icon (14px). */
export function LinearStatusIcon({state, size = 14}: {state?: {name?: string; type?: string; color?: string} | null; size?: number}) {
  const t = (state?.type || '').toLowerCase();
  const n = (state?.name || '').toLowerCase();
  const c = state?.color || '#8a94a6';
  const common = {width: size, height: size, viewBox: '0 0 16 16', className: 'lin-status-icon', 'aria-hidden': true} as const;
  if (n.includes('duplicate')) return (<svg {...common}><circle cx="8" cy="8" r="6" fill="none" stroke={c} strokeWidth="1.5" /><path d="M4.5 11.5l7-7" stroke={c} strokeWidth="1.5" /></svg>);
  if (t === 'completed') return (<svg {...common}><circle cx="8" cy="8" r="6.5" fill="#5e6ad2" /><path d="M5 8.3l2.1 2.1L11.2 6" stroke="#fff" strokeWidth="1.7" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>);
  if (t === 'canceled') return (<svg {...common}><circle cx="8" cy="8" r="6.5" fill="#8a94a6" opacity="0.55" /><path d="M6 6l4 4M10 6l-4 4" stroke="#fff" strokeWidth="1.6" strokeLinecap="round" /></svg>);
  if (t === 'backlog') return (<svg {...common}><circle cx="8" cy="8" r="6" fill="none" stroke="#8a94a6" strokeWidth="1.5" strokeDasharray="2.4 2.2" /></svg>);
  if (n.includes('review')) return (<svg {...common}><circle cx="8" cy="8" r="6" fill="none" stroke={c} strokeWidth="1.5" /><path d="M8 8V2A6 6 0 0 1 8 14Z" fill={c} /></svg>);
  if (t === 'started') return (<svg {...common}><circle cx="8" cy="8" r="6" fill="none" stroke="#e5b800" strokeWidth="1.5" /><path d="M8 2a6 6 0 0 1 0 12Z" fill="#e5b800" /></svg>);
  if (t === 'unstarted') return (<svg {...common}><circle cx="8" cy="8" r="6" fill="none" stroke="#8a94a6" strokeWidth="1.5" /></svg>);
  return (<svg {...common}><circle cx="8" cy="8" r="6" fill="none" stroke={c} strokeWidth="1.5" /></svg>);
}

/** Cycle icon used in chips and the cycles timeline. */
export function CycleGlyph({size = 13, tone = 'currentColor'}: {size?: number; tone?: string}) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke={tone} strokeWidth="1.6" aria-hidden="true" className="lin-cycle-glyph">
      <circle cx="8" cy="8" r="5.6" strokeDasharray="3 2.4" />
      <path d="M8 4.6V8l2.4 1.6" strokeLinecap="round" />
    </svg>
  );
}

export function LabelChip({label, onClick, onRemove}: {label: IssueLabel; onClick?: (e: React.MouseEvent) => void; onRemove?: () => void}) {
  return (
    <span className="lin-chip lin-label-chip" onClick={onClick} role={onClick ? 'button' : undefined} tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? e => { if (e.key === 'Enter') onClick(e as unknown as React.MouseEvent); } : undefined}>
      <i style={{background: label.color || '#8a94a6'}} />
      {label.name}
      {onRemove && <button type="button" className="lin-chip-x" aria-label={`Remove label ${label.name}`} onClick={e => { e.stopPropagation(); onRemove(); }}>×</button>}
    </span>
  );
}

export function ProjectChip({name}: {name?: string | null}) {
  if (!name) return null;
  return (
    <span className="lin-chip lin-project-chip" title={`Project: ${name}`}>
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="M8 1.8l5.6 3.2v6.4L8 14.6l-5.6-3.2V5L8 1.8Z" strokeLinejoin="round" /><path d="M8 1.8V8m0 0l5.6-3M8 8L2.4 5" /></svg>
      {name}
    </span>
  );
}

export function CycleChip({number, name}: {number?: number | null; name?: string | null}) {
  if (number == null && !name) return null;
  return (
    <span className="lin-chip lin-cycle-chip" title={name || `Cycle ${number}`}>
      <CycleGlyph size={12} />
      {number ?? name}
    </span>
  );
}

/** "2/4" sub-issue progress pill. */
export function SubIssuePill({done, total}: {done: number; total: number}) {
  if (!total) return null;
  const frac = done / total;
  const r = 5, circ = 2 * Math.PI * r;
  return (
    <span className="lin-subissue-pill" title={`${done} of ${total} sub-issues complete`}>
      <svg width="13" height="13" viewBox="0 0 14 14" aria-hidden="true">
        <circle cx="7" cy="7" r={r} fill="none" stroke="#d4d4d8" strokeWidth="1.6" />
        <circle cx="7" cy="7" r={r} fill="none" stroke="#5e6ad2" strokeWidth="1.6"
          strokeDasharray={`${circ * frac} ${circ}`} strokeLinecap="round" transform="rotate(-90 7 7)" />
      </svg>
      {done}/{total}
    </span>
  );
}

/** Due-date chip; red when overdue. */
export function DueChip({iso}: {iso?: string | null}) {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  const overdue = d.getTime() < Date.now();
  const label = d.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
  return (
    <span className={cx('lin-chip lin-due-chip', overdue && 'overdue')} title={`Due ${d.toLocaleDateString()}`}>
      <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><rect x="2" y="3" width="12" height="11" rx="2" /><path d="M2 6.5h12M5.5 1.5V4M10.5 1.5V4" strokeLinecap="round" /></svg>
      {label}
    </span>
  );
}

export function fmtDate(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString(undefined, {month: 'short', day: 'numeric'});
}

export function fmtFullDate(iso?: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined, {month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit'});
}

export function fmtRange(start?: string | null, end?: string | null): string {
  const a = fmtDate(start), b = fmtDate(end);
  if (a && b) return `${a} → ${b}`;
  return a || b || '';
}

/** Outside-click + Escape dismissal for popovers/menus. */
export function useDismiss(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('pointerdown', onDown); document.removeEventListener('keydown', onKey); };
  }, [onClose]);
  return ref;
}

export type MenuItem = {
  key: string; label: React.ReactNode; icon?: React.ReactNode; shortcut?: string;
  danger?: boolean; disabled?: boolean; divider?: never; onSelect?: () => void;
} | {key: string; divider: true; label?: never};

/** Linear-style dropdown menu. */
export function LinMenu({items, onClose, className}: {items: MenuItem[]; onClose: () => void; className?: string}) {
  const ref = useDismiss(onClose);
  return (
    <div ref={ref} className={cx('lin-menu', className)} role="menu">
      {items.map(it => it.divider
        ? <div key={it.key} className="lin-menu-divider" role="separator" />
        : (
          <button key={it.key} type="button" role="menuitem" className={cx('lin-menu-item', it.danger && 'danger')}
            disabled={it.disabled} onClick={() => { onClose(); it.onSelect?.(); }}>
            {it.icon && <span className="lin-menu-icon">{it.icon}</span>}
            <span className="lin-menu-label">{it.label}</span>
            {it.shortcut && <kbd className="lin-kbd">{it.shortcut}</kbd>}
          </button>
        ))}
    </div>
  );
}

/** CSS-hover tooltip. */
export function Tip({label, children, className}: {label: string; children: React.ReactNode; className?: string}) {
  return (
    <span className={cx('lin-tip', className)} data-tip={label}>
      {children}
    </span>
  );
}

/* ---------- favorites (browser-local, ALT+F) ---------- */
const FAV_KEY = 'hoja_favorites_v1';
type FavStore = {issues: string[]; cycles: string[]};
function readFavs(): FavStore {
  try {
    const raw = localStorage.getItem(FAV_KEY);
    if (raw) { const p = JSON.parse(raw); return {issues: p.issues || [], cycles: p.cycles || []}; }
  } catch { /* ignore */ }
  return {issues: [], cycles: []};
}
export function useFavorite(kind: 'issues' | 'cycles', id: string): [boolean, () => void] {
  const [favs, setFavs] = useState<FavStore>(readFavs);
  const toggle = () => {
    setFavs(prev => {
      const next = {...prev, [kind]: prev[kind].includes(id) ? prev[kind].filter(x => x !== id) : [...prev[kind], id]};
      try { localStorage.setItem(FAV_KEY, JSON.stringify(next)); } catch { /* ignore */ }
      return next;
    });
  };
  return [favs[kind].includes(id), toggle];
}

/** Star button with ALT+F shortcut. */
export function FavStar({kind, id, label}: {kind: 'issues' | 'cycles'; id: string; label: string}) {
  const [fav, toggle] = useFavorite(kind, id);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.altKey && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); toggle(); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [toggle]);
  return (
    <Tip label={fav ? 'Remove from favorites' : 'Add to favorites (Alt+F)'}>
      <button type="button" className={cx('lin-icon-btn', fav && 'active')} aria-label={`${fav ? 'Remove from favorites: ' : 'Add to favorites: '}${label}`}
        aria-pressed={fav} onClick={toggle}>
        <svg width="15" height="15" viewBox="0 0 16 16" fill={fav ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
          <path d="M8 1.8l2 4.1 4.5.6-3.3 3.1.8 4.5L8 11.9l-4 2.2.8-4.5L1.5 6.5l4.5-.6z" strokeLinejoin="round" />
        </svg>
      </button>
    </Tip>
  );
}

/** Icon button matching Linear's toolbar buttons. */
export function IconBtn({label, title, onClick, active, children, disabled}: {
  label: string; title?: string; onClick?: (e: React.MouseEvent) => void; active?: boolean;
  children: React.ReactNode; disabled?: boolean;
}) {
  return (
    <Tip label={title || label}>
      <button type="button" className={cx('lin-icon-btn', active && 'active')} aria-label={label}
        title={title} onClick={onClick} disabled={disabled}>
        {children}
      </button>
    </Tip>
  );
}

/** Small progress ring (0..1) used in cycle stat rows. */
export function Ring({frac, size = 16}: {frac: number; size?: number}) {
  const r = (size - 3) / 2, circ = 2 * Math.PI * r;
  const f = Math.min(1, Math.max(0, frac));
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true" className="lin-ring">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--line)" strokeWidth="2.4" />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke={f >= 1 ? '#5e6ad2' : '#f2994a'} strokeWidth="2.4"
        strokeDasharray={`${circ * f} ${circ}`} strokeLinecap="round" transform={`rotate(-90 ${size / 2} ${size / 2})`} />
    </svg>
  );
}
