import { useEffect, useMemo, useRef, useState } from "react";
import { CalendarDays, Check, Copy, Search, X } from "lucide-react";
import {
  googleCalendarUrl,
  joinUrl,
  meetingsApi,
  type Access,
  type CreateInput,
  type Meeting,
  type Person,
} from "./api";
import "./meetings-page.css";

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const DURATIONS = [15, 30, 45, 60, 90, 120];

/** Chips + search over workspace members; with `allowEmail`, any valid address can be added too. */
export function PeoplePicker({
  people,
  value,
  onChange,
  excludeId,
  allowEmail,
  placeholder,
  autoFocus,
}: {
  people: Person[];
  value: string[];
  onChange: (next: string[]) => void;
  excludeId?: string;
  allowEmail?: boolean;
  placeholder?: string;
  autoFocus?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const byId = useMemo(() => new Map(people.map((p) => [p.id, p])), [people]);
  const needle = query.trim().toLowerCase();
  const matches = useMemo(
    () =>
      people
        .filter((p) => p.id !== excludeId && !value.includes(p.id))
        .filter(
          (p) =>
            !needle ||
            `${p.name} ${p.email || ""}`.toLowerCase().includes(needle),
        )
        .slice(0, 6),
    [people, value, excludeId, needle],
  );

  const add = (token: string) => {
    onChange([...value, token]);
    setQuery("");
    setError("");
  };
  const addEmail = () => {
    const email = query.trim().toLowerCase();
    if (!email) return;
    if (!EMAIL.test(email)) {
      setError("Enter a valid email address.");
      return;
    }
    if (value.includes(email)) {
      setQuery("");
      return;
    }
    add(email);
  };

  return (
    <div className="grid gap-1.5" data-fidelity-state="mt-people">
      {value.length > 0 && (
        <ul
          className="flex flex-wrap gap-1.25 m-0 p-0 list-none"
          data-fidelity-state="mt-chips"
          aria-label="Selected people"
        >
          {value.map((token) => (
            <li
              key={token}
              className="inline-flex items-center gap-1 max-w-full pt-0.5 pr-1 pb-0.5 pl-2.25 rounded-xl bg-accent-soft text-[12px]"
              data-fidelity-state="mt-chip"
            >
              <span
                className="overflow-hidden text-ellipsis whitespace-nowrap"
                data-fidelity-state="mt-chip-text"
              >
                {byId.get(token)?.name || token}
              </span>
              <button
                className="grid place-items-center flex-none w-4.5 h-4.5 rounded-[50%] bg-transparent text-muted cursor-pointer [&:hover]:bg-line [&:hover]:text-foreground shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                type="button"
                aria-label={`Remove ${byId.get(token)?.name || token}`}
                onClick={() => onChange(value.filter((v) => v !== token))}
              >
                <X size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <label
        className="flex items-center gap-2 pl-2.5 border border-solid border-line rounded-md bg-bg text-muted focus-within:outline-solid focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-1"
        data-fidelity-state="mt-search"
      >
        <Search size={14} aria-hidden="true" />
        <input
          className="w-full min-h-8 py-1.5 px-2.5 border-0 rounded-md bg-transparent text-foreground text-[13px] focus-visible:[outline:0] shell-phone:min-h-11 pointer-coarse:min-h-11"
          value={query}
          aria-label="Search people"
          placeholder={
            placeholder ||
            (allowEmail ? "Search people or type an email" : "Search people")
          }
          onChange={(e) => {
            setQuery(e.target.value);
            setError("");
          }}
          onKeyDown={(e) => {
            if (e.key !== "Enter") return;
            e.preventDefault();
            if (matches[0] && !query.includes("@")) add(matches[0].id);
            else if (allowEmail) addEmail();
          }}
        />
      </label>
      {error && (
        <p
          className="mt-1.5 mx-0 mb-0 text-danger text-[12px]"
          data-fidelity-state="mt-field-error"
          role="alert"
        >
          {error}
        </p>
      )}
      {(needle || matches.length > 0) && (
        <ul
          className="grid m-0 p-0.75 border border-solid border-line rounded-lg bg-panel list-none"
          data-fidelity-state="mt-suggest"
          role="listbox"
          aria-label="People"
        >
          {matches.map((p) => (
            <li key={p.id}>
              <button
                className="flex items-center gap-2.25 w-full py-1.5 px-2 rounded-[5px] bg-transparent text-foreground text-left cursor-pointer [&:hover]:bg-subtle shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                type="button"
                role="option"
                aria-selected={false}
                onClick={() => add(p.id)}
              >
                <span
                  className="grid place-items-center flex-none w-6.5 h-6.5 rounded-[50%] bg-accent-soft text-accent text-[11px] font-bold"
                  data-fidelity-state="mt-avatar"
                  aria-hidden="true"
                >
                  {(p.name || "?")[0]?.toUpperCase()}
                </span>
                <span
                  className="grid min-w-0"
                  data-fidelity-state="mt-suggest-text"
                >
                  <b className="overflow-hidden text-ellipsis whitespace-nowrap">
                    {p.name}
                  </b>
                  <small className="overflow-hidden text-ellipsis whitespace-nowrap text-muted">
                    {p.email}
                  </small>
                </span>
              </button>
            </li>
          ))}
          {allowEmail && needle.includes("@") && (
            <li>
              <button
                className="flex items-center gap-2.25 w-full py-1.5 px-2 rounded-[5px] bg-transparent text-foreground text-left cursor-pointer [&:hover]:bg-subtle shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                type="button"
                onClick={addEmail}
              >
                <span
                  className="grid place-items-center flex-none w-6.5 h-6.5 rounded-[50%] bg-accent-soft text-accent text-[11px] font-bold"
                  data-fidelity-state="mt-avatar"
                  aria-hidden="true"
                >
                  @
                </span>
                <span
                  className="grid min-w-0"
                  data-fidelity-state="mt-suggest-text"
                >
                  <b className="overflow-hidden text-ellipsis whitespace-nowrap">
                    Invite {needle}
                  </b>
                  <small className="overflow-hidden text-ellipsis whitespace-nowrap text-muted">
                    By email
                  </small>
                </span>
              </button>
            </li>
          )}
          {!matches.length && !(allowEmail && needle.includes("@")) && (
            <li
              className="p-2 text-muted"
              data-fidelity-state="mt-suggest-empty"
            >
              No matching people
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

const pad = (n: number) => String(n).padStart(2, "0");
const dateValue = (d: Date) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const timeValue = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

function defaultStart(): Date {
  const d = new Date(Date.now() + 3600000);
  d.setMinutes(d.getMinutes() < 30 ? 30 : 60, 0, 0);
  return d;
}

export function ScheduleDialog({
  meeting,
  viewerId,
  initialDate,
  onClose,
  onSaved,
}: {
  meeting?: Meeting;
  viewerId?: string;
  initialDate?: Date;
  onClose: () => void;
  onSaved?: (m: Meeting) => void;
}) {
  const editing = !!meeting;
  const start = meeting?.scheduledStart
    ? new Date(meeting.scheduledStart)
    : initialDate
      ? (() => {
          const d = new Date(initialDate);
          d.setHours(defaultStart().getHours(), 0, 0, 0);
          return d;
        })()
      : defaultStart();
  const [title, setTitle] = useState(meeting?.title || "");
  const [description, setDescription] = useState(meeting?.description || "");
  const [date, setDate] = useState(dateValue(start));
  const [time, setTime] = useState(timeValue(start));
  const [duration, setDuration] = useState(meeting?.durationMinutes || 30);
  const [access, setAccess] = useState<Access>(
    meeting?.access === "org" ? "org" : "invited",
  );
  const [waitingRoom, setWaitingRoom] = useState(
    meeting?.settings.waitingRoom ?? true,
  );
  const [linkEnabled, setLinkEnabled] = useState(meeting?.linkEnabled ?? false);
  const [muteOnEntry, setMuteOnEntry] = useState(
    meeting?.settings.muteOnEntry ?? true,
  );
  const [screenAll, setScreenAll] = useState(
    (meeting?.settings.screenShare ?? "all") === "all",
  );
  const [chat, setChat] = useState(meeting?.settings.chatEnabled ?? true);
  const [invitees, setInvitees] = useState<string[]>([]);
  const [people, setPeople] = useState<Person[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<{
    meeting: Meeting;
    passcode: string | null;
  } | null>(null);
  const [copied, setCopied] = useState(false);
  const titleRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void meetingsApi
      .people()
      .then(setPeople)
      .catch(() => setPeople([]));
  }, []);
  useEffect(() => {
    titleRef.current?.focus();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const startDate = useMemo(() => new Date(`${date}T${time}`), [date, time]);
  const validStart = !Number.isNaN(startDate.getTime());
  const inPast = validStart && startDate.getTime() < Date.now() - 60000;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) {
      setError("Give the meeting a title.");
      return;
    }
    if (!validStart) {
      setError("Choose a valid date and time.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const settings = {
        waitingRoom,
        linkEnabled,
        muteOnEntry,
        chatEnabled: chat,
        access,
        screenShare: screenAll ? ("all" as const) : ("host" as const),
      };
      if (!editing) {
        const input: CreateInput = {
          title: title.trim(),
          description: description.trim() || undefined,
          ...settings,
          scheduledStart: startDate.toISOString(),
          durationMinutes: duration,
          invitees,
        };
        const res = await meetingsApi.create(input);
        setDone({ meeting: res.meeting, passcode: res.passcode });
        onSaved?.(res.meeting);
      } else {
        const patch: Record<string, unknown> = {};
        const before = meeting!;
        if (title.trim() !== before.title) patch.title = title.trim();
        if ((description.trim() || null) !== (before.description || null))
          patch.description = description.trim() || null;
        if (
          startDate.toISOString() !==
          (before.scheduledStart
            ? new Date(before.scheduledStart).toISOString()
            : "")
        )
          patch.scheduledStart = startDate.toISOString();
        if (duration !== before.durationMinutes)
          patch.durationMinutes = duration;
        if (access !== before.access) patch.access = access;
        if (waitingRoom !== before.settings.waitingRoom)
          patch.waitingRoom = waitingRoom;
        if (linkEnabled !== before.linkEnabled) patch.linkEnabled = linkEnabled;
        if (muteOnEntry !== before.settings.muteOnEntry)
          patch.muteOnEntry = muteOnEntry;
        if (settings.screenShare !== before.settings.screenShare)
          patch.screenShare = settings.screenShare;
        if (chat !== before.settings.chatEnabled) patch.chatEnabled = chat;
        let current = before;
        let passcode: string | null = null;
        if (Object.keys(patch).length) {
          const res = await meetingsApi.patch(before.code, patch);
          current = res.meeting;
          passcode = res.passcode;
        }
        if (invitees.length) await meetingsApi.invite(before.code, invitees);
        setDone({ meeting: current, passcode });
        onSaved?.(current);
      }
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Could not save the meeting.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      /* clipboard blocked */
    }
  }

  return (
    <div
      className="fixed inset-0 z-60 grid place-items-center p-4 bg-[#0006] [@media(max-width:600px)]:p-0 [@media(max-width:600px)]:[place-items:end_stretch]"
      data-fidelity-state="mt-backdrop"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        data-mt-dialog=""
        className="flex flex-col w-[min(560px,100%)] max-h-[calc(100dvh-32px)] border border-solid border-line rounded-[10px] bg-panel shadow-(--shadow) [@media(max-width:600px)]:w-full [@media(max-width:600px)]:max-h-[92vh] [@media(max-width:600px)]:rounded-[12px_12px_0_0]"
        data-fidelity-state="mt-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mt-schedule-title"
      >
        <header
          className="flex items-center justify-between gap-2.5 py-3.5 px-4 border-b border-b-line"
          data-fidelity-state="mt-dialog-head"
        >
          <h2 className="m-0 text-[16px]" id="mt-schedule-title">
            {done
              ? editing
                ? "Meeting updated"
                : "Meeting scheduled"
              : editing
                ? "Edit meeting"
                : "Schedule a meeting"}
          </h2>
          <button
            type="button"
            className="inline-grid place-items-center flex-none w-7.5 h-7.5 border-0 rounded-md bg-transparent text-muted cursor-pointer [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-50 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            data-fidelity-state="mt-icon-btn"
            aria-label="Close"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </header>

        {done ? (
          <div
            className="grid gap-3.5 min-h-0 py-3.5 px-4 overflow-y-auto"
            data-fidelity-state="mt-dialog-body"
          >
            <p
              className="flex items-center gap-2 m-0 text-[15px] font-semibold"
              data-fidelity-state="mt-done-title"
            >
              <Check size={16} aria-hidden="true" /> {done.meeting.title}
            </p>
            <div className="flex gap-1.5" data-fidelity-state="mt-copy-row">
              <input
                className="w-full min-h-8 py-1.5 px-2.5 border border-solid border-line rounded-md bg-bg text-foreground text-[13px] flex-1 min-w-0 shell-phone:min-h-11 pointer-coarse:min-h-11"
                readOnly
                value={joinUrl(done.meeting.code)}
                aria-label="Meeting link"
                onFocus={(e) => e.currentTarget.select()}
              />
              <button
                type="button"
                className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-panel text-foreground text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                data-fidelity-state="mt-btn"
                onClick={() => void copy(joinUrl(done.meeting.code))}
              >
                <Copy size={14} aria-hidden="true" />
                {copied ? "Copied" : "Copy"}
              </button>
            </div>
            {done.passcode && (
              <p
                className="mt-2 mx-0 mb-0 py-2 px-2.5 rounded-md bg-accent-soft text-foreground text-[12px] leading-normal"
                data-fidelity-state="mt-note"
              >
                Passcode for guests:{" "}
                <b
                  className="font-[ui-monospace,SFMono-Regular,Menlo,monospace] tracking-[.06em]"
                  data-fidelity-state="mt-code"
                >
                  {done.passcode}
                </b>{" "}
                (the link above works without it only for invited members).
              </p>
            )}
            {done.meeting.scheduledStart && (
              <a
                className="inline-flex items-center gap-1.5 text-accent font-medium"
                data-fidelity-state="mt-link"
                href={googleCalendarUrl(done.meeting)}
                target="_blank"
                rel="noreferrer"
              >
                <CalendarDays size={14} aria-hidden="true" /> Add to Google
                Calendar
              </a>
            )}
            <footer
              className="flex justify-end gap-2 pt-1"
              data-fidelity-state="mt-dialog-foot"
            >
              <button
                type="button"
                className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-accent text-white text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-accent disabled:opacity-55 disabled:cursor-default border-accent [&:hover]:filter-[brightness(1.1)] [:root[data-theme=dark]_&]:bg-accent [:root[data-theme=dark]_&]:text-[#171719] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                data-fidelity-state="mt-btn mt-btn-primary"
                onClick={onClose}
              >
                Done
              </button>
            </footer>
          </div>
        ) : (
          <form
            className="grid gap-3.5 min-h-0 py-3.5 px-4 overflow-y-auto"
            data-fidelity-state="mt-dialog-body"
            onSubmit={(e) => void submit(e)}
          >
            <label
              className="grid gap-1.5 min-w-0 [&>span]:text-[12px] [&>span]:font-semibold"
              data-fidelity-state="mt-field"
            >
              <span className="">Title</span>
              <input
                className="w-full min-h-8 py-1.5 px-2.5 border border-solid border-line rounded-md bg-bg text-foreground text-[13px] shell-phone:min-h-11 pointer-coarse:min-h-11"
                ref={titleRef}
                value={title}
                maxLength={200}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Weekly sync"
              />
            </label>
            <div
              className="grid grid-cols-[1.3fr_1fr_1fr] gap-2.5 shell-phone:grid-cols-[1fr_1fr]"
              data-fidelity-state="mt-row3"
            >
              <label
                className="grid gap-1.5 min-w-0 [&>span]:text-[12px] [&>span]:font-semibold shell-phone:first:col-span-full"
                data-fidelity-state="mt-field"
              >
                <span className="">Date</span>
                <input
                  className="w-full min-h-8 py-1.5 px-2.5 border border-solid border-line rounded-md bg-bg text-foreground text-[13px] shell-phone:min-h-11 pointer-coarse:min-h-11"
                  type="date"
                  value={date}
                  onChange={(e) => setDate(e.target.value)}
                  required
                />
              </label>
              <label
                className="grid gap-1.5 min-w-0 [&>span]:text-[12px] [&>span]:font-semibold shell-phone:first:col-span-full"
                data-fidelity-state="mt-field"
              >
                <span className="">Start</span>
                <input
                  className="w-full min-h-8 py-1.5 px-2.5 border border-solid border-line rounded-md bg-bg text-foreground text-[13px] shell-phone:min-h-11 pointer-coarse:min-h-11"
                  type="time"
                  value={time}
                  onChange={(e) => setTime(e.target.value)}
                  required
                />
              </label>
              <label
                className="grid gap-1.5 min-w-0 [&>span]:text-[12px] [&>span]:font-semibold shell-phone:first:col-span-full"
                data-fidelity-state="mt-field"
              >
                <span className="">Duration</span>
                <select
                  className="w-full min-h-8 py-1.5 px-2.5 border border-solid border-line rounded-md bg-bg text-foreground text-[13px] shell-phone:min-h-11 pointer-coarse:min-h-11"
                  value={duration}
                  onChange={(e) => setDuration(Number(e.target.value))}
                >
                  {[...new Set([...DURATIONS, duration])]
                    .sort((a, b) => a - b)
                    .map((d) => (
                      <option key={d} value={d}>
                        {d < 60 ? `${d} min` : `${d / 60} h`}
                      </option>
                    ))}
                </select>
              </label>
            </div>
            {inPast && (
              <p
                className="mt-2 mx-0 mb-0 py-2 px-2.5 rounded-md bg-[#d9770622] text-foreground text-[12px] leading-normal"
                data-fidelity-state="mt-note mt-note-warn"
              >
                That time is in the past. The meeting will still be created.
              </p>
            )}
            <label
              className="grid gap-1.5 min-w-0 [&>span]:text-[12px] [&>span]:font-semibold"
              data-fidelity-state="mt-field"
            >
              <span className="">Description</span>
              <textarea
                className="w-full min-h-8 py-1.5 px-2.5 border border-solid border-line rounded-md bg-bg text-foreground text-[13px] resize-y shell-phone:min-h-11 pointer-coarse:min-h-11"
                rows={3}
                value={description}
                maxLength={5000}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Agenda or notes"
              />
            </label>
            <div
              className="grid gap-1.5 min-w-0 [&>span]:text-[12px] [&>span]:font-semibold"
              data-fidelity-state="mt-field"
            >
              <span>{editing ? "Invite more people" : "People"}</span>
              <PeoplePicker
                people={people}
                value={invitees}
                onChange={setInvitees}
                excludeId={viewerId}
                allowEmail
              />
            </div>
            <label
              className="grid gap-1.5 min-w-0 [&>span]:text-[12px] [&>span]:font-semibold"
              data-fidelity-state="mt-field"
            >
              <span className="">Who can join</span>
              <select
                className="w-full min-h-8 py-1.5 px-2.5 border border-solid border-line rounded-md bg-bg text-foreground text-[13px] shell-phone:min-h-11 pointer-coarse:min-h-11"
                value={access}
                onChange={(e) => setAccess(e.target.value as Access)}
              >
                <option value="invited">Only invited people</option>
                <option value="org">Everyone in the workspace</option>
              </select>
            </label>
            <fieldset
              className="grid gap-2 m-0 py-2.5 px-3 border border-solid border-line rounded-lg"
              data-fidelity-state="mt-options"
            >
              <legend className="py-0 px-1 text-[12px] font-semibold">
                Options
              </legend>
              <label className="flex items-start gap-2 leading-[1.4]">
                <input
                  className="mt-0.5"
                  type="checkbox"
                  checked={waitingRoom}
                  onChange={(e) => setWaitingRoom(e.target.checked)}
                />{" "}
                Waiting room — the host admits people
              </label>
              <label className="flex items-start gap-2 leading-[1.4]">
                <input
                  className="mt-0.5"
                  type="checkbox"
                  checked={linkEnabled}
                  onChange={(e) => setLinkEnabled(e.target.checked)}
                />{" "}
                Public guest link — anyone with the link can ask to join
              </label>
              {linkEnabled && !waitingRoom && (
                <p
                  className="mt-2 mx-0 mb-0 py-2 px-2.5 rounded-md bg-accent-soft text-foreground text-[12px] leading-normal"
                  data-fidelity-state="mt-note"
                >
                  Without a waiting room, guests need a passcode. One is
                  generated for you.
                </p>
              )}
              <label className="flex items-start gap-2 leading-[1.4]">
                <input
                  className="mt-0.5"
                  type="checkbox"
                  checked={muteOnEntry}
                  onChange={(e) => setMuteOnEntry(e.target.checked)}
                />{" "}
                Mute people when they join
              </label>
              <label className="flex items-start gap-2 leading-[1.4]">
                <input
                  className="mt-0.5"
                  type="checkbox"
                  checked={screenAll}
                  onChange={(e) => setScreenAll(e.target.checked)}
                />{" "}
                Everyone can share their screen
              </label>
              <label className="flex items-start gap-2 leading-[1.4]">
                <input
                  className="mt-0.5"
                  type="checkbox"
                  checked={chat}
                  onChange={(e) => setChat(e.target.checked)}
                />{" "}
                Chat enabled
              </label>
            </fieldset>
            {error && (
              <p
                className="mt-1.5 mx-0 mb-0 text-danger text-[12px]"
                data-fidelity-state="mt-field-error"
                role="alert"
              >
                {error}
              </p>
            )}
            <footer
              className="flex justify-end gap-2 pt-1"
              data-fidelity-state="mt-dialog-foot"
            >
              <button
                type="button"
                className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-panel text-foreground text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                data-fidelity-state="mt-btn"
                onClick={onClose}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-accent text-white text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-accent disabled:opacity-55 disabled:cursor-default border-accent [&:hover]:filter-[brightness(1.1)] [:root[data-theme=dark]_&]:bg-accent [:root[data-theme=dark]_&]:text-[#171719] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                data-fidelity-state="mt-btn mt-btn-primary"
                disabled={busy}
              >
                {busy ? "Saving…" : editing ? "Save changes" : "Schedule"}
              </button>
            </footer>
          </form>
        )}
      </div>
    </div>
  );
}
