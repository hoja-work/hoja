/* Project members: who is working on a project.
 *
 * Members follow the project (its updates, replies and status changes reach their inbox),
 * and anyone added is told so in their inbox. The list is replaced as a whole through
 * `projectUpdate(memberIds)`; each toggle is applied at once and rolled back if refused. */
import { useEffect, useState } from "react";
import { Check, UserPlus } from "lucide-react";
import { gql } from "./main";
import { errorText } from "./localFirst";
import { Avatar } from "./graph/shared";
import {
  Popover as UIPopover,
  PopoverContent,
  PopoverTrigger,
} from "./components/ui/popover";

type Person = { id: string; name: string; avatarSeed?: string | null };

export function ProjectMembers({
  projectId,
  people,
}: {
  projectId: string;
  /** Everyone who can be added: the workspace's people. */
  people: Person[];
}) {
  const [members, setMembers] = useState<Person[] | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    gql(`query($id:String!){project(id:$id){id members{nodes{id name avatarSeed}}}}`, {
      id: projectId,
    })
      .then((d) => {
        if (live) setMembers(d.project?.members?.nodes || []);
      })
      .catch((e) => {
        if (live) setError(errorText(e));
      });
    return () => {
      live = false;
    };
  }, [projectId]);

  const toggle = async (person: Person) => {
    if (!members) return;
    const before = members;
    const isMember = members.some((m) => m.id === person.id);
    const next = isMember
      ? members.filter((m) => m.id !== person.id)
      : [...members, person];
    setMembers(next);
    setError("");
    try {
      const d = await gql(
        `mutation($id:String!,$input:ProjectUpdateInput!){projectUpdate(id:$id,input:$input){success}}`,
        { id: projectId, input: { memberIds: next.map((m) => m.id) } },
      );
      if (!d.projectUpdate?.success)
        throw Error(
          "Only the project's creator, lead or an admin can change members.",
        );
    } catch (e) {
      setMembers(before);
      setError(`Couldn’t update members: ${errorText(e)}`);
    }
  };

  const q = query.trim().toLowerCase();
  const choices = people.filter((p) => !q || p.name.toLowerCase().includes(q));

  return (
    <div className="flex min-w-0 flex-col gap-1" data-project-members="">
      <div className="flex min-w-0 flex-wrap items-center gap-1">
        {members === null && !error && (
          <span className="text-xs text-muted">Loading…</span>
        )}
        {members?.length === 0 && (
          <span className="text-xs text-muted">No members</span>
        )}
        {members?.map((m) => (
          <Avatar key={m.id} name={m.name} seed={m.avatarSeed} size={20} title={m.name} />
        ))}
        <UIPopover open={open} onOpenChange={setOpen}>
          <PopoverTrigger asChild>
            <button
              type="button"
              className="grid size-6 place-items-center rounded-md bg-transparent text-muted hover:bg-subtle hover:text-foreground focus-visible:outline-2 focus-visible:outline-accent shell-phone:size-11 pointer-coarse:size-11"
              aria-label="Add or remove members"
              aria-haspopup="listbox"
              aria-expanded={open}
              disabled={members === null}
            >
              <UserPlus size={14} />
            </button>
          </PopoverTrigger>
          <PopoverContent
            align="start"
            sideOffset={6}
            className="z-50 flex w-64 flex-col gap-1 rounded-lg border border-line bg-panel p-1 shadow-(--shadow)"
            data-project-members-menu=""
          >
            <input
              className="rounded-md border border-line bg-bg px-2 py-1 text-sm"
              placeholder="Search people…"
              aria-label="Search people"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <ul
              className="m-0 flex max-h-64 list-none flex-col overflow-y-auto p-0"
              role="listbox"
              aria-multiselectable="true"
              aria-label="Members"
            >
              {choices.map((p) => {
                const selected = !!members?.some((m) => m.id === p.id);
                return (
                  <li key={p.id} role="option" aria-selected={selected}>
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 rounded-md bg-transparent px-2 py-1.5 text-left text-sm hover:bg-subtle"
                      onClick={() => void toggle(p)}
                    >
                      <Avatar name={p.name} size={18} />
                      <span className="min-w-0 flex-1 truncate">{p.name}</span>
                      {selected && <Check size={14} className="text-muted" />}
                    </button>
                  </li>
                );
              })}
              {!choices.length && (
                <li className="px-2 py-1.5 text-xs text-muted">
                  No one matches
                </li>
              )}
            </ul>
            <p className="m-0 px-2 pb-1 text-xs text-muted">
              Members follow the project, and anyone you add is told in their
              inbox.
            </p>
          </PopoverContent>
        </UIPopover>
      </div>
      {error && (
        <p role="alert" className="m-0 text-xs text-muted">
          {error}
        </p>
      )}
    </div>
  );
}
