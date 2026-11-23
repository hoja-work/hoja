import { useId } from "react";
import { Input } from "../../components/ui/input";
import { UserBlobatar } from "../../graph/shared";
import { useSettings } from "../context";
import {
  Badge,
  SettingRow,
  SettingsPage,
  SettingsSection,
  gql,
  useSettingText,
} from "../ui";

const NAME_MAX = 80;

/* Profile: the one thing a person can change about themselves here is their display name.
 * Email, role and avatar are shown honestly as read-only, each saying who controls it. The
 * backend also stores a time zone and a status, but has no way to write them, so they are
 * not shown as if they were settings. */
export default function ProfileSettings() {
  const { viewer, onProfileSaved, openSection, isAdmin } = useSettings();
  const nameId = useId();
  const previousName = viewer.name || "";
  const name = useSettingText({
    value: previousName,
    validate: (next) =>
      !next
        ? "Enter a name. It is how teammates see you on tasks and comments."
        : next.length > NAME_MAX
          ? `Use ${NAME_MAX} characters or fewer.`
          : null,
    save: async (next, previous) => {
      // The new name shows across the app at once and is restored if the server refuses it.
      onProfileSaved(next);
      try {
        const d = await gql<{
          profileNameUpdate: {
            success: boolean;
            user: { name: string } | null;
          } | null;
        }>(
          `mutation($name:String!){profileNameUpdate(name:$name){success user{id name}}}`,
          { name: next },
        );
        if (!d.profileNameUpdate?.success || !d.profileNameUpdate.user)
          throw new Error(
            `The name wasn't saved. Use 1–${NAME_MAX} characters.`,
          );
        onProfileSaved(d.profileNameUpdate.user.name);
      } catch (e) {
        onProfileSaved(previous);
        throw e;
      }
    },
  });

  return (
    <SettingsPage
      title="Profile"
      description="How you appear to everyone in this workspace."
    >
      <SettingsSection title="About you">
        <SettingRow
          label="Avatar"
          description="Generated from your account and shown next to your name. Uploading a photo isn't available."
        >
          <span
            className="grid size-10 flex-none place-items-center overflow-hidden rounded-full bg-subtle"
            aria-hidden="true"
          >
            <UserBlobatar user={viewer} />
          </span>
        </SettingRow>
        <SettingRow
          label="Display name"
          htmlFor={nameId}
          description="Shown on tasks, comments and mentions. Saves when you press Enter or leave the field."
          status={name.status}
        >
          <Input
            id={nameId}
            autoComplete="name"
            maxLength={NAME_MAX}
            className="sm:w-64"
            {...name.inputProps}
            onKeyDown={(e) => {
              // useSettingText blurs on Escape, and its blur handler commits the stale draft.
              // Revert without blurring so Escape never saves (see Proposals, HOJA-734).
              if (e.key === "Escape") {
                e.preventDefault();
                name.inputProps.onChange({
                  target: { value: previousName },
                } as React.ChangeEvent<HTMLInputElement>);
                return;
              }
              name.inputProps.onKeyDown(e);
            }}
          />
        </SettingRow>
        <SettingRow
          label="Email"
          description={viewer.email || "No email on this account"}
          locked="Comes from how you sign in, so it can't be changed here."
        />
        <SettingRow
          label="Role"
          description={
            isAdmin
              ? "You can change workspace settings, members and billing."
              : "You can work on tasks and change your own settings."
          }
          locked={
            isAdmin
              ? "Roles are changed in Members."
              : "Only a workspace admin can change your role."
          }
        >
          <Badge tone={isAdmin ? "strong" : "neutral"}>
            {isAdmin ? "Workspace admin" : "Member"}
          </Badge>
        </SettingRow>
      </SettingsSection>
      <p className="m-0 text-sm text-muted-foreground">
        Looking for theme or text size? They are in{" "}
        <a
          href="/workspace/settings/preferences"
          className="font-medium text-foreground underline underline-offset-2 hover:no-underline"
          onClick={(e) => {
            e.preventDefault();
            openSection("preferences");
          }}
        >
          Preferences
        </a>
        .
      </p>
    </SettingsPage>
  );
}
