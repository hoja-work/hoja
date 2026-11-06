import {useLayoutEffect, useRef, type ReactNode} from 'react';

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
  const onOpenChangeRef = useRef(onOpenChange);

  useLayoutEffect(() => {
    onOpenChangeRef.current = onOpenChange;
  }, [onOpenChange]);

  useLayoutEffect(() => {
    if (!open) return;

    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) onOpenChangeRef.current(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onOpenChangeRef.current(false);
    };

    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown, true);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown, true);
    };
  }, [open]);

  return (
    <div className={className} ref={rootRef}>
      {trigger(open, () => onOpenChangeRef.current(!open))}
      {open && (
        <div className={`popover-panel ${panelClassName}`} id={panelId} role={panelRole}>
          {children}
        </div>
      )}
    </div>
  );
}
