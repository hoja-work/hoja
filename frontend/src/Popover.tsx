import {useEffect, useRef, type ReactNode} from 'react';

type PopoverProps = {
  className?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  panelClassName: string;
  panelId: string;
  panelRole?: 'menu' | 'dialog' | 'listbox';
  trigger: (open: boolean, toggle: () => void) => ReactNode;
  children: ReactNode;
};

export function Popover({
  className,
  open,
  onOpenChange,
  panelClassName,
  panelId,
  panelRole,
  trigger,
  children,
}: PopoverProps) {
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) onOpenChange(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onOpenChange(false);
    };

    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open, onOpenChange]);

  return (
    <div className={className} ref={rootRef}>
      {trigger(open, () => onOpenChange(!open))}
      {open && (
        <div className={`popover-panel ${panelClassName}`} id={panelId} role={panelRole}>
          {children}
        </div>
      )}
    </div>
  );
}
