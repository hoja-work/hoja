/* A project's repository: where its agent tasks are worked on (HOJA-602).
 *
 * Empty means tasks in the project run in a scratch folder (or in the agent's own repo grant).
 * Set, each delegated task works in a worktree of it on its own branch, agent/<task>. Only an
 * admin can set it (the server refuses anyone else), because it decides where agents write. */
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, GitBranch, Search, X } from "lucide-react";

import { Popover } from "../menu";
import { getAgentsAdapter, type ProjectRepositoryChoice } from "./adapter";

const SOURCE_LABEL: Record<string, string> = {
  project: "Project",
  github_link: "Linked",
  github: "GitHub",
};

export function ProjectRepository({ projectId }: { projectId: string }) {
  const [value, setValue] = useState<string | null | undefined>(undefined);
  const [choices, setChoices] = useState<ProjectRepositoryChoice[]>([]);
  const [loadingChoices, setLoadingChoices] = useState(false);
  const [draft, setDraft] = useState("");
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const panelId = useId();

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

  useEffect(() => {
    if (!open) return;
    let live = true;
    setLoadingChoices(true);
    getAgentsAdapter()
      .listProjectRepositoryChoices()
      .then((rows) => {
        if (live) setChoices(rows);
      })
      .catch(() => {
        if (live) setChoices([]);
      })
      .finally(() => {
        if (live) setLoadingChoices(false);
      });
    const frame = requestAnimationFrame(() => {
      inputRef.current?.focus();
    });
    return () => {
      live = false;
      cancelAnimationFrame(frame);
    };
  }, [open]);

  const filteredChoices = useMemo(() => {
    const q = draft.trim().toLowerCase();
    if (!q) return choices;
    return choices.filter(
      (c) =>
        c.fullName.toLowerCase().includes(q) ||
        (c.url || "").toLowerCase().includes(q),
    );
  }, [choices, draft]);

  const trimmedDraft = draft.trim();
  const hasExactMatch = useMemo(
    () =>
      Boolean(trimmedDraft) &&
      choices.some(
        (c) => c.fullName.toLowerCase() === trimmedDraft.toLowerCase(),
      ),
    [choices, trimmedDraft],
  );

  const save = async (next: string | null) => {
    setBusy(true);
    setError("");
    try {
      const updated = await getAgentsAdapter().setProjectRepository(
        projectId,
        next,
      );
      setValue(updated);
      setOpen(false);
      setDraft("");
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
        <div className="flex min-w-0 items-center gap-1.5">
          <Popover
            className="min-w-0 flex-1"
            open={open}
            onOpenChange={(next) => {
              setOpen(next);
              if (next) {
                setDraft("");
                setError("");
              }
            }}
            panelClassName="w-72 p-2"
            panelId={panelId}
            panelRole="dialog"
            trigger={(isOpen) => (
              <button
                type="button"
                className="flex w-full min-w-0 items-center justify-between gap-1.5 rounded-md border border-transparent px-1.5 py-0.5 text-left text-xs font-medium text-foreground hover:border-line hover:bg-subtle focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2"
                aria-haspopup="dialog"
                aria-expanded={isOpen}
                aria-controls={panelId}
                disabled={busy}
              >
                <span className="flex min-w-0 items-center gap-1.5 truncate">
                  {value ? (
                    <GitBranch
                      size={12}
                      className="shrink-0 text-muted"
                      aria-hidden="true"
                    />
                  ) : null}
                  <span className="truncate">
                    {value === undefined
                      ? "…"
                      : value || "None: agent tasks use a scratch folder"}
                  </span>
                </span>
                <ChevronDown
                  size={12}
                  className="shrink-0 text-muted"
                  aria-hidden="true"
                />
              </button>
            )}
          >
            <form
              className="flex flex-col gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (!trimmedDraft || busy) return;
                void save(trimmedDraft);
              }}
            >
              <div className="flex items-center gap-1.5 rounded-md border border-line bg-bg px-2 py-1 focus-within:border-accent">
                <Search
                  size={12}
                  className="shrink-0 text-muted"
                  aria-hidden="true"
                />
                <input
                  ref={inputRef}
                  className="min-w-0 flex-1 bg-transparent text-xs text-foreground outline-none placeholder:text-muted"
                  aria-label="Project repository"
                  placeholder="Search or enter owner/name, URL, path"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  disabled={busy}
                />
                {draft ? (
                  <button
                    type="button"
                    className="rounded p-0.5 text-muted hover:text-foreground"
                    aria-label="Clear search"
                    onClick={() => setDraft("")}
                  >
                    <X size={12} aria-hidden="true" />
                  </button>
                ) : null}
              </div>

              <div
                className="flex max-h-52 flex-col gap-0.5 overflow-y-auto"
                role="listbox"
                aria-label="Available repositories"
              >
                {filteredChoices.map((choice) => {
                  const selected =
                    value !== null &&
                    value !== undefined &&
                    value.toLowerCase() === choice.fullName.toLowerCase();
                  return (
                    <button
                      key={choice.fullName}
                      type="button"
                      role="option"
                      aria-selected={selected}
                      disabled={busy}
                      className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-xs text-foreground hover:bg-subtle disabled:opacity-50"
                      onClick={() => void save(choice.fullName)}
                    >
                      <span className="flex min-w-0 items-center gap-1.5 truncate">
                        <GitBranch
                          size={12}
                          className="shrink-0 text-muted"
                          aria-hidden="true"
                        />
                        <span className="truncate font-medium">
                          {choice.fullName}
                        </span>
                      </span>
                      <span className="flex shrink-0 items-center gap-1.5">
                        <span className="rounded bg-subtle px-1.5 py-0.5 text-xs text-muted">
                          {SOURCE_LABEL[choice.source] || choice.source}
                        </span>
                        {selected ? (
                          <Check
                            size={12}
                            className="text-foreground"
                            aria-hidden="true"
                          />
                        ) : null}
                      </span>
                    </button>
                  );
                })}

                {trimmedDraft && !hasExactMatch ? (
                  <button
                    type="submit"
                    disabled={busy}
                    className="flex w-full items-center justify-between gap-2 rounded-md border border-dashed border-line px-2 py-1.5 text-left text-xs text-foreground hover:bg-subtle disabled:opacity-50"
                  >
                    <span className="truncate">
                      Use{" "}
                      <span className="font-medium">
                        &ldquo;{trimmedDraft}&rdquo;
                      </span>
                    </span>
                    <span className="shrink-0 text-xs text-muted">Custom</span>
                  </button>
                ) : null}

                {!loadingChoices &&
                !filteredChoices.length &&
                !trimmedDraft ? (
                  <p className="px-2 py-1.5 text-xs text-muted">
                    Type owner/name, a git URL, or a local path and press Enter.
                  </p>
                ) : null}

                {loadingChoices && !choices.length ? (
                  <p className="px-2 py-1.5 text-xs text-muted">
                    Loading repositories…
                  </p>
                ) : null}
              </div>

              <div className="flex items-center justify-between border-t border-line pt-1.5">
                {value ? (
                  <button
                    type="button"
                    disabled={busy}
                    className="text-xs text-muted hover:text-danger disabled:opacity-50"
                    onClick={() => void save(null)}
                  >
                    {busy ? "Clearing…" : "Clear"}
                  </button>
                ) : (
                  <span className="text-xs text-muted">
                    owner/name, URL, or path
                  </span>
                )}
                {trimmedDraft ? (
                  <button
                    type="submit"
                    disabled={busy}
                    className="rounded-md bg-accent px-2 py-1 text-xs font-medium text-panel disabled:opacity-50"
                  >
                    {busy ? "Saving…" : "Save"}
                  </button>
                ) : null}
              </div>
            </form>
          </Popover>

          {value ? (
            <button
              type="button"
              disabled={busy}
              className="shrink-0 rounded px-1.5 py-0.5 text-xs text-muted hover:bg-subtle hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2 disabled:opacity-50"
              aria-label="Clear project repository"
              onClick={() => void save(null)}
            >
              Clear
            </button>
          ) : null}
        </div>
        {value ? (
          <span className="text-xs text-muted">
            Agent tasks here work on their own branch, agent/&lt;task&gt;.
          </span>
        ) : null}
        {error ? (
          <span className="text-xs text-danger" role="alert">
            {error}
          </span>
        ) : null}
      </div>
    </div>
  );
}
