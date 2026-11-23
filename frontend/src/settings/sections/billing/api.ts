import { gql } from "../../ui";

export type Grouping = "agent" | "model" | "month";

export type UsageRow = {
  key: string;
  label: string;
  agentUserId?: string | null;
  model?: string | null;
  month: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  credits: number;
  runMinutes: number;
  runs: number;
  costUsd: string;
  computeCostUsd: string;
  limitCredits?: number | null;
  budgetAction?: string | null;
};

export type UsageSummary = {
  month: string;
  groupBy: string;
  credits: number;
  runMinutes: number;
  costUsd: string;
  computeCostUsd: string;
  totalUsd: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  events: number;
  priceCard: string;
  rows: UsageRow[];
};

export type UsageEvent = {
  id: string;
  agentName: string;
  model: string;
  credits: number;
  runMinutes: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  costUsd: string;
  computeCostUsd: string;
  source: string;
  authMode: string;
  compute: string;
  recordedAt: string;
};

const SUMMARY = `query UsageSummary($month: String, $groupBy: String) {
  usageSummary(month: $month, groupBy: $groupBy) {
    month groupBy credits runMinutes costUsd computeCostUsd totalUsd
    inputTokens outputTokens cacheReadTokens events priceCard
    rows { key label agentUserId model month inputTokens outputTokens cacheReadTokens
           credits runMinutes runs costUsd computeCostUsd limitCredits budgetAction }
  }
}`;

const EVENTS = `query UsageEvents($month: String, $limit: Int) {
  usageEvents(month: $month, limit: $limit) {
    id agentName model credits runMinutes inputTokens outputTokens cacheReadTokens
    costUsd computeCostUsd source authMode compute recordedAt
  }
}`;

export async function loadSummary(month: string, groupBy: Grouping) {
  const d = await gql<{ usageSummary: UsageSummary }>(SUMMARY, {
    month,
    groupBy,
  });
  return d.usageSummary;
}

export const RECENT_LIMIT = 15;

export async function loadEvents(month: string) {
  const d = await gql<{ usageEvents: UsageEvent[] }>(EVENTS, {
    month,
    limit: RECENT_LIMIT,
  });
  return d.usageEvents;
}
