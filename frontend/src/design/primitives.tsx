/*
 * Shared primitives. Reviews is the first consumer; Pulse and Settings compose the same
 * components instead of re-implementing the numbers.
 *
 * Every primitive renders a .ds-* class (styling from design.css, values from tokens.css)
 * AND the caller's className verbatim, so a screen keeps its own hook for per-screen
 * overrides without copying a single measured value.
 */
import React from 'react';

const cx = (...parts: Array<string | false | null | undefined>) => parts.filter(Boolean).join(' ');

/** Two-pane split: a capped ledger column and the detail pane that takes the rest. */
export function SplitPane({as: Root='div', className, ledgerClassName, detailClassName, ledger, detail, detailLabel}:{
  /** Element for the outer split. A screen that used a landmark (<section>/<main>) keeps it. */
  as?: 'div'|'section'|'main';
  className?: string; ledgerClassName?: string; detailClassName?: string;
  ledger: React.ReactNode; detail: React.ReactNode; detailLabel?: string;
}) {
  return <Root className={cx('ds-split', className)}>
    <div className={cx('ds-split-ledger', ledgerClassName)}>{ledger}</div>
    <aside className={cx('ds-split-detail', detailClassName)} aria-label={detailLabel}>{detail}</aside>
  </Root>;
}

/** Scroll region that owns its own overflow, so the page never scrolls sideways. */
export function ScrollArea({className, variant='list', role, id, labelledBy, tabIndex, children}:{
  className?: string; variant?: 'list'|'detail'|'diff';
  role?: string; id?: string; labelledBy?: string; tabIndex?: number; children: React.ReactNode;
}) {
  return <div className={cx('ds-scroll', `ds-scroll--${variant}`, className)} role={role} id={id}
    aria-labelledby={labelledBy} tabIndex={tabIndex}>{children}</div>;
}

/** A strip of tabs. The pane row is a real tablist; the ledger pills are just a strip. */
export function TabStrip({className, role, label, children}:{
  className?: string; role?: 'tablist'; label?: string; children: React.ReactNode;
}) {
  return <div className={cx('ds-tabstrip', className)} role={role} aria-label={role ? label : undefined}>{children}</div>;
}

export function Tab(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const {className, children, ...rest} = props;
  return <button type="button" className={cx('ds-tab', className)} {...rest}>{children}</button>;
}

/** A collapsible group band (Ready to merge / Open / Draft, and any grouped list). */
export function GroupBar({as: Heading='h3', className, label, count, caret, controls}:{
  /** Heading level. A feed's era band sits above post titles, so Pulse asks for h2. */
  as?: 'h2'|'h3'|'h4';
  className?: string; label: React.ReactNode; count?: number;
  caret?: React.ReactNode; controls?: string;
}) {
  return <Heading className={cx('ds-group-bar', className)} aria-controls={controls}>
    {label}{count != null ? <b>{count}</b> : null}{caret}
  </Heading>;
}

/** A flat list row: 40px, no border, with a full-height click target passed in. */
export function ListRow({className, main, aside, selected}:{
  className?: string; main: React.ReactNode; aside?: React.ReactNode; selected?: boolean;
}) {
  return <li className={cx('ds-list-row', className)} data-selected={selected ? 'true' : undefined}>
    {main}{aside}
  </li>;
}

/** A property row: fixed label column, value column that takes the remaining width. */
export function PropertyRow({className, label, children}:{
  className?: string; label: React.ReactNode; children: React.ReactNode;
}) {
  return <div className={cx('ds-prop', className)}>
    <dt className="ds-prop-label">{label}</dt>
    <dd className="ds-prop-value">{children}</dd>
  </div>;
}

/** What a pane shows when it has nothing to show — never a fake row. */
export function EmptyState({className, children}:{className?: string; children: React.ReactNode}) {
  return <div className={cx('ds-empty', className)}>{children}</div>;
}

/** A composer: one input, optional leading icon, one send action. */
export function Composer({className, value, placeholder, disabled, icon, sendIcon, sendLabel='Send', onChange, onSend, note}:{
  className?: string; value: string; placeholder?: string; disabled?: boolean;
  icon?: React.ReactNode; sendIcon: React.ReactNode; sendLabel?: string;
  onChange: (next: string) => void; onSend: () => void; note?: React.ReactNode;
}) {
  return <form className={cx('ds-composer', className)} onSubmit={e => {e.preventDefault(); onSend();}}>
    {icon ? <span className="ds-composer-action" aria-hidden="true">{icon}</span> : null}
    <input className="ds-composer-input" value={value} placeholder={placeholder} disabled={disabled}
      onChange={e => onChange(e.target.value)}/>
    <button type="submit" className="ds-composer-action" aria-label={sendLabel} disabled={disabled}>{sendIcon}</button>
    {note}
  </form>;
}
