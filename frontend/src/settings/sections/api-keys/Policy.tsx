import { Segmented } from "../../../components/ui/switch";
import {
  Loadable,
  SettingRow,
  SettingsCard,
  SettingsSection,
  gql,
  useResource,
  useSetting,
} from "../../ui";

type Policy = {
  idleTimeoutMinutes: number | null;
  allowedSigninMethods: string[] | null;
  ipAllowlist: string[] | null;
  oauthClients: string | null;
};
const POLICY_FIELDS =
  "idleTimeoutMinutes allowedSigninMethods ipAllowlist oauthClients";

const readPolicy = () =>
  gql<{ workspaceSecurityPolicy: Policy }>(
    `query Policy{workspaceSecurityPolicy{${POLICY_FIELDS}}}`,
  ).then((d) => d.workspaceSecurityPolicy);

/* Which apps members may approve (enforced on the OAuth approval page). It lives on the same
 * security-policy row as the Security page's fields, and workspaceSecurityUpdate clears any of
 * the three older fields it is not sent, so every save re-reads the row first and sends them back
 * unchanged. That way a change made on the Security page a moment ago is never overwritten. */
async function saveOauthClients(value: string) {
  const current = await readPolicy();
  const d = await gql<{ workspaceSecurityUpdate: Policy }>(
    `mutation($input:WorkspaceSecurityPolicyInput!){workspaceSecurityUpdate(input:$input){${POLICY_FIELDS}}}`,
    {
      input: {
        idleTimeoutMinutes: current.idleTimeoutMinutes,
        allowedSigninMethods: current.allowedSigninMethods,
        ipAllowlist: current.ipAllowlist,
        oauthClients: value,
      },
    },
  );
  if ((d.workspaceSecurityUpdate?.oauthClients || "all") !== value)
    throw new Error("The setting wasn't saved. Try again.");
}

function PolicyRow({ initial }: { initial: string }) {
  const setting = useSetting({
    value: initial,
    save: (next: string) => saveOauthClients(next),
    undoLabel: "App approval policy changed",
  });
  return (
    <SettingRow
      label="Apps members can approve"
      description={
        setting.value === "first_party"
          ? "Only Hoja's own apps, like the Hoja CLI. Other apps are refused on the approval page; apps already approved keep working until revoked below."
          : "Any app, including MCP clients and third-party tools. Hoja's own apps are always allowed."
      }
      status={setting.status}
    >
      <Segmented
        label="Apps members can approve"
        className="flex-none"
        value={setting.value}
        onValueChange={(v) => void setting.set(v)}
        options={[
          { value: "all", label: "Any app" },
          { value: "first_party", label: "Hoja apps only" },
        ]}
      />
    </SettingRow>
  );
}

export function WorkspacePolicySection() {
  const policy = useResource(readPolicy);
  return (
    <SettingsSection
      title="Workspace access"
      description="Applies to every member of this workspace. Only admins see this."
      bare
    >
      <Loadable resource={policy} rows={1}>
        {(data) => (
          <SettingsCard>
            <PolicyRow initial={data.oauthClients || "all"} />
          </SettingsCard>
        )}
      </Loadable>
    </SettingsSection>
  );
}
