import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "../components/ui/popover";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  CalendarDays,
  Copy,
  Download,
  Link2,
  Pencil,
  Phone,
  Trash2,
  Users,
  Video,
  X,
} from "lucide-react";
import {
  fmtDuration,
  fmtTime,
  fmtWhen,
  googleCalendarUrl,
  joinUrl,
  meetingsApi,
  type Invitee,
  type Meeting,
  type Person,
  type Rsvp,
} from "./api";
import { PeoplePicker, ScheduleDialog } from "./ScheduleDialog";
import { Markdown } from "../markdown";
import "./meetings-page.css";

const RSVP_LABEL: Record<Rsvp, string> = {
  needsAction: "No reply",
  accepted: "Going",
  declined: "Declined",
  tentative: "Maybe",
};

type Viewer = { id?: string; name?: string; email?: string; admin?: boolean };

export function MeetingDrawer({
  meeting: initial,
  viewer,
  onClose,
  onChanged,
}: {
  meeting: Meeting;
  viewer: Viewer;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [meeting, setMeeting] = useState<Meeting>(initial);
  const [invitees, setInvitees] = useState<Invitee[] | null>(null);
  const [people, setPeople] = useState<Person[]>([]);
  const [adding, setAdding] = useState(false);
  const [toInvite, setToInvite] = useState<string[]>([]);
  const [editing, setEditing] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [calMenu, setCalMenu] = useState(false);
  const [passcode, setPasscode] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);

  const isHost = meeting.isHost ?? meeting.hostId === viewer.id;
  const canManage = isHost || !!viewer.admin;
  const over = meeting.status === "ended" || meeting.status === "cancelled";

  const reload = useCallback(async () => {
    try {
      const { meeting: fresh } = await meetingsApi.get(initial.code);
      setMeeting((m) => ({
        ...m,
        ...fresh,
        isHost: m.isHost,
        myResponse: fresh.myResponse ?? m.myResponse,
      }));
    } catch {
      /* keep what the list gave us */
    }
    if (initial.status === "ended" || initial.status === "cancelled") return; // the recap replaces the guest list
    try {
      setInvitees(await meetingsApi.invitees(initial.code));
    } catch {
      setInvitees(null);
    }
  }, [initial.code, initial.status]);

  useEffect(() => {
    void reload();
  }, [reload]);
  useEffect(() => {
    void meetingsApi
      .people()
      .then(setPeople)
      .catch(() => setPeople([]));
  }, []);
  useEffect(() => {
    closeRef.current?.focus();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !editing) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, editing]);

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await fn();
      if (ok) setNotice(ok);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(joinUrl(meeting.code));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      /* blocked */
    }
  };

  const answer = (response: Exclude<Rsvp, "needsAction">) =>
    run(async () => {
      await meetingsApi.rsvp(meeting.code, response);
      setMeeting((m) => ({ ...m, myResponse: response }));
      onChanged();
    });

  const invite = () =>
    run(async () => {
      await meetingsApi.invite(meeting.code, toInvite);
      setToInvite([]);
      setAdding(false);
      await reload();
      onChanged();
    }, "Invitations sent.");

  const ring = (userId: string) =>
    run(async () => {
      await meetingsApi.ring(meeting.code, [userId]);
    }, "Ringing…");

  const toggleLink = () =>
    run(async () => {
      const res = await meetingsApi.patch(meeting.code, {
        linkEnabled: !meeting.linkEnabled,
      });
      setMeeting((m) => ({ ...m, ...res.meeting, isHost: m.isHost }));
      setPasscode(res.passcode);
      onChanged();
    });

  const cancel = () =>
    run(async () => {
      await meetingsApi.cancel(meeting.code);
      onChanged();
      onClose();
    });

  const gb = meeting.usage ? meeting.usage.egressBytes / 1e9 : 0;

  return (
    <div
      className="fixed inset-0 z-60 grid [place-items:stretch_end] p-0 bg-[#0006] [@media(max-width:600px)]:p-0 [@media(max-width:600px)]:[place-items:end_stretch]"
      data-fidelity-state="mt-backdrop mt-backdrop-right"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <aside
        data-mt-drawer=""
        className="flex flex-col w-[min(480px,100%)] h-full border-l border-l-line bg-panel shadow-(--shadow) animate-[mt-slide_.18s_ease] [@media(max-width:600px)]:w-full [@media(max-width:600px)]:border-l-0 motion-reduce:animate-none"
        data-fidelity-state="mt-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="mt-drawer-title"
      >
        <header
          className="flex items-start justify-between gap-2.5 pt-4 px-4 pb-3 border-b border-b-line"
          data-fidelity-state="mt-drawer-head"
        >
          <div
            className="grid gap-1.25 min-w-0"
            data-fidelity-state="mt-drawer-titles"
          >
            <span
              className="flex-none py-0.5 px-1.75 rounded-[9px] bg-subtle text-muted text-[11px] font-semibold capitalize justify-self-start data-[fidelity-state~=mt-status-live]:bg-[#16a34a22] data-[fidelity-state~=mt-status-live]:text-[#16a34a] data-[fidelity-state~=mt-status-scheduled]:bg-accent-soft data-[fidelity-state~=mt-status-scheduled]:text-accent data-[fidelity-state~=mt-status-cancelled]:bg-[#ef444422] data-[fidelity-state~=mt-status-cancelled]:text-danger"
              data-fidelity-state={`mt-status mt-status-${meeting.status}`}
            >
              {meeting.status === "live" ? "Live now" : meeting.status}
            </span>
            <h2
              className="m-0 overflow-hidden text-[18px] tracking-[-.02em] text-ellipsis whitespace-nowrap"
              id="mt-drawer-title"
              title={meeting.title}
            >
              {meeting.title}
            </h2>
            <p className="text-muted m-0" data-fidelity-state="mt-muted">
              {fmtWhen(meeting)}
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            className="inline-grid place-items-center flex-none w-7.5 h-7.5 border-0 rounded-md bg-transparent text-muted cursor-pointer [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-50 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
            data-fidelity-state="mt-icon-btn"
            aria-label="Close details"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </header>

        <div
          className="grid flex-1 content-start gap-4 min-h-0 py-3.5 px-4 overflow-y-auto"
          data-fidelity-state="mt-drawer-body"
        >
          {!over && (
            <div
              className="flex flex-wrap gap-2"
              data-fidelity-state="mt-actions"
            >
              <a
                className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-accent text-white text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-accent disabled:opacity-55 disabled:cursor-default border-accent [&:hover]:filter-[brightness(1.1)] [:root[data-theme=dark]_&]:bg-accent [:root[data-theme=dark]_&]:text-[#171719]"
                data-fidelity-state="mt-btn mt-btn-primary"
                href={`/meet/${meeting.code}`}
              >
                <Video size={14} aria-hidden="true" />
                {isHost
                  ? meeting.status === "live"
                    ? "Join"
                    : "Start"
                  : "Join"}
              </a>
              <Popover open={calMenu} onOpenChange={setCalMenu}>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-panel text-foreground text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    data-fidelity-state="mt-btn"
                    aria-expanded={calMenu}
                  >
                    <CalendarDays size={14} aria-hidden="true" />
                    Add to calendar
                  </button>
                </PopoverTrigger>

                <PopoverContent
                  align="start"
                  className="grid w-auto min-w-47.5 p-1 border border-solid border-line rounded-lg bg-panel shadow-(--shadow)"
                  data-fidelity-state="mt-menu"
                  role="menu"
                >
                  <a
                    className="flex items-center gap-1.5 py-1.75 px-2.5 rounded-[5px] text-foreground no-underline [&:hover]:bg-subtle"
                    role="menuitem"
                    href={googleCalendarUrl(meeting)}
                    target="_blank"
                    rel="noreferrer"
                    onClick={() => setCalMenu(false)}
                  >
                    Google Calendar
                  </a>
                  <a
                    className="flex items-center gap-1.5 py-1.75 px-2.5 rounded-[5px] text-foreground no-underline [&:hover]:bg-subtle"
                    role="menuitem"
                    href={meetingsApi.icsUrl(meeting.code)}
                    download={`${meeting.code}.ics`}
                    onClick={() => setCalMenu(false)}
                  >
                    <Download size={13} aria-hidden="true" />
                    Download .ics
                  </a>
                </PopoverContent>
              </Popover>
            </div>
          )}

          {meeting.myResponse && !isHost && !over && (
            <section
              className="grid gap-2"
              data-fidelity-state="mt-section"
              aria-label="Your response"
            >
              <h3 className="flex items-center gap-1.5 m-0 text-[12px] text-muted uppercase tracking-[.06em]">
                Your response
              </h3>
              <div
                className="inline-flex border border-solid border-line rounded-[7px] bg-subtle p-0.5"
                data-fidelity-state="mt-segment"
                role="group"
                aria-label="RSVP"
              >
                {(["accepted", "tentative", "declined"] as const).map((r) => (
                  <button
                    key={r}
                    type="button"
                    disabled={busy}
                    aria-pressed={meeting.myResponse === r}
                    className="min-h-6.5 py-0.75 px-2.75 rounded-[5px] bg-transparent text-muted text-[12px] font-medium cursor-pointer data-[fidelity-state~=mt-on]:bg-panel data-[fidelity-state~=mt-on]:text-foreground data-[fidelity-state~=mt-on]:shadow-[0_1px_2px_#0002] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    data-fidelity-state={
                      meeting.myResponse === r ? "mt-on" : ""
                    }
                    onClick={() => void answer(r)}
                  >
                    {RSVP_LABEL[r]}
                  </button>
                ))}
              </div>
            </section>
          )}

          <dl className="grid gap-1.5 m-0" data-fidelity-state="mt-facts">
            <div className="flex gap-2.5">
              <dt className="flex-none w-24 text-muted">Host</dt>
              <dd className="m-0 min-w-0 wrap-anywhere">
                {meeting.hostName || "Unknown"}
              </dd>
            </div>
            <div className="flex gap-2.5">
              <dt className="flex-none w-24 text-muted">Who can join</dt>
              <dd className="m-0 min-w-0 wrap-anywhere">
                {meeting.access === "org"
                  ? "Everyone in the workspace"
                  : meeting.access === "team"
                    ? "Team members"
                    : "Invited people"}
              </dd>
            </div>
            {meeting.hasPasscode && (
              <div className="flex gap-2.5">
                <dt className="flex-none w-24 text-muted">Passcode</dt>
                <dd className="m-0 min-w-0 wrap-anywhere">
                  Required for guests
                </dd>
              </div>
            )}
          </dl>

          {meeting.description && (
            <div
              className="m-0 wrap-anywhere leading-normal"
              data-fidelity-state="mt-description"
            >
              <Markdown text={meeting.description} density="compact" />
            </div>
          )}

          {meeting.status === "ended" && <Recap meeting={meeting} />}
          {meeting.status === "cancelled" && (
            <p
              className="mt-2 mx-0 mb-0 py-2 px-2.5 rounded-md bg-accent-soft text-foreground text-[12px] leading-normal"
              data-fidelity-state="mt-note"
              role="status"
            >
              This meeting was cancelled.
            </p>
          )}

          {!over && (
            <section className="grid gap-2" data-fidelity-state="mt-section">
              <h3 className="flex items-center gap-1.5 m-0 text-[12px] text-muted uppercase tracking-[.06em]">
                Join link
              </h3>
              <div className="flex gap-1.5" data-fidelity-state="mt-copy-row">
                <input
                  className="w-full min-h-8 py-1.5 px-2.5 border border-solid border-line rounded-md bg-bg text-foreground text-[13px] flex-1 min-w-0 shell-phone:min-h-11 pointer-coarse:min-h-11"
                  readOnly
                  value={joinUrl(meeting.code)}
                  aria-label="Meeting link"
                  onFocus={(e) => e.currentTarget.select()}
                />
                <button
                  type="button"
                  className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-panel text-foreground text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-fidelity-state="mt-btn"
                  onClick={() => void copy()}
                >
                  <Copy size={14} aria-hidden="true" />
                  {copied ? "Copied" : "Copy"}
                </button>
              </div>
              {canManage && !over && (
                <label
                  className="flex items-center gap-2 cursor-pointer"
                  data-fidelity-state="mt-switch"
                >
                  <input
                    type="checkbox"
                    checked={meeting.linkEnabled}
                    disabled={busy}
                    onChange={() => void toggleLink()}
                  />
                  <span className="inline-flex items-center gap-1.25">
                    <Link2 size={13} aria-hidden="true" /> Anyone with the link
                    can ask to join
                  </span>
                </label>
              )}
              {passcode && (
                <p
                  className="mt-2 mx-0 mb-0 py-2 px-2.5 rounded-md bg-accent-soft text-foreground text-[12px] leading-normal"
                  data-fidelity-state="mt-note"
                >
                  New passcode:{" "}
                  <b
                    className="font-[ui-monospace,SFMono-Regular,Menlo,monospace] tracking-[.06em]"
                    data-fidelity-state="mt-code"
                  >
                    {passcode}
                  </b>
                  . Share it with guests; it is not shown again.
                </p>
              )}
            </section>
          )}

          {!over && (
            <section className="grid gap-2" data-fidelity-state="mt-section">
              <div
                className="flex items-center justify-between gap-2"
                data-fidelity-state="mt-section-head"
              >
                <h3 className="flex items-center gap-1.5 m-0 text-[12px] text-muted uppercase tracking-[.06em]">
                  <Users size={14} aria-hidden="true" /> People
                  {invitees ? ` · ${invitees.length}` : ""}
                </h3>
                {canManage && !over && (
                  <button
                    type="button"
                    className="inline-flex items-center justify-center gap-1.5 min-h-7 py-1 px-2.25 border border-solid border-line rounded-md bg-panel text-foreground text-[12px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    data-fidelity-state="mt-btn mt-btn-small"
                    onClick={() => setAdding((v) => !v)}
                  >
                    {adding ? "Close" : "Invite people"}
                  </button>
                )}
              </div>
              {adding && (
                <div
                  className="grid gap-2 p-2.5 border border-solid border-line rounded-lg bg-subtle"
                  data-fidelity-state="mt-invite-box"
                >
                  <PeoplePicker
                    people={people}
                    value={toInvite}
                    onChange={setToInvite}
                    excludeId={viewer.id}
                    allowEmail
                    autoFocus
                  />
                  <button
                    type="button"
                    className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-accent text-white text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-accent disabled:opacity-55 disabled:cursor-default border-accent [&:hover]:filter-[brightness(1.1)] [:root[data-theme=dark]_&]:bg-accent [:root[data-theme=dark]_&]:text-[#171719] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                    data-fidelity-state="mt-btn mt-btn-primary"
                    disabled={busy || !toInvite.length}
                    onClick={() => void invite()}
                  >
                    Send invitations
                  </button>
                </div>
              )}
              {invitees === null ? (
                <p className="text-muted" data-fidelity-state="mt-muted">
                  {canManage
                    ? "Loading people…"
                    : "The guest list is visible to the host."}
                </p>
              ) : invitees.length === 0 ? (
                <p className="text-muted" data-fidelity-state="mt-muted">
                  No one invited yet.
                </p>
              ) : (
                <ul
                  className="grid gap-1 m-0 p-0 list-none"
                  data-fidelity-state="mt-people-list"
                >
                  {invitees.map((i) => (
                    <li
                      className="flex items-center gap-2 min-w-0 py-1 px-0"
                      key={i.id}
                    >
                      <span
                        className="grid place-items-center flex-none w-6.5 h-6.5 rounded-[50%] bg-accent-soft text-accent text-[11px] font-bold"
                        data-fidelity-state="mt-avatar"
                        aria-hidden="true"
                      >
                        {(i.name || i.email || "?")[0]?.toUpperCase()}
                      </span>
                      <span
                        className="grid flex-1 min-w-0"
                        data-fidelity-state="mt-person"
                      >
                        <b className="overflow-hidden text-ellipsis whitespace-nowrap">
                          {i.name || i.email}
                        </b>
                        {i.name && i.email && (
                          <small className="overflow-hidden text-ellipsis whitespace-nowrap text-muted">
                            {i.email}
                          </small>
                        )}
                      </span>
                      {i.response && (
                        <span
                          className="flex-none py-0.5 px-1.75 rounded-[9px] bg-subtle text-muted text-[11px] data-[fidelity-state~=mt-rsvp-accepted]:bg-[#16a34a22] data-[fidelity-state~=mt-rsvp-accepted]:text-[#16a34a] data-[fidelity-state~=mt-rsvp-tentative]:bg-[#d9770622] data-[fidelity-state~=mt-rsvp-tentative]:text-[#d97706] data-[fidelity-state~=mt-rsvp-declined]:bg-[#ef444422] data-[fidelity-state~=mt-rsvp-declined]:text-danger"
                          data-fidelity-state={`mt-rsvp mt-rsvp-${i.response}`}
                        >
                          {RSVP_LABEL[i.response]}
                        </span>
                      )}
                      {i.role === "cohost" && (
                        <span
                          className="flex-none py-0.5 px-1.75 rounded-[9px] bg-subtle text-muted text-[11px]"
                          data-fidelity-state="mt-rsvp"
                        >
                          Co-host
                        </span>
                      )}
                      {canManage &&
                        i.userId &&
                        !over &&
                        i.userId !== viewer.id && (
                          <button
                            type="button"
                            className="inline-grid place-items-center flex-none w-7.5 h-7.5 border-0 rounded-md bg-transparent text-muted cursor-pointer [&:hover]:bg-subtle [&:hover]:text-foreground disabled:opacity-50 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                            data-fidelity-state="mt-icon-btn"
                            aria-label={`Call ${i.name || i.email}`}
                            title="Call"
                            disabled={busy}
                            onClick={() => void ring(i.userId!)}
                          >
                            <Phone size={14} />
                          </button>
                        )}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {canManage && meeting.usage && (
            <p
              className="text-muted m-0 text-[12px]"
              data-fidelity-state="mt-muted mt-usage"
            >
              ~${meeting.usage.estimatedCostUsd.toFixed(2)} ·{" "}
              {gb < 0.1
                ? `${(gb * 1000).toFixed(0)} MB`
                : `${gb.toFixed(1)} GB`}{" "}
              streamed
            </p>
          )}

          {error && (
            <p
              className="mt-1.5 mx-0 mb-0 text-danger text-[12px]"
              data-fidelity-state="mt-field-error"
              role="alert"
            >
              {error}
            </p>
          )}
          {notice && (
            <p
              className="mt-2 mx-0 mb-0 py-2 px-2.5 rounded-md bg-accent-soft text-foreground text-[12px] leading-normal"
              data-fidelity-state="mt-note"
              role="status"
            >
              {notice}
            </p>
          )}
        </div>

        {canManage && !over && (
          <footer
            className="flex flex-wrap items-center justify-between gap-2 py-3 px-4 border-t border-t-line bg-panel"
            data-fidelity-state="mt-drawer-foot"
          >
            {confirmCancel ? (
              <>
                <span
                  className="flex-[1_1_100%] font-medium [&+button]:flex-1"
                  data-fidelity-state="mt-confirm-text"
                >
                  Cancel this meeting for everyone?
                </span>
                <button
                  type="button"
                  className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-panel text-foreground text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-fidelity-state="mt-btn"
                  onClick={() => setConfirmCancel(false)}
                >
                  Keep it
                </button>
                <button
                  type="button"
                  className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-danger text-white text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-danger disabled:opacity-55 disabled:cursor-default border-danger [&:hover]:filter-[brightness(1.08)] shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-fidelity-state="mt-btn mt-btn-danger"
                  disabled={busy}
                  onClick={() => void cancel()}
                >
                  Cancel meeting
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-panel text-foreground text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-fidelity-state="mt-btn"
                  onClick={() => setEditing(true)}
                >
                  <Pencil size={14} aria-hidden="true" />
                  Edit
                </button>
                <button
                  type="button"
                  className="inline-flex items-center justify-center gap-1.5 min-h-8 py-1.5 px-3 border border-solid border-line rounded-md bg-panel text-danger text-[13px] font-medium no-underline whitespace-nowrap cursor-pointer [&:hover]:bg-subtle disabled:opacity-55 disabled:cursor-default shell-phone:min-h-11 pointer-coarse:min-h-11 shell-phone:min-w-11 pointer-coarse:min-w-11"
                  data-fidelity-state="mt-btn mt-btn-danger-ghost"
                  onClick={() => setConfirmCancel(true)}
                >
                  <Trash2 size={14} aria-hidden="true" />
                  Cancel meeting
                </button>
              </>
            )}
          </footer>
        )}
      </aside>
      {editing && (
        <ScheduleDialog
          meeting={meeting}
          viewerId={viewer.id}
          onClose={() => {
            setEditing(false);
            void reload();
          }}
          onSaved={(m) => {
            setMeeting((prev) => ({
              ...prev,
              ...m,
              isHost: prev.isHost,
              myResponse: prev.myResponse,
            }));
            onChanged();
          }}
        />
      )}
    </div>
  );
}

function Recap({ meeting }: { meeting: Meeting }) {
  const recap = meeting.recap;
  const people = recap?.attendees ?? [];
  return (
    <section
      className="grid gap-2.5 p-3 border border-solid border-line rounded-[9px] bg-subtle"
      data-fidelity-state="mt-section mt-recap"
      aria-label="Meeting recap"
    >
      <p className="m-0 wrap-anywhere" data-fidelity-state="mt-recap-line">
        <b className="">Meeting ended</b>
        {meeting.endedAt
          ? ` · ${fmtTime(meeting.endedAt, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}`
          : ""}
        {meeting.durationSeconds != null
          ? ` · lasted ${fmtDuration(meeting.durationSeconds)}`
          : ""}
      </p>
      {people.length === 0 ? (
        <p className="text-muted" data-fidelity-state="mt-muted">
          Nobody joined this meeting.
        </p>
      ) : (
        <>
          <h3 className="flex items-center gap-1.5 m-0 text-[12px] text-muted uppercase tracking-[.06em]">
            <Users size={14} aria-hidden="true" /> Who joined ·{" "}
            {recap?.peopleCount ?? people.length}
          </h3>
          <ul
            className="grid gap-1 m-0 p-0 list-none"
            data-fidelity-state="mt-people-list"
          >
            {people.map((p, i) => (
              <li
                key={`${p.name}-${p.joinedAt}-${i}`}
                className="flex items-center gap-2 min-w-0 py-1 px-0"
              >
                <span
                  className="grid place-items-center flex-none w-6.5 h-6.5 rounded-[50%] bg-accent-soft text-accent text-[11px] font-bold"
                  data-fidelity-state="mt-avatar"
                  aria-hidden="true"
                >
                  {(p.name || "?")[0]?.toUpperCase()}
                </span>
                <span
                  className="grid flex-1 min-w-0"
                  data-fidelity-state="mt-person"
                >
                  <b className="overflow-hidden text-ellipsis whitespace-nowrap">
                    {p.name}
                  </b>
                  <small className="overflow-hidden text-ellipsis whitespace-nowrap text-muted">
                    {fmtTime(p.joinedAt)}
                    {p.leftAt ? ` – ${fmtTime(p.leftAt)}` : ""}
                    {p.seconds != null ? ` · ${fmtDuration(p.seconds)}` : ""}
                  </small>
                </span>
                {p.role === "host" && (
                  <span
                    className="flex-none py-0.5 px-1.75 rounded-[9px] bg-subtle text-muted text-[11px]"
                    data-fidelity-state="mt-rsvp"
                  >
                    Host
                  </span>
                )}
                {p.kind === "guest" && (
                  <span
                    className="flex-none py-0.5 px-1.75 rounded-[9px] bg-subtle text-muted text-[11px]"
                    data-fidelity-state="mt-rsvp"
                  >
                    Guest
                  </span>
                )}
                {p.device && (
                  <span
                    className="flex-none py-0.5 px-1.75 rounded-[9px] bg-subtle text-muted text-[11px]"
                    data-fidelity-state="mt-rsvp"
                  >
                    {p.device}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
