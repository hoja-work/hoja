import { ArrowRight } from "lucide-react";
import { Button } from "../../components/ui/button";
import { useSettings } from "../context";
import {
  CopyField,
  Loadable,
  SettingRow,
  SettingsCard,
  SettingsPage,
  SettingsSection,
  gql,
  useResource,
} from "../ui";
import {
  AppsSection,
  SessionsSection,
  WorkspacePolicyLocked,
} from "./api-keys/Access";
import { ApiKeysSection } from "./api-keys/ApiKeys";
import { WorkspacePolicySection } from "./api-keys/Policy";

/* Connected apps & keys: how tools reach this workspace as you (addresses, approved apps, API
 * keys, browser sessions) and, for admins, which apps members may approve and every app that
 * holds access. Every revoke and sign-out confirms first and names what stops working. */
function ConnectSection() {
  const workspace = useResource(() =>
    gql<{ workspace: { name: string; origin: string } }>(
      "query ConnectOrigin{workspace{name origin}}",
    ).then((d) => d.workspace),
  );
  return (
    <SettingsSection
      title="Connect"
      description="Addresses for agents, MCP clients and scripts. The Hoja CLI and MCP clients sign in through your browser; unattended scripts use an API key."
      bare
    >
      <Loadable resource={workspace} rows={4}>
        {(w) => {
          const origin = w.origin.replace(/\/+$/, "");
          const rows = [
            ["Sign in from a terminal", `hoja auth login ${origin}`],
            ["MCP server URL", `${origin}/mcp`],
            ["GraphQL endpoint", `${origin}/graphql`],
            [
              "Add to Claude Code",
              `claude mcp add --transport http hoja ${origin}/mcp`,
            ],
          ] as const;
          return (
            <SettingsCard>
              {rows.map(([label, value]) => (
                <SettingRow key={label} label={label} stacked>
                  <CopyField label={label} value={value} secret />
                </SettingRow>
              ))}
            </SettingsCard>
          );
        }}
      </Loadable>
    </SettingsSection>
  );
}

export default function ApiKeysSettings() {
  const { isAdmin, openSection } = useSettings();
  return (
    <SettingsPage
      title="Connected apps & keys"
      description="Everything that can act as you in this workspace: apps you approved, API keys and signed-in browsers."
    >
      <AppsSection scope="mine" />
      <ApiKeysSection />
      <SessionsSection />
      <ConnectSection />
      {isAdmin ? (
        <>
          <WorkspacePolicySection />
          <AppsSection scope="workspace" />
        </>
      ) : (
        <WorkspacePolicyLocked />
      )}
      <SettingsSection>
        <SettingRow
          label="Services Hoja connects to"
          description="GitHub, Slack and other services that Hoja reaches on your behalf are managed under Connectors."
        >
          <Button onClick={() => openSection("connections")}>
            Open Connectors
            <ArrowRight className="size-3.5" aria-hidden="true" />
          </Button>
        </SettingRow>
      </SettingsSection>
    </SettingsPage>
  );
}
