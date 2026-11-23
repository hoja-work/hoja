import { gql } from "../../ui";

export type Scope = "workspace" | "team" | "project";

export type KnowledgeDoc = {
  id: string;
  scope: Scope;
  scopeId: string | null;
  title: string;
  body: string;
  enabled: boolean;
  updatedBy: { id: string; displayName: string | null; name: string } | null;
  createdAt: string;
  updatedAt: string;
};

export type Named = { id: string; name: string; key?: string };

export type KnowledgeData = {
  docs: KnowledgeDoc[];
  teams: Named[];
  projects: Named[];
};

/** Agents receive at most this many characters of merged knowledge (knowledge.py). */
export const MAX_CONTEXT_CHARS = 12000;

const DOC_FIELDS = `id scope scopeId title body enabled
  updatedBy { id name displayName } createdAt updatedAt`;

export async function loadKnowledge(): Promise<KnowledgeData> {
  const d = await gql<{
    knowledgeDocs: KnowledgeDoc[];
    teams: { nodes: Named[] };
    projects: { nodes: Named[] };
  }>(`query SettingsKnowledge {
    knowledgeDocs { ${DOC_FIELDS} }
    teams { nodes { id key name } }
    projects { nodes { id name } }
  }`);
  return {
    docs: d.knowledgeDocs || [],
    teams: d.teams?.nodes || [],
    projects: d.projects?.nodes || [],
  };
}

export type DocInput = {
  scope: Scope;
  scopeId: string | null;
  title: string;
  body: string;
};

export async function createDoc(input: DocInput): Promise<KnowledgeDoc> {
  const d = await gql<{
    knowledgeDocCreate: { success: boolean; knowledgeDoc: KnowledgeDoc | null };
  }>(
    `mutation SettingsKnowledgeCreate($input: KnowledgeDocInput!) {
      knowledgeDocCreate(input: $input) { success knowledgeDoc { ${DOC_FIELDS} } }
    }`,
    { input: { ...input, enabled: true } },
  );
  const doc = d.knowledgeDocCreate?.knowledgeDoc;
  if (!d.knowledgeDocCreate?.success || !doc)
    throw new Error("The document wasn't saved. Try again.");
  return doc;
}

export async function updateDoc(
  id: string,
  input: Partial<DocInput> & { enabled?: boolean },
): Promise<KnowledgeDoc> {
  const d = await gql<{
    knowledgeDocUpdate: { success: boolean; knowledgeDoc: KnowledgeDoc | null };
  }>(
    `mutation SettingsKnowledgeUpdate($id: String!, $input: KnowledgeDocUpdateInput!) {
      knowledgeDocUpdate(id: $id, input: $input) { success knowledgeDoc { ${DOC_FIELDS} } }
    }`,
    { id, input },
  );
  const doc = d.knowledgeDocUpdate?.knowledgeDoc;
  if (!d.knowledgeDocUpdate?.success || !doc)
    throw new Error("The change wasn't saved. Try again.");
  return doc;
}

/** The mutation answers `false` (not an error) when nothing was deleted: treat that as one. */
export async function deleteDoc(id: string): Promise<void> {
  const d = await gql<{ knowledgeDocDelete: boolean }>(
    `mutation SettingsKnowledgeDelete($id: String!) { knowledgeDocDelete(id: $id) }`,
    { id },
  );
  if (d.knowledgeDocDelete !== true)
    throw new Error(
      "This document no longer exists. Someone may have deleted it already; the list has been refreshed.",
    );
}

export async function loadContext(
  teamId: string | null,
  projectId: string | null,
): Promise<string> {
  const d = await gql<{ knowledgeContext: string }>(
    `query SettingsKnowledgeContext($teamId: String, $projectId: String) {
      knowledgeContext(teamId: $teamId, projectId: $projectId)
    }`,
    { teamId, projectId },
  );
  return d.knowledgeContext || "";
}
