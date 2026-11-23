/** GraphQL helpers and formatting utilities for member profiles and leaderboards. */

export type ActivityDay = {
  date: string;
  tokens: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  credits: number;
  runMinutes: number;
  runs: number;
  completedTasks: number;
  actions: number;
  intensity: number;
};

export type PersonLeaderboardRow = {
  userId: string;
  name: string;
  displayName?: string | null;
  email?: string | null;
  avatarSeed?: string | null;
  kind: string;
  tokens: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  credits: number;
  runMinutes: number;
  runs: number;
  completedTasks: number;
  openTasks: number;
  actions: number;
  activeDays: number;
};

export type TaskLeaderboardRow = {
  issueId: string;
  identifier: string;
  title: string;
  stateName?: string | null;
  stateColor?: string | null;
  stateType?: string | null;
  assigneeId?: string | null;
  assigneeName?: string | null;
  assigneeAvatarSeed?: string | null;
  delegateId?: string | null;
  delegateName?: string | null;
  delegateAvatarSeed?: string | null;
  tokens: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  credits: number;
  runMinutes: number;
  runs: number;
};

export type AgentLeaderboardRow = {
  agentUserId: string;
  name: string;
  avatarSeed?: string | null;
  tokens: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  credits: number;
  runMinutes: number;
  runs: number;
  completedRuns: number;
};

export type ActivityLeaderboard = {
  days: number;
  heatmapDays: number;
  totalTokens: number;
  totalInputTokens: number;
  totalOutputTokens: number;
  totalCacheReadTokens: number;
  totalCredits: number;
  totalRunMinutes: number;
  totalRuns: number;
  totalCompletedTasks: number;
  totalActions: number;
  daily: ActivityDay[];
  people: PersonLeaderboardRow[];
  tasks: TaskLeaderboardRow[];
  agents: AgentLeaderboardRow[];
};

export type ProfileUser = {
  id: string;
  name: string;
  displayName?: string | null;
  email?: string | null;
  avatarSeed?: string | null;
  admin: boolean;
  active: boolean;
  kind: string;
  timezone?: string | null;
  statusEmoji?: string | null;
  statusLabel?: string | null;
  createdAt?: string | null;
};

export type ProfileTeam = {
  id: string;
  key: string;
  name: string;
};

export type ProfileTaskSummary = {
  id: string;
  identifier: string;
  title: string;
  priority: number;
  stateName?: string | null;
  stateColor?: string | null;
  stateType?: string | null;
  updatedAt?: string | null;
  tokens: number;
  runMinutes: number;
};

export type ProfileActivityItem = {
  id: string;
  eventType: string;
  createdAt: string;
  issueId: string;
  issueIdentifier: string;
  issueTitle: string;
};

export type MemberProfile = {
  user: ProfileUser;
  teams: ProfileTeam[];
  days: number;
  heatmapDays: number;
  totalTokens: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  credits: number;
  runMinutes: number;
  runs: number;
  completedTasks: number;
  openTasks: number;
  createdTasks: number;
  actions: number;
  activeDays: number;
  currentStreak: number;
  longestStreak: number;
  rankByTokens?: number | null;
  rankByCompleted?: number | null;
  daily: ActivityDay[];
  topTasks: TaskLeaderboardRow[];
  assignedTasks: ProfileTaskSummary[];
  recentActivity: ProfileActivityItem[];
};

export type TaskModelUsage = {
  agentUserId: string;
  agentName: string;
  agentAvatarSeed?: string | null;
  model: string;
  tokens: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  credits: number;
  runMinutes: number;
  runs: number;
};

export type TaskUsageSummary = {
  issueId: string;
  tokens: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  credits: number;
  runMinutes: number;
  runs: number;
  breakdown: TaskModelUsage[];
};

async function gqlRequest<T>(
  query: string,
  variables?: Record<string, unknown>,
): Promise<T> {
  const token = localStorage.getItem("hoja_token");
  const response = await fetch("/graphql", {
    method: "POST",
    credentials: "include",
    headers: {
      "content-type": "application/json",
      ...(token && token !== "session"
        ? { authorization: `Bearer ${token}` }
        : {}),
    },
    body: JSON.stringify({ query, variables }),
  });
  const body = await response.json();
  if (!response.ok || body.errors?.length) {
    throw new Error(body.errors?.[0]?.message || "Could not load metrics.");
  }
  return body.data as T;
}

const LEADERBOARD_QUERY = `query ActivityLeaderboard($days: Int, $heatmapDays: Int, $limit: Int) {
  activityLeaderboard(days: $days, heatmapDays: $heatmapDays, limit: $limit) {
    days heatmapDays totalTokens totalInputTokens totalOutputTokens totalCacheReadTokens
    totalCredits totalRunMinutes totalRuns totalCompletedTasks totalActions
    daily { date tokens inputTokens outputTokens cacheReadTokens credits runMinutes runs completedTasks actions intensity }
    people { userId name displayName email avatarSeed kind tokens inputTokens outputTokens cacheReadTokens credits runMinutes runs completedTasks openTasks actions activeDays }
    tasks { issueId identifier title stateName stateColor stateType assigneeId assigneeName assigneeAvatarSeed delegateId delegateName delegateAvatarSeed tokens inputTokens outputTokens cacheReadTokens credits runMinutes runs }
    agents { agentUserId name avatarSeed tokens inputTokens outputTokens cacheReadTokens credits runMinutes runs completedRuns }
  }
}`;

const MEMBER_PROFILE_QUERY = `query MemberProfile($userId: ID!, $days: Int, $heatmapDays: Int) {
  memberProfile(userId: $userId, days: $days, heatmapDays: $heatmapDays) {
    user { id name displayName email avatarSeed admin active kind timezone statusEmoji statusLabel createdAt }
    teams { id key name }
    days heatmapDays totalTokens inputTokens outputTokens cacheReadTokens credits runMinutes runs
    completedTasks openTasks createdTasks actions activeDays currentStreak longestStreak
    rankByTokens rankByCompleted
    daily { date tokens inputTokens outputTokens cacheReadTokens credits runMinutes runs completedTasks actions intensity }
    topTasks { issueId identifier title stateName stateColor stateType assigneeId assigneeName assigneeAvatarSeed delegateId delegateName delegateAvatarSeed tokens inputTokens outputTokens cacheReadTokens credits runMinutes runs }
    assignedTasks { id identifier title priority stateName stateColor stateType updatedAt tokens runMinutes }
    recentActivity { id eventType createdAt issueId issueIdentifier issueTitle }
  }
}`;

const TASK_USAGE_QUERY = `query TaskUsage($issueId: String!) {
  taskUsage(issueId: $issueId) {
    issueId tokens inputTokens outputTokens cacheReadTokens credits runMinutes runs
    breakdown { agentUserId agentName agentAvatarSeed model tokens inputTokens outputTokens cacheReadTokens credits runMinutes runs }
  }
}`;

export async function fetchLeaderboard(
  days = 30,
  heatmapDays = 182,
  limit = 25,
): Promise<ActivityLeaderboard> {
  const data = await gqlRequest<{ activityLeaderboard: ActivityLeaderboard }>(
    LEADERBOARD_QUERY,
    { days, heatmapDays, limit },
  );
  return data.activityLeaderboard;
}

export async function fetchMemberProfile(
  userId: string,
  days = 30,
  heatmapDays = 182,
): Promise<MemberProfile | null> {
  const data = await gqlRequest<{ memberProfile: MemberProfile | null }>(
    MEMBER_PROFILE_QUERY,
    { userId, days, heatmapDays },
  );
  return data.memberProfile;
}

export async function fetchTaskUsage(
  issueId: string,
): Promise<TaskUsageSummary | null> {
  const data = await gqlRequest<{ taskUsage: TaskUsageSummary | null }>(
    TASK_USAGE_QUERY,
    { issueId },
  );
  return data.taskUsage;
}

export function formatTokens(count: number): string {
  const n = Math.max(0, Math.round(count || 0));
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 1 : 2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 100_000 ? 0 : 1)}k`;
  return String(n);
}

export function formatDuration(minutes: number): string {
  const m = Math.max(0, Math.round(minutes || 0));
  if (m === 0) return "0m";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem > 0 ? `${h}h ${rem}m` : `${h}h`;
}

export function formatRelativeTime(iso?: string | null): string {
  if (!iso) return "";
  const ms = Date.now() - Date.parse(iso);
  if (!Number.isFinite(ms)) return "";
  const mins = Math.max(0, Math.floor(ms / 60_000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

export function formatActivityVerb(eventType: string): string {
  const labels: Record<string, string> = {
    created: "Created",
    stateChange: "Moved",
    assigneeChange: "Assigned",
    priorityChange: "Reprioritized",
    comment: "Commented on",
    commentCreate: "Commented on",
    updated: "Updated",
    slaBreached: "SLA breached on",
    slaResolved: "Met SLA on",
    slaStarted: "Started SLA on",
    completed: "Completed",
  };
  return labels[eventType] || eventType.replace(/([A-Z])/g, " $1").trim();
}
