import {
  clearExtensionActionsCache,
  type Extension,
} from "../../../extensions";
import { gql as rawGql } from "../../ui";

/* The server's "HOJA_SECRET_KEY must be set" names an operator variable; say what it means. */
async function gql<T>(query: string, variables?: Record<string, unknown>) {
  try {
    return await rawGql<T>(query, variables);
  } catch (e) {
    if (e instanceof Error && /HOJA_SECRET_KEY/.test(e.message))
      throw new Error(
        "This server can't store keys yet because encryption isn't set up. Ask whoever runs your Hoja server to configure it.",
      );
    throw e;
  }
}

export type { Extension };

/* Same fields as src/extensions.ts minus `enabled`, which is derived from uninstalled_at and
 * is always true for a listed extension (there is no enable/disable mutation). */
const FIELDS =
  "id key name version url access manifest botUser{id name} createdAt updatedAt " +
  "hooks{key on failurePolicy position} actions{id key title entity extensionId extensionName} " +
  "failures{at kind key httpStatus message}";

export async function listExtensions(): Promise<Extension[]> {
  const d = await gql<{ extensions: Extension[] }>(
    `query Extensions { extensions { ${FIELDS} } }`,
  );
  return d.extensions ?? [];
}

export async function installExtension(manifest: Record<string, unknown>) {
  const d = await gql<{
    extensionInstall: {
      success: boolean;
      extension: Extension | null;
      apiKey: string | null;
      signingSecret: string | null;
    };
  }>(
    `mutation ExtensionInstall($manifest: JSON!) {
      extensionInstall(manifest: $manifest) { success apiKey signingSecret extension { ${FIELDS} } }
    }`,
    { manifest },
  );
  const p = d.extensionInstall;
  if (!p?.success || !p.extension)
    throw new Error("The extension wasn't installed. Try again.");
  clearExtensionActionsCache();
  return p as typeof p & { extension: Extension };
}

export async function updateExtension(
  id: string,
  manifest: Record<string, unknown>,
) {
  const d = await gql<{
    extensionUpdate: { success: boolean; extension: Extension | null };
  }>(
    `mutation ExtensionUpdate($id: String!, $manifest: JSON!) {
      extensionUpdate(id: $id, manifest: $manifest) { success extension { ${FIELDS} } }
    }`,
    { id, manifest },
  );
  const p = d.extensionUpdate;
  if (!p?.success || !p.extension)
    throw new Error("The manifest wasn't saved. Try again.");
  clearExtensionActionsCache();
  return p.extension;
}

export async function uninstallExtension(id: string) {
  const d = await gql<{ extensionUninstall: { success: boolean } }>(
    "mutation ExtensionUninstall($id: String!) { extensionUninstall(id: $id) { success } }",
    { id },
  );
  clearExtensionActionsCache();
  if (!d.extensionUninstall?.success)
    throw new Error(
      "This extension is no longer installed. Someone may have removed it already.",
    );
}
