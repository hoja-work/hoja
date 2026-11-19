/* A project's repository: where its agent tasks are worked on (HOJA-602).
 *
 * Empty means tasks in the project run in a scratch folder (or in the agent's own repo grant).
 * Set, each delegated task works in a worktree of it on its own branch, agent/<task>. Only an
 * admin can set it (the server refuses anyone else), because it decides where agents write. */
import { useEffect, useState } from "react";

import { getAgentsAdapter } from "./adapter";

export function ProjectRepository({ projectId }: { projectId: string }) {
  const [value, setValue] = useState<string | null | undefined>(undefined);
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    getAgentsAdapter()
      .getProjectRepository(projectId)
      .then((repo) => live && setValue(repo))
      .catch(() => live && setValue(null));
    return () => {
      live = false;
    };
  }, [projectId]);

  const save = async (next: string | null) => {
    setBusy(true);
    setError("");
    try {
      setValue(await getAgentsAdapter().setProjectRepository(projectId, next));
      setEditing(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="grid items-start gap-2 grid-cols-2"
      data-project-repository={value ?? ""}
    >
      <span className="text-muted">Repository</span>
      <div className="flex min-w-0 flex-col gap-1">
        {editing ? (
          <form
            className="flex flex-col gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              void save(draft.trim() || null);
            }}
          >
            <input
              className="min-w-0 rounded-md border border-line bg-panel px-2 py-1 text-xs text-foreground"
              aria-label="Project repository"
              placeholder="owner/name, git URL or path"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              autoFocus
            />
            <span className="flex gap-2">
              <button
                type="submit"
                className="text-xs font-medium text-foreground underline"
                disabled={busy}
              >
                {busy ? "Saving…" : "Save"}
              </button>
              <button
                type="button"
                className="text-xs text-muted"
                onClick={() => setEditing(false)}
              >
                Cancel
              </button>
            </span>
          </form>
        ) : (
          <button
            type="button"
            className="truncate text-left text-xs font-medium text-foreground hover:underline"
            onClick={() => {
              setDraft(value ?? "");
              setEditing(true);
            }}
          >
            {value === undefined
              ? "…"
              : value || "None: agent tasks use a scratch folder"}
          </button>
        )}
        {value && !editing ? (
          <span className="text-xs text-muted">
            Agent tasks here work on their own branch, agent/&lt;task&gt;.
          </span>
        ) : null}
        {error && (
          <span className="text-xs text-danger" role="alert">
            {error}
          </span>
        )}
      </div>
    </div>
  );
}
