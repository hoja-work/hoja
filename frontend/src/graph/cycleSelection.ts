/* Keyboard behavior for the cycle workbench. Selection is local UI state: this
 * helper deliberately knows nothing about routes, history, or data fetching. */
export function cycleKeyTarget(ids: readonly string[], currentId: string, key: string): string | null {
  if (!ids.length) return null;
  const current = ids.indexOf(currentId);
  let next: number;
  if (key === 'Home') next = 0;
  else if (key === 'End') next = ids.length - 1;
  else if (key === 'ArrowDown' || key === 'ArrowRight') next = Math.min(ids.length - 1, Math.max(0, current + 1));
  else if (key === 'ArrowUp' || key === 'ArrowLeft') next = Math.max(0, current < 0 ? 0 : current - 1);
  else return null;
  return ids[next];
}
