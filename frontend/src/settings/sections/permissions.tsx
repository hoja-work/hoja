import { useId } from "react";
import { ShieldCheck } from "lucide-react";
import { Button } from "../../components/ui/button";
import { Switch } from "../../components/ui/switch";
import { useSettings } from "../context";
import {
  EmptyState,
  Loadable,
  Notice,
  SettingRow,
  SettingsPage,
  SettingsSection,
  gql,
  useResource,
  useSetting,
} from "../ui";

/* Permissions: one switch per member capability. Label and description come from the server
 * (permissions.CAPABILITIES), so a capability added there shows up here without a UI change.
 * Every switch saves on its own, optimistically, and offers Undo because it changes what
 * everyone without admin can do. */

type PermissionRow = {
  capability: string;
  label: string;
  description: string;
  memberAllowed: boolean;
};

export default function PermissionsSettings() {
  const { openSection } = useSettings();
  const permissions = useResource(() =>
    gql<{ workspacePermissions: PermissionRow[] }>(
      `query{workspacePermissions{capability label description memberAllowed}}`,
    ).then((d) => d.workspacePermissions),
  );

  return (
    <SettingsPage
      title="Permissions"
      description="Choose what members can do without being made an admin."
    >
      <Notice title="Admins always hold every capability">
        These switches only change what members can do. The server checks them
        on every request, so turning one off takes effect immediately.{" "}
        <Button
          variant="link"
          className="text-sm font-medium"
          onClick={() => openSection("members")}
        >
          Manage admins
        </Button>
      </Notice>
      <SettingsSection
        title="Member capabilities"
        description="Everything here starts off, so only admins can do it until you allow members."
      >
        <Loadable
          resource={permissions}
          rows={5}
          isEmpty={(rows) => rows.length === 0}
          empty={
            <EmptyState
              icon={<ShieldCheck />}
              title="Nothing to configure"
              description="This server has no member capabilities to grant. Every admin-only action stays admin-only."
            />
          }
        >
          {(rows) =>
            rows.map((row) => (
              <CapabilityRow
                key={row.capability}
                row={row}
                onSaved={(memberAllowed) =>
                  permissions.setData((current) =>
                    current?.map((r) =>
                      r.capability === row.capability
                        ? { ...r, memberAllowed }
                        : r,
                    ),
                  )
                }
              />
            ))
          }
        </Loadable>
      </SettingsSection>
    </SettingsPage>
  );
}

function CapabilityRow({
  row,
  onSaved,
}: {
  row: PermissionRow;
  onSaved: (memberAllowed: boolean) => void;
}) {
  const id = useId();
  const label = row.label.charAt(0).toLowerCase() + row.label.slice(1);
  const setting = useSetting({
    value: row.memberAllowed,
    undoLabel: row.memberAllowed
      ? `Only admins can ${label} now`
      : `Members can ${label} now`,
    save: async (next) => {
      const d = await gql<{
        workspacePermissionUpdate: { memberAllowed: boolean };
      }>(
        `mutation($capability:String!,$memberAllowed:Boolean!){workspacePermissionUpdate(capability:$capability,memberAllowed:$memberAllowed){capability memberAllowed}}`,
        { capability: row.capability, memberAllowed: next },
      );
      onSaved(d.workspacePermissionUpdate.memberAllowed);
    },
  });
  return (
    <SettingRow
      label={row.label}
      htmlFor={id}
      description={row.description}
      status={setting.status}
    >
      <span
        className="text-sm text-muted-foreground tabular-nums"
        aria-hidden="true"
      >
        {setting.value ? "Members and admins" : "Admins only"}
      </span>
      <Switch
        id={id}
        aria-label={`Members can ${label}`}
        checked={setting.value}
        onCheckedChange={(on) => void setting.set(on)}
      />
    </SettingRow>
  );
}
