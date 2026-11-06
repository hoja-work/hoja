import React, {useEffect, useId, useLayoutEffect, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {Check, ChevronDown} from 'lucide-react';

export type SettingsOption = {
  value: string;
  label: string;
  description?: string;
  meta?: string;
  disabled?: boolean;
};

type Props = {
  label: string;
  value: string;
  options: SettingsOption[];
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
};

type Placement = {top: number; left: number; width: number; maxHeight: number};

/** A select-only combobox with contextual option copy, viewport-aware placement,
 * aria-activedescendant navigation, typeahead, and no focus theft from its trigger. */
export function SettingsSelect({label, value, options, onChange, placeholder = 'Choose an option…', disabled, className = ''}: Props) {
  const uid = useId().replace(/:/g, '');
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState('');
  const [placement, setPlacement] = useState<Placement>({top: 0, left: 0, width: 320, maxHeight: 360});
  const trigger = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const typeahead = useRef('');
  const typeaheadTimer = useRef<number | undefined>(undefined);
  const enabled = options.filter(o => !o.disabled);
  const selected = options.find(o => o.value === value);
  const listId = `${uid}-listbox`;
  const optionId = React.useCallback((option: string) => `${uid}-option-${option.replace(/[^a-z0-9_-]/gi, '-')}`, [uid]);

  const place = () => {
    const rect = trigger.current?.getBoundingClientRect();
    if (!rect) return;
    const gutter = window.innerWidth <= 700 ? 12 : 8;
    const width = window.innerWidth <= 700 ? window.innerWidth - gutter * 2 : Math.min(420, Math.max(300, rect.width));
    const left = window.innerWidth <= 700 ? gutter : Math.max(gutter, Math.min(rect.left, window.innerWidth - width - gutter));
    const below = window.innerHeight - rect.bottom - 16;
    const above = rect.top - 16;
    const showBelow = below >= 220 || below >= above;
    const maxHeight = Math.max(160, Math.min(390, showBelow ? below : above));
    const panelHeight = Math.min(maxHeight, panel.current?.scrollHeight || maxHeight);
    const top = showBelow ? rect.bottom + 8 : Math.max(gutter, rect.top - panelHeight - 8);
    setPlacement({top, left, width, maxHeight});
  };

  useLayoutEffect(() => {
    if (!open) return;
    place();
    const onResize = () => place();
    const onPointer = (event: PointerEvent) => {
      if (!trigger.current?.contains(event.target as Node) && !panel.current?.contains(event.target as Node)) setOpen(false);
    };
    const onEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      trigger.current?.focus();
    };
    window.addEventListener('resize', onResize);
    window.addEventListener('scroll', onResize, true);
    document.addEventListener('pointerdown', onPointer);
    document.addEventListener('keydown', onEscape, true);
    return () => {
      window.removeEventListener('resize', onResize);
      window.removeEventListener('scroll', onResize, true);
      document.removeEventListener('pointerdown', onPointer);
      document.removeEventListener('keydown', onEscape, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open || !active) return;
    document.getElementById(optionId(active))?.scrollIntoView({block: 'nearest'});
  }, [active, open, optionId]);

  useEffect(() => () => window.clearTimeout(typeaheadTimer.current), []);

  const show = (preferred?: string) => {
    if (disabled || !enabled.length) return;
    const next = preferred || selected?.value || enabled[0].value;
    setActive(next);
    setOpen(true);
  };
  const move = (direction: 1 | -1) => {
    const index = Math.max(0, enabled.findIndex(o => o.value === active));
    setActive(enabled[(index + direction + enabled.length) % enabled.length].value);
  };
  const choose = (option: string) => {
    if (!enabled.some(o => o.value === option)) return;
    onChange(option);
    setOpen(false);
    trigger.current?.focus();
  };
  const findMatch = (character: string) => {
    window.clearTimeout(typeaheadTimer.current);
    typeahead.current += character.toLowerCase();
    const query = typeahead.current;
    const match = enabled.find(o => `${o.label} ${o.description || ''}`.toLowerCase().startsWith(query))
      || enabled.find(o => o.label.toLowerCase().startsWith(character.toLowerCase()));
    if (match) setActive(match.value);
    typeaheadTimer.current = window.setTimeout(() => { typeahead.current = ''; }, 600);
  };
  const onKeyDown = (event: React.KeyboardEvent) => {
    if (disabled) return;
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault();
      if (!open) show(event.key === 'Home' ? enabled[0]?.value : event.key === 'End' ? enabled[enabled.length - 1]?.value : undefined);
      else if (event.key === 'Home') setActive(enabled[0].value);
      else if (event.key === 'End') setActive(enabled[enabled.length - 1].value);
      else move(event.key === 'ArrowDown' ? 1 : -1);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      if (open && active) choose(active); else show();
    } else if (event.key === 'Escape' && open) {
      event.preventDefault();
      setOpen(false);
    } else if (event.key === 'Tab') {
      if (open) setOpen(false);
    } else if (event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      if (!open) show();
      findMatch(event.key);
    }
  };

  return <div className={`settings-select ${className}`}>
    <div
      ref={trigger}
      className={`settings-select-trigger ${open ? 'open' : ''}`}
      role="combobox"
      aria-label={label}
      aria-expanded={open}
      aria-controls={open ? listId : undefined}
      aria-haspopup="listbox"
      aria-activedescendant={open && active ? optionId(active) : undefined}
      aria-disabled={disabled || undefined}
      tabIndex={disabled ? -1 : 0}
      onKeyDown={onKeyDown}
      onClick={() => open ? setOpen(false) : show()}
    >
      <span className="settings-select-value">
        <b>{selected?.label || placeholder}</b>
        {selected?.meta && <small>{selected.meta}</small>}
      </span>
      <ChevronDown size={16} aria-hidden="true"/>
    </div>
    {open && createPortal(<div
      ref={panel}
      id={listId}
      className="settings-select-panel"
      role="listbox"
      aria-label={label}
      style={{top: placement.top, left: placement.left, width: placement.width, maxHeight: placement.maxHeight}}
    >
      <div className="settings-select-menu-label">{label}</div>
      {options.map(option => <div
        id={optionId(option.value)}
        key={option.value}
        className={`settings-select-option ${active === option.value ? 'active' : ''} ${value === option.value ? 'selected' : ''}`}
        role="option"
        aria-selected={value === option.value}
        aria-disabled={option.disabled || undefined}
        data-active={active === option.value || undefined}
        data-value={option.value}
        onPointerMove={() => !option.disabled && setActive(option.value)}
        onClick={() => !option.disabled && choose(option.value)}
        onKeyDown={event => { if ((event.key === 'Enter' || event.key === ' ') && !option.disabled) choose(option.value); }}
        tabIndex={-1}
      >
        <span className="settings-select-option-copy"><b>{option.label}</b>{option.description && <small>{option.description}</small>}</span>
        <span className="settings-select-option-meta">{option.meta}{value === option.value && <Check size={15}/>}</span>
      </div>)}
      <div className="settings-select-menu-hint">↑↓ to explore · Enter to select · Esc to close</div>
    </div>, document.body)}
  </div>;
}
