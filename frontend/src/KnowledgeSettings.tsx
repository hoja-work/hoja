import React, { useEffect, useState } from "react";
import { BookOpen } from "lucide-react";
import { confirmDialog } from "./dialogs";

type Scope = "workspace" | "team" | "project";
type ScopeFilter = "all" | Scope;

type KnowledgeUser = {
  id: string;
  name: string;
  displayName: string;
};

type KnowledgeDoc = {
  id: string;
  scope: Scope;
  scopeId: string | null;
  title: string;
  body: string;
  enabled: boolean;
  createdBy: KnowledgeUser | null;
  updatedBy: KnowledgeUser | null;
  createdAt: string;
  updatedAt: string;
};

type TeamNode = {
  id: string;
  key: string;
  name: string;
};

type ProjectNode = {
  id: string;
  name: string;
};

const LOAD_QUERY = `
  query KnowledgeSettingsLoad {
    knowledgeDocs {
      id
      scope
      scopeId
      title
      body
      enabled
      createdBy { id name displayName }
      updatedBy { id name displayName }
      createdAt
      updatedAt
    }
    teams {
      nodes {
        id
        key
        name
      }
    }
    projects {
      nodes {
        id
        name
      }
    }
  }
`;

const CREATE_MUTATION = `
  mutation KnowledgeDocCreate($input: KnowledgeDocInput!) {
    knowledgeDocCreate(input: $input) {
      success
      knowledgeDoc {
        id
      }
    }
  }
`;

const UPDATE_MUTATION = `
  mutation KnowledgeDocUpdate($id: String!, $input: KnowledgeDocUpdateInput!) {
    knowledgeDocUpdate(id: $id, input: $input) {
      success
      knowledgeDoc {
        id
      }
    }
  }
`;

const DELETE_MUTATION = `
  mutation KnowledgeDocDelete($id: String!) {
    knowledgeDocDelete(id: $id)
  }
`;

const FILTER_OPTIONS: { id: ScopeFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "workspace", label: "Workspace" },
  { id: "team", label: "Team" },
  { id: "project", label: "Project" },
];

async function callGraphQL<T>(
  query: string,
  variables: Record<string, unknown> = {},
): Promise<T> {
  const token = localStorage.getItem("hoja_token");
  const res = await fetch("/graphql", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      ...(token && token !== "session"
        ? { authorization: `Bearer ${token}` }
        : {}),
    },
    credentials: "include",
    body: JSON.stringify({ query, variables }),
  });
  const body = await res.json();
  if (body.errors?.length) throw new Error(body.errors[0].message);
  return body.data as T;
}

function formatDate(value?: string | null): string {
  if (!value) return "Unknown";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Unknown" : date.toLocaleDateString();
}

export function KnowledgeSettings() {
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [teams, setTeams] = useState<TeamNode[]>([]);
  const [projects, setProjects] = useState<ProjectNode[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<ScopeFilter>("all");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [scope, setScope] = useState<Scope>("workspace");
  const [scopeId, setScopeId] = useState("");
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let live = true;
    callGraphQL<{
      knowledgeDocs: KnowledgeDoc[];
      teams: { nodes: TeamNode[] };
      projects: { nodes: ProjectNode[] };
    }>(LOAD_QUERY)
      .then((data) => {
        if (!live) return;
        setDocs(data.knowledgeDocs || []);
        setTeams(data.teams?.nodes || []);
        setProjects(data.projects?.nodes || []);
      })
      .catch((e) => {
        if (live) setError((e as Error).message);
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [refresh]);

  const resetForm = () => {
    setEditingId(null);
    setScope("workspace");
    setScopeId("");
    setTitle("");
    setBody("");
    setEnabled(true);
  };

  const handleScopeChange = (nextScope: Scope) => {
    setScope(nextScope);
    if (nextScope === "workspace") {
      setScopeId("");
    } else if (nextScope === "team") {
      setScopeId(teams[0]?.id || "");
    } else {
      setScopeId(projects[0]?.id || "");
    }
  };

  const startEdit = (doc: KnowledgeDoc) => {
    setMessage("");
    setError("");
    setEditingId(doc.id);
    setScope(doc.scope);
    setScopeId(doc.scopeId || "");
    setTitle(doc.title);
    setBody(doc.body);
    setEnabled(doc.enabled);
  };

  const submitForm = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const payload = {
        scope,
        scopeId: scope === "workspace" ? null : scopeId || null,
        title: title.trim(),
        body: body.trim(),
        enabled,
      };
      if (editingId) {
        await callGraphQL(UPDATE_MUTATION, { id: editingId, input: payload });
        setMessage("Knowledge document updated.");
      } else {
        await callGraphQL(CREATE_MUTATION, { input: payload });
        setMessage("Knowledge document created.");
      }
      resetForm();
      setRefresh((n) => n + 1);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const toggleEnabled = async (doc: KnowledgeDoc) => {
    setMessage("");
    setError("");
    try {
      await callGraphQL(UPDATE_MUTATION, {
        id: doc.id,
        input: { enabled: !doc.enabled },
      });
      setRefresh((n) => n + 1);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const removeDoc = async (doc: KnowledgeDoc) => {
    if (
      !(await confirmDialog(
        `Delete "${doc.title}"? Agents and delegated runs will no longer receive this document.`,
        { danger: true },
      ))
    )
      return;
    setMessage("");
    setError("");
    try {
      await callGraphQL(DELETE_MUTATION, { id: doc.id });
      if (editingId === doc.id) resetForm();
      setMessage("Knowledge document deleted.");
      setRefresh((n) => n + 1);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const resolveScopeLabel = (doc: KnowledgeDoc): string => {
    if (doc.scope === "workspace") return "Workspace";
    if (doc.scope === "team") {
      const found = teams.find((t) => t.id === doc.scopeId);
      return found ? `Team · ${found.name} (${found.key})` : "Team";
    }
    const found = projects.find((p) => p.id === doc.scopeId);
    return found ? `Project · ${found.name}` : "Project";
  };

  const filteredDocs =
    filter === "all" ? docs : docs.filter((d) => d.scope === filter);

  const canSubmit =
    !busy &&
    title.trim().length > 0 &&
    body.trim().length > 0 &&
    (scope === "workspace" || scopeId.trim().length > 0);

  return (
    <section
      className="mx-auto flex w-full max-w-4xl flex-col gap-6 p-4"
      aria-label="Knowledge and standards settings"
    >
      <header className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <BookOpen size={20} aria-hidden="true" />
          <h1 className="text-xl font-semibold">Knowledge and standards</h1>
        </div>
        <p className="text-sm text-muted">
          Store company standards, architecture notes, and engineering
          conventions in Markdown. Enabled documents are automatically merged in
          hierarchy order (Workspace, Team, Project) and injected into Agent
          Chat, delegated agent runs, and MCP clients via{" "}
          <code className="rounded bg-subtle px-1.5 py-0.5 text-xs">
            knowledgeContext
          </code>
          .
        </p>
      </header>

      <div className="flex flex-col gap-4 rounded-xl border border-line bg-panel p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-base font-semibold">
            {editingId ? "Edit knowledge document" : "Add knowledge document"}
          </h2>
          {editingId && (
            <button
              type="button"
              className="rounded-lg border border-line px-3 py-1.5 text-xs font-medium hover:bg-subtle"
              onClick={resetForm}
            >
              Cancel editing
            </button>
          )}
        </div>

        <form className="flex flex-col gap-4" onSubmit={submitForm}>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <label className="flex flex-col gap-1.5 text-sm font-medium">
              Scope
              <select
                aria-label="Document scope"
                className="rounded-lg border border-line bg-transparent px-3 py-2 text-sm"
                value={scope}
                onChange={(e) => handleScopeChange(e.target.value as Scope)}
              >
                <option value="workspace">Workspace (applies everywhere)</option>
                <option value="team">Team</option>
                <option value="project">Project</option>
              </select>
            </label>

            {scope === "team" && (
              <label className="flex flex-col gap-1.5 text-sm font-medium">
                Team
                <select
                  aria-label="Target team"
                  className="rounded-lg border border-line bg-transparent px-3 py-2 text-sm"
                  value={scopeId}
                  onChange={(e) => setScopeId(e.target.value)}
                  required
                >
                  <option value="">Select a team…</option>
                  {teams.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name} ({t.key})
                    </option>
                  ))}
                </select>
              </label>
            )}

            {scope === "project" && (
              <label className="flex flex-col gap-1.5 text-sm font-medium">
                Project
                <select
                  aria-label="Target project"
                  className="rounded-lg border border-line bg-transparent px-3 py-2 text-sm"
                  value={scopeId}
                  onChange={(e) => setScopeId(e.target.value)}
                  required
                >
                  <option value="">Select a project…</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>

          <label className="flex flex-col gap-1.5 text-sm font-medium">
            Title
            <input
              type="text"
              aria-label="Document title"
              className="rounded-lg border border-line bg-transparent px-3 py-2 text-sm"
              placeholder="e.g. Architecture principles or Python style guide"
              required
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>

          <label className="flex flex-col gap-1.5 text-sm font-medium">
            Markdown content
            <textarea
              aria-label="Document Markdown content"
              className="rounded-lg border border-line bg-transparent px-3 py-2 font-mono text-sm"
              rows={6}
              placeholder="Write instructions, architecture guidelines, or team conventions in Markdown…"
              required
              value={body}
              onChange={(e) => setBody(e.target.value)}
            />
          </label>

          <div className="flex flex-wrap items-center justify-between gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                aria-label="Enable document for automatic agent injection"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              <span>
                Enabled (inject into Agent Chat, delegated runs, and MCP)
              </span>
            </label>

            <button
              type="submit"
              disabled={!canSubmit}
              className="rounded-lg border border-foreground bg-foreground px-4 py-2 text-sm font-medium text-background disabled:cursor-not-allowed disabled:opacity-50"
            >
              {busy
                ? "Saving…"
                : editingId
                  ? "Save changes"
                  : "Save document"}
            </button>
          </div>
        </form>

        {message && (
          <p className="text-sm text-muted" role="status">
            {message}
          </p>
        )}
        {error && (
          <p className="text-sm text-muted" role="alert">
            {error}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filter documents by scope">
            {FILTER_OPTIONS.map((opt) => {
              const active = filter === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  aria-pressed={active}
                  onClick={() => setFilter(opt.id)}
                  className={
                    active
                      ? "rounded-full border border-foreground bg-foreground px-3 py-1 text-xs font-medium text-background"
                      : "rounded-full border border-line bg-transparent px-3 py-1 text-xs font-medium text-muted hover:bg-subtle"
                  }
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
          <span className="rounded-full border border-line bg-subtle px-2.5 py-0.5 text-xs font-medium text-muted">
            {filteredDocs.length}{" "}
            {filteredDocs.length === 1 ? "document" : "documents"}
          </span>
        </div>

        {loading && (
          <p className="rounded-xl border border-line p-4 text-center text-sm text-muted" role="status">
            Loading knowledge documents…
          </p>
        )}

        {!loading && filteredDocs.length === 0 && (
          <div className="flex flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-line p-6 text-center">
            <b className="text-sm font-semibold">No knowledge documents found</b>
            <p className="max-w-md text-xs text-muted">
              Add a workspace, team, or project knowledge document above so
              agents automatically follow your standards and architecture
              decisions.
            </p>
          </div>
        )}

        {!loading && filteredDocs.length > 0 && (
          <ul className="flex flex-col gap-3" aria-label="Knowledge documents">
            {filteredDocs.map((doc) => (
              <li
                key={doc.id}
                className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4"
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <b className="text-sm font-semibold">{doc.title}</b>
                      <span className="rounded-full border border-line bg-subtle px-2 py-0.5 text-xs font-medium text-muted">
                        {resolveScopeLabel(doc)}
                      </span>
                      <span className="rounded-full border border-line bg-subtle px-2 py-0.5 text-xs font-medium text-muted">
                        {doc.enabled ? "Enabled" : "Disabled"}
                      </span>
                    </div>
                    <small className="text-xs text-muted">
                      Updated {formatDate(doc.updatedAt)}
                      {doc.updatedBy?.displayName
                        ? ` by ${doc.updatedBy.displayName}`
                        : ""}
                    </small>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      className="rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium hover:bg-subtle"
                      onClick={() => startEdit(doc)}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium hover:bg-subtle"
                      onClick={() => void toggleEnabled(doc)}
                    >
                      {doc.enabled ? "Disable" : "Enable"}
                    </button>
                    <button
                      type="button"
                      className="rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium hover:bg-subtle"
                      onClick={() => void removeDoc(doc)}
                    >
                      Delete
                    </button>
                  </div>
                </div>

                <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg border border-line bg-subtle p-3 font-mono text-xs">
                  {doc.body}
                </pre>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
