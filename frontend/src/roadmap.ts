// Roadmap ordering rule, extracted so it can be tested without a browser (HOJA-322).
// The Roadmap tab presents initiatives as a sequential timeline: earliest target
// date first, entries without a target date last, creation date breaking ties.
export interface RoadmapEntry {
  targetDate?: string;
  createdAt?: string;
}

export function orderRoadmap<T extends RoadmapEntry>(rows: T[]): T[] {
  const undated = '9999';
  return [...rows].sort((a, b) =>
    (a.targetDate || undated).localeCompare(b.targetDate || undated) ||
    (a.createdAt || '').localeCompare(b.createdAt || ''));
}
