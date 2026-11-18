// Based on Motion Primitives' accordion (https://motion-primitives.com/docs/accordion).
// Adapted to the existing framer-motion dependency, with accessible panel IDs.
import React, { createContext, useContext, useId, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";

const Context = createContext<{ open: boolean; toggle: () => void; id: string } | null>(null);
function useAccordion() {
  const value = useContext(Context);
  if (!value) throw new Error("Accordion components require Accordion");
  return value;
}
export function Accordion({ children, defaultExpanded = true }: { children: React.ReactNode; defaultExpanded?: boolean }) {
  const [open, setOpen] = useState(defaultExpanded);
  const id = useId();
  return <Context.Provider value={{ open, toggle: () => setOpen((value) => !value), id }}>{children}</Context.Provider>;
}
export function AccordionTrigger({ children }: { children: React.ReactNode }) {
  const { open, toggle, id } = useAccordion();
  return <button type="button" id={`${id}-trigger`} aria-expanded={open} aria-controls={`${id}-content`} onClick={toggle} className="group flex min-w-0 items-center gap-2 text-left focus-visible:outline-2 focus-visible:outline-accent" data-expanded={open ? "" : undefined}>{children}</button>;
}
export function AccordionContent({ children }: { children: React.ReactNode }) {
  const { open, id } = useAccordion();
  const reduced = useReducedMotion();
  return <AnimatePresence initial={false}>{open && <motion.div key="content" id={`${id}-content`} role="region" aria-labelledby={`${id}-trigger`} initial="collapsed" animate="expanded" exit="collapsed" variants={{ expanded: { height: "auto", opacity: 1 }, collapsed: { height: 0, opacity: 0 } }} transition={reduced ? { duration: 0 } : { type: "spring", stiffness: 200, damping: 30 }} className="overflow-hidden">{children}</motion.div>}</AnimatePresence>;
}
