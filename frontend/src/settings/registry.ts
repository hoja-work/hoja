import type React from "react";
import type { Capability } from "./context";
import {
  BarChart3,
  Bell,
  Blocks,
  BookOpen,
  Bot,
  Building2,
  CalendarDays,
  CircleDot,
  Clock,
  KeyRound,
  LayoutTemplate,
  Layers3,
  MessageCircle,
  Plug,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Tags,
  Target,
  UserRound,
  Users,
} from "lucide-react";

/* The single list of settings sections. The settings navigation, the router, the command
 * palette and the e2e suite all read this; nothing else names a section. */
export type SettingsSectionId =
  | "preferences"
  | "profile"
  | "notifications"
  | "api-keys"
  | "agent-conversations"
  | "connections"
  | "knowledge"
  | "issue-labels"
  | "issue-templates"
  | "slas"
  | "cycles"
  | "project-statuses"
  | "project-templates"
  | "initiatives"
  | "workspace"
  | "workspace-templates"
  | "teams"
  | "members"
  | "permissions"
  | "security"
  | "billing"
  | "model-providers"
  | "extensions";

export type SettingsSectionDef = {
  id: SettingsSectionId;
  label: string;
  group:
    "Account" | "Tasks" | "Projects" | "Workspace" | "Agents & integrations";
  icon: React.ComponentType<{ size?: number; className?: string }>;
  /** Workspace admins only. Non-admins never see the entry; a deep link shows "Admins only". */
  admin?: boolean;
  /** Visible to admins and to members granted this capability (the backend checks the same one). */
  capability?: Capability;
  /** Extra search words, so "theme" finds Preferences and "token" finds API keys. */
  keywords?: string;
};

export const SETTINGS_SECTIONS: SettingsSectionDef[] = [
  {
    id: "preferences",
    label: "Preferences",
    group: "Account",
    icon: SlidersHorizontal,
    keywords: "theme dark light font size appearance",
  },
  {
    id: "profile",
    label: "Profile",
    group: "Account",
    icon: UserRound,
    keywords: "name email avatar password",
  },
  {
    id: "notifications",
    label: "Notifications",
    group: "Account",
    icon: Bell,
    keywords: "email inbox alerts",
  },
  {
    id: "api-keys",
    label: "Connected apps & keys",
    group: "Account",
    icon: KeyRound,
    keywords: "api key token personal oauth mcp",
  },
  {
    id: "issue-labels",
    label: "Labels",
    group: "Tasks",
    icon: Tags,
    keywords: "tags",
  },
  {
    id: "issue-templates",
    label: "Task templates",
    group: "Tasks",
    icon: LayoutTemplate,
    admin: true,
  },
  {
    id: "slas",
    label: "SLAs",
    group: "Tasks",
    icon: Clock,
    admin: true,
    keywords: "response resolution deadline",
  },
  {
    id: "cycles",
    label: "Cycles",
    group: "Tasks",
    icon: CalendarDays,
    keywords: "sprint cadence",
  },
  {
    id: "project-statuses",
    label: "Project statuses",
    group: "Projects",
    icon: CircleDot,
    admin: true,
  },
  {
    id: "project-templates",
    label: "Project templates",
    group: "Projects",
    icon: Layers3,
    admin: true,
  },
  {
    id: "initiatives",
    label: "Initiatives",
    group: "Projects",
    icon: Target,
    keywords: "roadmap",
  },
  {
    id: "workspace",
    label: "General",
    group: "Workspace",
    icon: Building2,
    admin: true,
    keywords: "workspace name url features delete",
  },
  {
    id: "workspace-templates",
    label: "Templates",
    group: "Workspace",
    icon: LayoutTemplate,
    admin: true,
    keywords:
      "workspace template preset defaults setup import export sla labels agents",
  },
  { id: "teams", label: "Teams", group: "Workspace", icon: Users },
  {
    id: "members",
    label: "Members",
    group: "Workspace",
    icon: UserRound,
    capability: "member.invite",
    keywords: "invite people users roles",
  },
  {
    id: "permissions",
    label: "Permissions",
    group: "Workspace",
    icon: Shield,
    admin: true,
    keywords: "roles access",
  },
  {
    id: "security",
    label: "Security",
    group: "Workspace",
    icon: ShieldCheck,
    admin: true,
    keywords: "sso login domains sessions",
  },
  {
    id: "billing",
    label: "Billing & usage",
    group: "Workspace",
    icon: BarChart3,
    admin: true,
    keywords: "plan cost spend usage ledger",
  },
  {
    id: "model-providers",
    label: "Model providers",
    group: "Agents & integrations",
    icon: Bot,
    admin: true,
    keywords: "openai anthropic llm ai key",
  },
  {
    id: "agent-conversations",
    label: "Agent conversations",
    group: "Agents & integrations",
    icon: MessageCircle,
  },
  {
    id: "connections",
    label: "Connectors",
    group: "Agents & integrations",
    icon: Plug,
    keywords: "github slack google integrations",
  },
  {
    id: "knowledge",
    label: "Knowledge",
    group: "Agents & integrations",
    icon: BookOpen,
    keywords: "docs context",
  },
  {
    id: "extensions",
    label: "Extensions",
    group: "Agents & integrations",
    icon: Blocks,
    capability: "extension.manage",
    keywords: "plugins",
  },
];

export const SETTINGS_GROUPS: SettingsSectionDef["group"][] = [
  "Account",
  "Tasks",
  "Projects",
  "Workspace",
  "Agents & integrations",
];

/** Old ids that still arrive through bookmarks and links. */
export const SETTINGS_ALIASES: Record<string, SettingsSectionId> = {
  account: "profile",
  usage: "billing",
  "mcp-connectors": "connections",
};

export function findSection(id: string): SettingsSectionDef | undefined {
  const canonical = SETTINGS_ALIASES[id] || id;
  return SETTINGS_SECTIONS.find((s) => s.id === canonical);
}

export function canOpen(
  s: SettingsSectionDef,
  isAdmin: boolean,
  can: (c: Capability) => boolean,
) {
  if (isAdmin) return true;
  if (s.admin) return false;
  if (s.capability) return can(s.capability);
  return true;
}

export function visibleSections(
  isAdmin: boolean,
  can: (c: Capability) => boolean = () => false,
) {
  return SETTINGS_SECTIONS.filter((s) => canOpen(s, isAdmin, can));
}

export function matchesQuery(s: SettingsSectionDef, q: string) {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return `${s.label} ${s.group} ${s.keywords || ""}`
    .toLowerCase()
    .includes(needle);
}
