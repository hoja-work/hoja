import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../components/ui/popover";
import { useCallback, useEffect, useState } from "react";
import {
  Calendar as CalendarIcon,
  Check,
  ChevronRight,
  Phone,
  Plus,
  Video,
} from "lucide-react";
import {
  fmtWhen,
  meetingsApi,
  type Meeting,
  type Person,
  fmtDuration,
} from "./api";
import { Calendar } from "./Calendar";
import { useInbox } from "./inbox";
import { MeetingDrawer } from "./MeetingDrawer";
import { PeoplePicker, ScheduleDialog } from "./ScheduleDialog";
import "./meetings-page.css";

type Tab = "calendar" | "upcoming" | "past";
type Viewer = { id?: string; name?: string; email?: string; admin?: boolean };

const CODE = /[a-z]{3}-[a-z]{4}-[a-z]{4}/i;

function MeetingRow({
  meeting,
  onOpen,
}: {
  meeting: Meeting;
  onOpen: () => void;
}) {
  const pending = meeting.myResponse === "needsAction" && !meeting.isHost;
  return (
    <li
      className="flex items-center gap-2 pt-0 pr-3 pb-0 pl-0 border-b border-b-line last:border-b-0"
      data-fidelity-state="mt-row"
    >
      <button
        type="button"
        className="flex items-center gap-2.5 flex-1 min-w-0 py-2.75 px-3 bg-transparent text-foreground text-left cursor-pointer [&:hover]:bg-subtle shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
        data-fidelity-state="mt-row-main"
        onClick={onOpen}
      >
        <span
          className="flex-none py-0.5 px-1.75 rounded-[9px] bg-subtle text-muted text-[11px] font-semibold capitalize data-[fidelity-state~=mt-status-live]:bg-[#16a34a22] data-[fidelity-state~=mt-status-live]:text-[#16a34a] data-[fidelity-state~=mt-status-scheduled]:bg-accent-soft data-[fidelity-state~=mt-status-scheduled]:text-accent data-[fidelity-state~=mt-status-cancelled]:bg-[#ef444422] data-[fidelity-state~=mt-status-cancelled]:text-danger"
          data-fidelity-state={`mt-status mt-status-${meeting.status}`}
        >
          {meeting.status === "live" ? "Live" : meeting.status}
        </span>
        <span
          className="grid flex-1 gap-0.5 min-w-0"
          data-fidelity-state="mt-row-text"
        >
          <b className="overflow-hidden text-ellipsis whitespace-nowrap">
            {meeting.title}
          </b>
          <small className="overflow-hidden text-ellipsis whitespace-nowrap text-muted">
            {meeting.status === "ended"
              ? `Ended${meeting.durationSeconds ? ` · lasted ${fmtDuration(meeting.durationSeconds)}` : ""} · `
              : ""}
            {fmtWhen(meeting)}
            {meeting.hostName
              ? ` · ${meeting.isHost ? "You" : meeting.hostName}`
              : ""}
            {pending ? " · awaiting your reply" : ""}
          </small>
        </span>
        <ChevronRight size={14} aria-hidden="true" />
      </button>
      {(meeting.status === "live" || meeting.status === "scheduled") && (
        <a
          className="inline-flex items-center justify-center gap-1.5 min-h-7 py-1 px-2.25 border border-solid border-line rounded-md bg-panel text-foreground text-[12px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default"
          data-fidelity-state="mt-btn mt-btn-small"
          href={`/meet/${meeting.code}`}
        >
          {meeting.isHost && meeting.status !== "live" ? "Start" : "Join"}
        </a>
      )}
    </li>
  );
}

function Invitations({
  viewer: _viewer,
  onOpen,
  onChanged,
}: {
  viewer: Viewer;
  onOpen: (m: Meeting) => void;
  onChanged: () => void;
}) {
  const invitations = useInbox((s) => s.invitations);
  const removeInvitation = useInbox((s) => s.removeInvitation);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const answer = async (
    m: Meeting,
    response: "accepted" | "tentative" | "declined",
  ) => {
    setBusy(m.code);
    setError("");
    try {
      await meetingsApi.rsvp(m.code, response);
      removeInvitation(m.code);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not send your reply.");
    } finally {
      setBusy("");
    }
  };

  if (!invitations.length) return null;
  return (
    <section
      className="py-3 px-3.5 border border-solid border-line rounded-[9px] bg-panel"
      data-fidelity-state="mt-invites"
      aria-label="Invitations"
    >
      <h2 className="flex items-center gap-2 mt-0 mx-0 mb-2 text-[13px]">
        Invitations{" "}
        <span
          className="inline-block min-w-4.5 py-0 px-1.5 rounded-[9px] bg-accent-soft text-accent text-[11px] font-semibold text-center"
          data-fidelity-state="mt-count"
        >
          {invitations.length}
        </span>
      </h2>
      {error && (
        <p
          className="mt-1.5 mx-0 mb-0 text-danger text-[12px]"
          data-fidelity-state="mt-field-error"
          role="alert"
        >
          {error}
        </p>
      )}
      <ul className="grid gap-1.5 m-0 p-0 list-none">
        {invitations.map((m) => (
          <li
            key={m.code}
            className="flex flex-wrap items-center justify-between gap-2 py-2 px-2.5 rounded-[7px] bg-subtle"
            data-fidelity-state="mt-invite"
          >
            <button
              type="button"
              className="grid gap-0.5 flex-[1_1_200px] min-w-0 p-0 bg-transparent text-left cursor-pointer shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
              data-fidelity-state="mt-invite-info"
              onClick={() => onOpen(m)}
            >
              <b className="overflow-hidden text-ellipsis whitespace-nowrap">
                {m.title}
              </b>
              <small className="overflow-hidden text-ellipsis whitespace-nowrap text-muted">
                {m.invitedBy ? `${m.invitedBy} · ` : ""}
                {fmtWhen(m)}
              </small>
            </button>
            <div
              className="flex flex-wrap gap-1.5"
              data-fidelity-state="mt-invite-actions"
            >
              <button
                type="button"
                className="inline-flex items-center justify-center gap-1.5 min-h-7 py-1 px-2.25 border border-solid border-line rounded-md bg-accent text-white text-[12px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-accent disabled:opacity-55 disabled:cursor-default border-accent [&:hover]:filter-[brightness(1.1)] [:root[data-theme=dark]_&]:bg-accent [:root[data-theme=dark]_&]:text-[#171719] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                data-fidelity-state="mt-btn mt-btn-small mt-btn-primary"
                disabled={busy === m.code}
                onClick={() => void answer(m, "accepted")}
              >
                <Check size={13} aria-hidden="true" />
                Accept
              </button>
              <button
                type="button"
                className="inline-flex items-center justify-center gap-1.5 min-h-7 py-1 px-2.25 border border-solid border-line rounded-md bg-panel text-foreground text-[12px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                data-fidelity-state="mt-btn mt-btn-small"
                disabled={busy === m.code}
                onClick={() => void answer(m, "tentative")}
              >
                Maybe
              </button>
              <button
                type="button"
                className="inline-flex items-center justify-center gap-1.5 min-h-7 py-1 px-2.25 border border-solid border-line rounded-md bg-panel text-foreground text-[12px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                data-fidelity-state="mt-btn mt-btn-small"
                disabled={busy === m.code}
                onClick={() => void answer(m, "declined")}
              >
                Decline
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function CallPopover({
  viewer,
  onClose,
}: {
  viewer: Viewer;
  onClose: () => void;
}) {
  const [people, setPeople] = useState<Person[]>([]);
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void meetingsApi
      .people()
      .then(setPeople)
      .catch(() => setPeople([]));
  }, []);

  const call = async () => {
    setBusy(true);
    setError("");
    try {
      const { meeting } = await meetingsApi.create({
        title:
          picked.length === 1
            ? `Call with ${people.find((p) => p.id === picked[0])?.name || "a colleague"}`
            : `${viewer.name || "Group"} call`,
        waitingRoom: false,
        joinBeforeHost: true,
        access: "invited",
        invitees: picked,
      });
      await meetingsApi.ring(meeting.code, picked);
      location.assign(`/meet/${meeting.code}?call=1`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not place the call.");
      setBusy(false);
    }
  };

  return (
    <PopoverContent
      align="start"
      sideOffset={6}
      className="grid gap-2.5 w-[min(340px,calc(100vw-32px))] p-3 border border-solid border-line rounded-[9px] bg-panel shadow-(--shadow)"
      data-fidelity-state="mt-popover"
      role="dialog"
      aria-label="Call someone"
    >
      <p className="m-0 font-semibold" data-fidelity-state="mt-popover-title">
        Call someone
      </p>
      <PeoplePicker
        people={people}
        value={picked}
        onChange={setPicked}
        excludeId={viewer.id}
        autoFocus
      />
      {error && (
        <p
          className="mt-1.5 mx-0 mb-0 text-danger text-[12px]"
          data-fidelity-state="mt-field-error"
          role="alert"
        >
          {error}
        </p>
      )}
      <div
        className="flex justify-end gap-2"
        data-fidelity-state="mt-popover-foot"
      >
        <button
          type="button"
          className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-panel text-foreground text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          data-fidelity-state="mt-btn"
          onClick={onClose}
        >
          Close
        </button>
        <button
          type="button"
          className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-accent text-white text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-accent disabled:opacity-55 disabled:cursor-default border-accent [&:hover]:filter-[brightness(1.1)] [:root[data-theme=dark]_&]:bg-accent [:root[data-theme=dark]_&]:text-[#171719] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          data-fidelity-state="mt-btn mt-btn-primary"
          disabled={!picked.length || busy}
          onClick={() => void call()}
        >
          <Phone size={14} aria-hidden="true" />
          {busy ? "Calling…" : "Call"}
        </button>
      </div>
    </PopoverContent>
  );
}

export function MeetingsPage({
  viewer,
}: {
  viewer: Viewer;
  onNavigate?: (view: string, path?: string) => void;
}) {
  const [tab, setTab] = useState<Tab>("calendar");
  const [list, setList] = useState<Meeting[]>([]);
  const [listError, setListError] = useState("");
  const [listLoading, setListLoading] = useState(false);
  const [open, setOpen] = useState<Meeting | null>(null);
  const [scheduling, setScheduling] = useState<{ day?: Date } | null>(null);
  const [calling, setCalling] = useState(false);
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState("");
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState("");
  const version = useInbox((s) => s.version);
  const [local, setLocal] = useState(0);
  const refresh = useCallback(() => {
    setLocal((n) => n + 1);
    useInbox.setState((s) => ({ version: s.version + 1 }));
  }, []);
  const reload = version + local;

  useEffect(() => {
    let live = true;
    void meetingsApi
      .invitations()
      .then((rows) => {
        if (live) useInbox.setState({ invitations: rows });
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [reload]);

  useEffect(() => {
    if (tab === "calendar") return;
    let live = true;
    setListLoading(true);
    meetingsApi
      .list(tab)
      .then((rows) => {
        if (live) {
          setList(rows);
          setListError("");
        }
      })
      .catch((e) => {
        if (live)
          setListError(
            e instanceof Error ? e.message : "Could not load meetings.",
          );
      })
      .finally(() => {
        if (live) setListLoading(false);
      });
    return () => {
      live = false;
    };
  }, [tab, reload]);

  const startNow = async () => {
    setStarting(true);
    setStartError("");
    try {
      const { meeting } = await meetingsApi.create({
        title: `${viewer.name || "My"} meeting`,
        waitingRoom: false,
        access: "invited",
      });
      location.assign(`/meet/${meeting.code}`);
    } catch (e) {
      setStartError(
        e instanceof Error ? e.message : "Could not start a meeting.",
      );
      setStarting(false);
    }
  };

  const joinWithCode = (e: React.FormEvent) => {
    e.preventDefault();
    const match = code.match(CODE);
    if (!match) {
      setCodeError("Enter a code like abc-defg-hij or paste a meeting link.");
      return;
    }
    location.assign(`/meet/${match[0].toLowerCase()}`);
  };

  return (
    <div
      data-mt-page
      className="grid gap-4.5 p-4 w-full max-w-280 my-0 mx-auto text-[13px]"
      data-fidelity-state="mt-page"
    >
      <header
        className="flex flex-wrap items-start justify-between gap-3.5"
        data-fidelity-state="mt-page-head"
      >
        <div>
          <h1 className="mt-0 mx-0 mb-1 text-[22px] tracking-[-.03em] shell-phone:text-[20px]">
            Meetings
          </h1>
          <p className="m-0 text-muted" data-fidelity-state="mt-muted">
            Video calls with your team, guests and customers.
          </p>
        </div>
        <div
          className="flex flex-wrap items-center gap-2"
          data-fidelity-state="mt-head-actions"
        >
          <button
            type="button"
            className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-accent text-white text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-accent disabled:opacity-55 disabled:cursor-default border-accent [&:hover]:filter-[brightness(1.1)] [:root[data-theme=dark]_&]:bg-accent [:root[data-theme=dark]_&]:text-[#171719] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            data-fidelity-state="mt-btn mt-btn-primary"
            disabled={starting}
            onClick={() => void startNow()}
          >
            <Video size={14} aria-hidden="true" />
            {starting ? "Starting…" : "New meeting"}
          </button>
          <button
            type="button"
            className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-panel text-foreground text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            data-fidelity-state="mt-btn"
            onClick={() => setScheduling({})}
          >
            <Plus size={14} aria-hidden="true" />
            Schedule
          </button>
          <Popover open={calling} onOpenChange={setCalling}>
            <PopoverTrigger asChild>
              <button
                type="button"
                className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-panel text-foreground text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                data-fidelity-state="mt-btn"
                aria-expanded={calling}
              >
                <Phone size={14} aria-hidden="true" />
                Call someone
              </button>
            </PopoverTrigger>

            <CallPopover viewer={viewer} onClose={() => setCalling(false)} />
          </Popover>
        </div>
      </header>
      {startError && (
        <p
          className="mt-1.5 mx-0 mb-0 text-danger text-[12px]"
          data-fidelity-state="mt-field-error"
          role="alert"
        >
          {startError}
        </p>
      )}

      <form
        className="flex flex-wrap items-center gap-2"
        data-fidelity-state="mt-join"
        onSubmit={joinWithCode}
      >
        <label
          className="flex-[1_1_220px] max-w-95"
          data-fidelity-state="mt-join-field"
        >
          <span
            className="absolute w-px h-px overflow-hidden [clip:rect(0_0_0_0)] whitespace-nowrap"
            data-fidelity-state="mt-sr"
          >
            Meeting code or link
          </span>
          <input
            className="w-full min-h-8 py-1.5 px-2.5 border border-solid border-line rounded-md bg-bg text-foreground text-[13px] shell-phone:min-h-11 pointer-coarse:min-h-11"
            value={code}
            onChange={(e) => {
              setCode(e.target.value);
              setCodeError("");
            }}
            placeholder="Enter a code or meeting link"
            aria-invalid={!!codeError}
          />
        </label>
        <button
          type="submit"
          className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-panel text-foreground text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
          data-fidelity-state="mt-btn"
          disabled={!code.trim()}
        >
          Join
        </button>
        {codeError && (
          <p
            className="m-0 text-danger text-[12px] basis-full"
            data-fidelity-state="mt-field-error"
            role="alert"
          >
            {codeError}
          </p>
        )}
      </form>

      <Invitations viewer={viewer} onOpen={setOpen} onChanged={refresh} />

      <div
        className="flex gap-0.5 border-b border-b-line"
        data-fidelity-state="mt-tabs"
        role="tablist"
        aria-label="Meeting views"
      >
        {(
          [
            ["calendar", "Calendar"],
            ["upcoming", "Upcoming"],
            ["past", "Past"],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className="inline-flex items-center gap-1.5 py-2 px-3 border-b-2 border-b-transparent bg-transparent text-muted font-medium cursor-pointer data-[fidelity-state~=mt-on]:border-b-accent data-[fidelity-state~=mt-on]:text-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            data-fidelity-state={tab === id ? "mt-on" : ""}
            onClick={() => setTab(id)}
          >
            {id === "calendar" && <CalendarIcon size={13} aria-hidden="true" />}
            {label}
          </button>
        ))}
      </div>

      {tab === "calendar" ? (
        <Calendar
          version={reload}
          onOpen={setOpen}
          onCreateOn={(day) => setScheduling({ day })}
        />
      ) : (
        <section
          className=""
          data-fidelity-state="mt-list"
          aria-label={
            tab === "upcoming" ? "Upcoming meetings" : "Past meetings"
          }
        >
          {listError && (
            <p
              className="mt-1.5 mx-0 mb-0 text-danger text-[12px]"
              data-fidelity-state="mt-field-error"
              role="alert"
            >
              {listError}
            </p>
          )}
          {listLoading && !list.length && (
            <p
              className="text-muted"
              data-fidelity-state="mt-muted"
              role="status"
            >
              Loading…
            </p>
          )}
          {!listLoading && !listError && !list.length && (
            <div
              className="grid justify-items-center gap-2 py-10 px-4 border border-dashed border-line rounded-[9px] text-muted text-center"
              data-fidelity-state="mt-empty"
            >
              <CalendarIcon size={22} aria-hidden="true" />
              <p className="m-0">
                {tab === "upcoming"
                  ? "No upcoming meetings."
                  : "No past meetings yet."}
              </p>
              {tab === "upcoming" && (
                <button
                  type="button"
                  className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-panel text-foreground text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-fidelity-state="mt-btn"
                  onClick={() => setScheduling({})}
                >
                  Schedule one
                </button>
              )}
            </div>
          )}
          <ul className="grid m-0 p-0 border border-solid border-line rounded-[9px] bg-panel list-none overflow-hidden empty:hidden">
            {list.map((m) => (
              <MeetingRow key={m.id} meeting={m} onOpen={() => setOpen(m)} />
            ))}
          </ul>
        </section>
      )}

      {open && (
        <MeetingDrawer
          key={open.code}
          meeting={open}
          viewer={viewer}
          onClose={() => setOpen(null)}
          onChanged={refresh}
        />
      )}
      {scheduling && (
        <ScheduleDialog
          viewerId={viewer.id}
          initialDate={scheduling.day}
          onClose={() => setScheduling(null)}
          onSaved={refresh}
        />
      )}
    </div>
  );
}
