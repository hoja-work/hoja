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

export type ModelProvider = {
  id: string;
  name: string;
  provider: string;
  modelId: string;
  /** Null for managed providers: their address is internal and never sent to a browser. */
  baseUrl: string | null;
  contextWindow: number | null;
  managed: boolean;
  enabled: boolean;
  hasApiKey: boolean;
  updatedAt?: string;
};

const FIELDS =
  "id name provider modelId baseUrl contextWindow enabled hasApiKey managed updatedAt";

export async function listProviders(): Promise<ModelProvider[]> {
  const d = await gql<{ modelProviders: ModelProvider[] }>(
    `query ModelProviders { modelProviders { ${FIELDS} } }`,
  );
  return d.modelProviders ?? [];
}

export async function createProvider(input: {
  name: string;
  provider: string;
  modelId: string;
  baseUrl: string | null;
  apiKey: string;
}): Promise<ModelProvider> {
  const d = await gql<{ modelProviderCreate: ModelProvider }>(
    `mutation ModelProviderCreate($name:String!,$provider:String!,$modelId:String!,$baseUrl:String,$apiKey:String!){
      modelProviderCreate(name:$name,provider:$provider,modelId:$modelId,baseUrl:$baseUrl,apiKey:$apiKey){ ${FIELDS} }
    }`,
    input,
  );
  return d.modelProviderCreate;
}

/** Only the fields present are changed; the server treats a missing field as "keep". */
export async function updateProvider(
  id: string,
  patch: {
    name?: string;
    modelId?: string;
    baseUrl?: string;
    apiKey?: string;
    enabled?: boolean;
  },
): Promise<ModelProvider> {
  const d = await gql<{ modelProviderUpdate: ModelProvider }>(
    `mutation ModelProviderUpdate($id:String!,$name:String,$modelId:String,$baseUrl:String,$apiKey:String,$enabled:Boolean){
      modelProviderUpdate(id:$id,name:$name,modelId:$modelId,baseUrl:$baseUrl,apiKey:$apiKey,enabled:$enabled){ ${FIELDS} }
    }`,
    { id, ...patch },
  );
  return d.modelProviderUpdate;
}

export async function deleteProvider(id: string): Promise<void> {
  const d = await gql<{ modelProviderDelete: boolean }>(
    "mutation ModelProviderDelete($id:String!){ modelProviderDelete(id:$id) }",
    { id },
  );
  // The server answers false (not an error) when the row is already gone.
  if (!d.modelProviderDelete)
    throw new Error(
      "This provider no longer exists. Someone may have deleted it already.",
    );
}

export function hostOf(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
