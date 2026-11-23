import { createContext, useContext } from "react";

export type SettingsViewer = {
  id?: string;
  name?: string;
  email?: string;
  admin?: boolean;
  avatarSeed?: string | null;
};
export type SettingsTeam = {
  id: string;
  name: string;
  key: string;
  description?: string;
  showInitiatives?: boolean;
  [k: string]: unknown;
};
export type Capability =
  | "team.create"
  | "member.invite"
  | "task.delete"
  | "label.manage"
  | "extension.manage";
export type SettingsWorkspace = { id: string; name: string; urlKey: string };

/* Everything a settings section may need from the app shell. Sections read this instead of
 * taking props, so a section can be written, moved or tested without touching main.tsx. */
export type SettingsContextValue = {
  viewer: SettingsViewer;
  workspace: SettingsWorkspace;
  teams: SettingsTeam[];
  isAdmin: boolean;
  /** True when the viewer holds a workspace capability (admins hold all). Gate UI on this,
   *  not on isAdmin, wherever the backend checks a capability (see permissions.py). */
  can: (capability: Capability) => boolean;
  /** False until the viewer's capabilities have loaded; gates wait instead of refusing. */
  capabilitiesReady: boolean;
  onTeamsChanged: (teams: SettingsTeam[]) => void;
  onWorkspaceSaved: (w: SettingsWorkspace) => void;
  onProfileSaved: (name: string) => void;
  /** Account preferences live in the shell (they restyle the whole app). */
  theme: string;
  fontScale: string;
  savePreference: (key: "theme" | "fontSize", value: string) => Promise<void>;
  openSection: (id: string) => void;
  openMember: (id: string) => void;
  openTeamCycleSettings: (team: SettingsTeam) => void;
};

export const SettingsContext = createContext<SettingsContextValue | null>(null);

export function useSettings(): SettingsContextValue {
  const v = useContext(SettingsContext);
  if (!v) throw new Error("useSettings outside SettingsContext");
  return v;
}
