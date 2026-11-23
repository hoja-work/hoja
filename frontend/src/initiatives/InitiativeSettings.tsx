/* Settings > Initiatives: the workspace update schedule, initiative labels and label
 * groups, and which teams list Initiatives in their sidebar. */
import React, { useEffect, useState } from "react";
import { Trash2 } from "lucide-react";
import type { Viewer } from "../main";
import { IconBtn } from "../graph/shared";
import { DetailButton } from "../graph/detail";
import {
  archiveLabel,
  createLabel,
  fetchLabels,
  fetchReminderPolicy,
  policyLabel,
  saveReminderPolicy,
  setTeamShowsInitiatives,
  updateLabel,
  WEEKDAYS,
  type InitiativeLabel,
  type ReminderPolicy,
} from "./api";
import { LabelDot, refreshInitiativeLabels } from "./fields";

type TeamRow = {
  id: string;
  name: string;
  key: string;
  showInitiatives?: boolean;
};

const field =
  "rounded-md border border-line bg-bg px-2 py-1.5 text-sm text-foreground disabled:opacity-60 pointer-coarse:min-h-11";

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className="grid gap-4 border-b border-line py-6"
      aria-label={title}
    >
      <div className="grid gap-1">
        <h2 className="m-0 text-base font-semibold">{title}</h2>
        <p className="m-0 text-sm text-muted">{description}</p>
      </div>
      {children}
    </section>
  );
}

export function InitiativeSettings({
  viewer,
  teams,
}: {
  viewer: Viewer;
  teams: TeamRow[];
}) {
  const [policy, setPolicy] = useState<ReminderPolicy | null>(null);
  const [labels, setLabels] = useState<InitiativeLabel[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [shown, setShown] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(teams.map((t) => [t.id, Boolean(t.showInitiatives)])),
  );
  const [newGroup, setNewGroup] = useState("");
  const [newLabel, setNewLabel] = useState("");
  const [newLabelGroup, setNewLabelGroup] = useState("");
  const admin = Boolean(viewer.admin);

  const reloadLabels = () =>
    fetchLabels()
      .then((rows) => {
        setLabels(rows);
        void refreshInitiativeLabels();
      })
      .catch((e: Error) => setError(e.message));
  useEffect(() => {
    fetchReminderPolicy()
      .then(setPolicy)
      .catch((e: Error) => setError(e.message));
    void reloadLabels();
  }, []);

  const run = async (fn: () => Promise<unknown>, done?: string) => {
    setError("");
    setNotice("");
    try {
      await fn();
      if (done) setNotice(done);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const savePolicy = (patch: Partial<ReminderPolicy>) =>
    run(
      async () => setPolicy(await saveReminderPolicy(patch)),
      "Schedule saved.",
    );

  const groups = labels.filter((l) => l.isGroup);
  const ungrouped = labels.filter((l) => !l.isGroup && !l.parent);

  return (
    <section
      className="mx-auto w-full max-w-3xl p-4 text-foreground"
      data-settings-page=""
      data-settings-pane="initiatives"
    >
      <header className="grid gap-2 pb-2">
        <h1 className="m-0 text-2xl font-semibold">Initiatives</h1>
        <p className="m-0 text-sm text-muted">
          How initiative owners are reminded to post updates, the labels
          initiatives can carry, and which teams show initiatives in their
          sidebar.
        </p>
      </header>
      {error && (
        <p
          className="m-0 rounded-md border border-line p-2 text-sm text-danger"
          role="alert"
        >
          {error}
        </p>
      )}
      {notice && (
        <p className="m-0 text-sm text-muted" role="status">
          {notice}
        </p>
      )}

      <Section
        title="Update reminders"
        description="Owners of active initiatives are reminded on this schedule, in their own timezone, when they have not posted since the last reminder. Follow-ups go out one working day later and two working days after that. An initiative can use its own cadence or opt out."
      >
        {policy ? (
          <div
            className="grid gap-3 md:grid-cols-2"
            data-initiative-schedule=""
          >
            <label className="grid gap-1 text-sm">
              Cadence
              <select
                className={field}
                disabled={!admin}
                value={policy.cadence}
                onChange={(e) => void savePolicy({ cadence: e.target.value })}
              >
                <option value="never">Never</option>
                <option value="weekly">Every week</option>
                <option value="biweekly">Every two weeks</option>
                <option value="monthly">Every month</option>
              </select>
            </label>
            <label className="grid gap-1 text-sm">
              Day
              <select
                className={field}
                disabled={!admin || policy.cadence === "never"}
                value={policy.weekday}
                onChange={(e) =>
                  void savePolicy({ weekday: Number(e.target.value) })
                }
              >
                {WEEKDAYS.map((d, i) => (
                  <option key={d} value={i}>
                    {d}
                  </option>
                ))}
              </select>
            </label>
            <label className="grid gap-1 text-sm">
              Time
              <select
                className={field}
                disabled={!admin || policy.cadence === "never"}
                value={policy.hour}
                onChange={(e) =>
                  void savePolicy({ hour: Number(e.target.value) })
                }
              >
                {Array.from({ length: 24 }, (_, h) => (
                  <option key={h} value={h}>
                    {String(h).padStart(2, "0")}:00
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-2 self-end text-sm pointer-coarse:min-h-11">
              <input
                className="pointer-coarse:size-5"
                type="checkbox"
                disabled={!admin}
                checked={policy.followUps}
                onChange={(e) =>
                  void savePolicy({ followUps: e.target.checked })
                }
              />
              Send follow-ups
            </label>
            <p className="m-0 text-xs text-muted md:col-span-2">
              {policyLabel(policy)}
              {!admin && " · Only workspace admins change the schedule."}
            </p>
          </div>
        ) : (
          <p className="m-0 text-sm text-muted" role="status">
            Loading schedule…
          </p>
        )}
      </Section>

      <Section
        title="Labels"
        description="Labels sort initiatives across teams. A label group holds related labels; by default an initiative takes one label from a group, and a group can allow several."
      >
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!newGroup.trim()) return;
            void run(async () => {
              await createLabel(newGroup.trim(), { isGroup: true });
              setNewGroup("");
              await reloadLabels();
            }, "Group created.");
          }}
        >
          <input
            className={`${field} min-w-0 flex-1 max-sm:basis-full`}
            aria-label="New label group"
            placeholder="New label group, for example Horizon"
            value={newGroup}
            onChange={(e) => setNewGroup(e.target.value)}
          />
          <DetailButton
            type="submit"
            className="pointer-coarse:min-h-11 max-sm:flex-1"
            disabled={!newGroup.trim()}
          >
            Add group
          </DetailButton>
        </form>
        <form
          className="flex flex-wrap gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (!newLabel.trim()) return;
            void run(async () => {
              await createLabel(newLabel.trim(), {
                parentId: newLabelGroup || null,
              });
              setNewLabel("");
              await reloadLabels();
            }, "Label created.");
          }}
        >
          <input
            className={`${field} min-w-0 flex-1 max-sm:basis-full`}
            aria-label="New label"
            placeholder="New label"
            value={newLabel}
            onChange={(e) => setNewLabel(e.target.value)}
          />
          <select
            className={`${field} max-sm:min-w-0 max-sm:flex-1`}
            aria-label="Group for the new label"
            value={newLabelGroup}
            onChange={(e) => setNewLabelGroup(e.target.value)}
          >
            <option value="">No group</option>
            {groups.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
          <DetailButton
            type="submit"
            className="pointer-coarse:min-h-11"
            disabled={!newLabel.trim()}
          >
            Add label
          </DetailButton>
        </form>
        <div className="grid gap-4" data-initiative-label-settings="">
          {groups.map((g) => (
            <div
              key={g.id}
              className="grid gap-2 rounded-lg border border-line p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="flex-1 font-medium">{g.name}</span>
                <label className="flex items-center gap-2 text-sm text-muted pointer-coarse:min-h-11">
                  <input
                    className="pointer-coarse:size-5"
                    type="checkbox"
                    checked={g.exclusive === false}
                    onChange={(e) =>
                      void run(async () => {
                        await updateLabel(g.id, {
                          exclusive: !e.target.checked,
                        });
                        await reloadLabels();
                      })
                    }
                  />
                  Allow several from this group
                </label>
                <IconBtn
                  label={`Archive group ${g.name}`}
                  title="Archive group"
                  onClick={() =>
                    void run(async () => {
                      await archiveLabel(g.id);
                      await reloadLabels();
                    })
                  }
                >
                  <Trash2 size={14} />
                </IconBtn>
              </div>
              <LabelRows
                rows={labels.filter((l) => l.parent?.id === g.id)}
                groups={groups}
                run={run}
                reload={reloadLabels}
              />
            </div>
          ))}
          <LabelRows
            rows={ungrouped}
            groups={groups}
            run={run}
            reload={reloadLabels}
          />
          {!labels.length && (
            <p className="m-0 text-sm text-muted">No initiative labels yet.</p>
          )}
        </div>
      </Section>

      <Section
        title="Team initiatives"
        description="A team that shows initiatives gets an Initiatives entry in its sidebar, listing the initiatives it leads and, on request, those its projects contribute to. Initiatives led by a private team are visible only to its members."
      >
        <ul
          className="m-0 grid list-none gap-2 p-0"
          data-initiative-team-settings=""
        >
          {teams.map((t) => (
            <li key={t.id}>
              <label className="flex items-center gap-2 text-sm pointer-coarse:min-h-11">
                <input
                  className="shrink-0 pointer-coarse:size-5"
                  type="checkbox"
                  checked={Boolean(shown[t.id])}
                  onChange={(e) => {
                    const show = e.target.checked;
                    setShown((cur) => ({ ...cur, [t.id]: show }));
                    void run(
                      () => setTeamShowsInitiatives(t.id, show),
                      `${t.name} ${show ? "shows" : "hides"} initiatives. Reload to update the sidebar.`,
                    );
                  }}
                />
                Show initiatives in the {t.name} sidebar
              </label>
            </li>
          ))}
        </ul>
      </Section>
    </section>
  );
}

function LabelRows({
  rows,
  groups,
  run,
  reload,
}: {
  rows: InitiativeLabel[];
  groups: InitiativeLabel[];
  run: (fn: () => Promise<unknown>, done?: string) => Promise<void>;
  reload: () => Promise<unknown>;
}) {
  if (!rows.length) return null;
  return (
    <ul className="m-0 grid list-none gap-1 p-0">
      {rows.map((l) => (
        <li key={l.id} className="flex flex-wrap items-center gap-2 text-sm">
          <LabelDot color={l.color} />
          <input
            className="min-w-0 flex-1 rounded-md border border-transparent bg-transparent px-1 py-0.5 hover:border-line focus:border-line pointer-coarse:min-h-11 pointer-coarse:border-line pointer-coarse:px-2"
            aria-label={`Rename ${l.name}`}
            defaultValue={l.name}
            onBlur={(e) => {
              const name = e.target.value.trim();
              if (name && name !== l.name)
                void run(async () => {
                  await updateLabel(l.id, { name });
                  await reload();
                });
            }}
          />
          {groups.length > 0 && (
            <select
              className="rounded-md border border-line bg-bg px-1.5 py-0.5 text-xs pointer-coarse:min-h-11"
              aria-label={`Group of ${l.name}`}
              value={l.parent?.id || ""}
              onChange={(e) =>
                void run(async () => {
                  await updateLabel(l.id, { parentId: e.target.value });
                  await reload();
                })
              }
            >
              <option value="">No group</option>
              {groups.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
            </select>
          )}
          <IconBtn
            label={`Archive ${l.name}`}
            title="Archive label"
            onClick={() =>
              void run(async () => {
                await archiveLabel(l.id);
                await reload();
              })
            }
          >
            <Trash2 size={13} />
          </IconBtn>
        </li>
      ))}
    </ul>
  );
}
