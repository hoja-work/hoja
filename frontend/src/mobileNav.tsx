import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { PanelLeft } from "lucide-react";

type MobileNavContextValue = {
  open: boolean;
  setOpen: React.Dispatch<React.SetStateAction<boolean>>;
  toggleRef: React.RefObject<HTMLButtonElement | null>;
  isSettings: boolean;
  hasInlineTrigger: boolean;
  registerInlineTrigger: () => () => void;
};

const MobileNavContext = createContext<MobileNavContextValue | null>(null);

export function MobileNavProvider({
  open,
  setOpen,
  toggleRef,
  isSettings,
  children,
}: {
  open: boolean;
  setOpen: React.Dispatch<React.SetStateAction<boolean>>;
  toggleRef: React.RefObject<HTMLButtonElement | null>;
  isSettings: boolean;
  children: React.ReactNode;
}) {
  const [inlineCount, setInlineCount] = useState(0);
  const registerInlineTrigger = useCallback(() => {
    setInlineCount((n) => n + 1);
    return () => setInlineCount((n) => Math.max(0, n - 1));
  }, []);

  const value = useMemo(
    () => ({
      open,
      setOpen,
      toggleRef,
      isSettings,
      hasInlineTrigger: inlineCount > 0,
      registerInlineTrigger,
    }),
    [open, setOpen, toggleRef, isSettings, inlineCount, registerInlineTrigger],
  );

  return (
    <MobileNavContext.Provider value={value}>
      {children}
    </MobileNavContext.Provider>
  );
}

export function useMobileNav() {
  return useContext(MobileNavContext);
}

/**
 * Inline mobile/tablet sidebar trigger matching Linear's header-embedded button.
 * Renders a 44x44 touch target on <=880px / landscape phones with a crisp 28x28
 * visual pill inside so it aligns flush with page titles without adding a topbar.
 */
export function MobileNavTrigger({
  className = "",
  fallback = false,
}: {
  className?: string;
  /** True only for the shell's automatic fallback bar so it does not count as an inline trigger. */
  fallback?: boolean;
}) {
  const ctx = useMobileNav();
  const register = ctx?.registerInlineTrigger;

  useEffect(() => {
    if (fallback || !register) return;
    return register();
  }, [fallback, register]);

  if (!ctx) return null;

  return (
    <button
      ref={ctx.toggleRef}
      type="button"
      data-mobile-nav-toggle=""
      aria-label={
        ctx.isSettings ? "Open settings navigation" : "Open navigation"
      }
      aria-expanded={ctx.open}
      aria-controls={ctx.isSettings ? "settings-navigation" : "workspace-sidebar"}
      onClick={() => ctx.setOpen((v) => !v)}
      className={`group -ml-2 grid size-11 flex-none place-items-center rounded-lg bg-transparent text-muted focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 shell-desktop:hidden ${className}`.trim()}
    >
      <span className="grid size-7 place-items-center rounded-md transition-colors duration-120 ease-out group-hover:bg-sidebar-hover group-hover:text-foreground group-hover:transition-none">
        <PanelLeft size={16} aria-hidden="true" />
      </span>
    </button>
  );
}
