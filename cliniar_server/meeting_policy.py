"""ABAC policy for meetings (see ``abac`` for the evaluation model).

Attributes the service supplies:

subject
    kind           "member" (signed-in workspace user) or "guest" (public-link visitor)
    same_org       member belongs to the meeting's organization
    admin          member is a workspace admin
    key_access     "read" / "read_write" for API-key callers, None for browser sessions
    is_host        member is the meeting's host
    invited        member (or their email) is on the invitee list
    invite_role    "cohost" / "attendee" / None, from the invitee row
    team_visible   member can see the meeting's team (existing team-visibility rule)
    bound          the credential is already attached to a participant row of this meeting
    role           participant role: host / cohost / attendee, else None
    state          participant state: waiting / admitted / left / denied / removed, else None
    was_admitted   participant was admitted earlier in this meeting (a rejoin)
    passcode_ok    the supplied passcode matches
    forced_mute    host muted the participant and the meeting disallows self-unmute
resource
    status, access ("invited" | "team" | "org"), locked, link_enabled, waiting_room,
    join_before_host, passcode_required, full, host_present, screen_share ("all" | "host"),
    chat_enabled, recording ("off" | "allowed" | "recording"), target_role, target_self
"""
from __future__ import annotations

from cliniar_server.abac import Context, Decision, Policy, deny, permit

# --- actions ---------------------------------------------------------------
VIEW = "meeting.view"
PREVIEW = "meeting.preview"
LIST = "meeting.list"
CREATE = "meeting.create"
MANAGE = "meeting.manage"
SETTINGS = "meeting.settings"
END = "meeting.end"
INVITE = "meeting.invite"
LINK = "link.manage"
JOIN = "meeting.join"
ENTER = "meeting.enter"  # skip the waiting room
ADMIT = "participant.admit"
DENY_ENTRY = "participant.deny"
MUTE = "participant.mute"
MUTE_ALL = "meeting.mute_all"
REMOVE = "participant.remove"
LOWER_HAND = "participant.lower_hand"
PROMOTE = "participant.promote"
TRANSFER_HOST = "participant.transfer_host"
AUDIO = "media.publish_audio"
VIDEO = "media.publish_video"
SCREEN = "media.publish_screen"
SUBSCRIBE = "media.subscribe"
CHAT = "chat.send"
HAND = "hand.raise"
REACT = "react.send"
RECORD = "record.control"
RSVP = "meeting.rsvp"
USAGE = "meeting.usage"
MEDIA_STATE = "media.state"

READ_ONLY = frozenset({VIEW, PREVIEW, LIST})
MODERATION = frozenset({ADMIT, DENY_ENTRY, MUTE, MUTE_ALL, REMOVE, LOWER_HAND, RECORD})
IN_MEETING = frozenset({AUDIO, VIDEO, SCREEN, SUBSCRIBE, CHAT, HAND, REACT, MEDIA_STATE})
ALL_ACTIONS = frozenset({
    VIEW, PREVIEW, LIST, CREATE, MANAGE, SETTINGS, END, INVITE, LINK, JOIN, ENTER, PROMOTE, TRANSFER_HOST, RSVP, USAGE,
}) | MODERATION | IN_MEETING
MUTATING = ALL_ACTIONS - READ_ONLY


def _member(c: Context) -> bool:
    return c.s.kind == "member" and bool(c.s.same_org)


def _live(c: Context) -> bool:
    return c.r.status in ("scheduled", "live")


def _moderator(c: Context) -> bool:
    return c.s.state == "admitted" and c.s.role in ("host", "cohost")


def _in_meeting(c: Context) -> bool:
    return c.s.state == "admitted"


def _host_role(c: Context) -> bool:
    return c.s.role == "host" or bool(c.s.is_host)


RULES = [
    # ---- deny rules (win over every permit) ----
    deny("d-read-only-key", MUTATING, lambda c: c.s.key_access == "read",
         "This API key is read-only."),
    deny("d-cross-org", ALL_ACTIONS - {PREVIEW}, lambda c: c.s.kind == "member" and not c.s.same_org,
         "Meeting belongs to another workspace."),
    deny("d-over", ALL_ACTIONS - {VIEW, LIST, CREATE, USAGE}, lambda c: not _live(c),
         "This meeting has ended or was cancelled."),
    deny("d-excluded", ALL_ACTIONS - {VIEW, PREVIEW, LIST, CREATE, USAGE}, lambda c: c.s.state in ("removed", "denied"),
         "You were removed from this meeting."),
    deny("d-locked", JOIN, lambda c: bool(c.r.locked) and not c.s.is_host and not c.s.was_admitted,
         "The host locked this meeting."),
    deny("d-full", JOIN, lambda c: bool(c.r.full) and not c.s.is_host and c.s.state != "admitted",
         "This meeting is full."),
    deny("d-passcode", JOIN,
         lambda c: bool(c.r.passcode_required) and not c.s.passcode_ok and not c.s.is_host and not c.s.bound
         and not (c.s.kind == "member" and c.s.invited),
         "A valid passcode is required."),
    deny("d-guest-link-off", JOIN, lambda c: c.s.kind == "guest" and not c.r.link_enabled,
         "Guest links are disabled for this meeting."),
    deny("d-wait-for-host", ENTER,
         lambda c: not c.r.host_present and not c.r.join_before_host and not c.s.is_host
         and c.s.role not in ("host", "cohost"),
         "Waiting for the host to start the meeting."),
    deny("d-screen-host-only", SCREEN, lambda c: c.r.screen_share == "host" and c.s.role not in ("host", "cohost"),
         "Only hosts can share their screen."),
    deny("d-chat-off", CHAT, lambda c: not c.r.chat_enabled and c.s.role not in ("host", "cohost"),
         "Chat is disabled."),
    deny("d-forced-mute", AUDIO, lambda c: bool(c.s.forced_mute),
         "The host muted you and disabled unmuting."),
    deny("d-target-host", {REMOVE, MUTE, DENY_ENTRY, PROMOTE}, lambda c: c.r.target_role == "host",
         "The host cannot be targeted."),
    deny("d-cohost-vs-cohost", {REMOVE, MUTE}, lambda c: c.s.role == "cohost" and c.r.target_role == "cohost",
         "Co-hosts cannot act on other co-hosts."),
    deny("d-self-remove", REMOVE, lambda c: bool(c.r.target_self), "Use leave to exit a meeting."),
    deny("d-recording-off", RECORD, lambda c: c.r.recording == "off", "Recording is not enabled for this meeting."),

    # ---- read ----
    permit("p-preview", PREVIEW, lambda c: bool(c.r.link_enabled) and _live(c), "Public link preview."),
    permit("p-list", LIST, lambda c: c.s.kind == "member" and bool(c.s.same_org), "Members list their meetings."),
    permit("p-view-involved", VIEW, lambda c: _member(c) and (c.s.is_host or c.s.admin or c.s.invited
                                                               or c.s.role is not None),
           "Host, admin, invitee or participant."),
    permit("p-view-org", VIEW, lambda c: _member(c) and c.r.access == "org", "Workspace-wide meeting."),
    permit("p-view-team", VIEW, lambda c: _member(c) and c.r.access == "team" and bool(c.s.team_visible),
           "Team meeting visible to this member."),
    permit("p-view-guest", VIEW,
           lambda c: c.s.kind == "guest" and bool(c.s.bound) and c.s.state in ("waiting", "admitted", "left"),
           "Guest bound to this meeting."),

    # ---- create / manage ----
    permit("p-create", CREATE, lambda c: _member(c), "Members can create meetings."),
    permit("p-manage", {MANAGE, LINK, END}, lambda c: _member(c) and (c.s.is_host or c.s.admin),
           "Host or workspace admin."),
    permit("p-invite", INVITE, lambda c: _member(c) and (c.s.is_host or c.s.admin or _moderator(c)),
           "Host, admin or co-host invites people."),
    permit("p-usage", USAGE, lambda c: bool(c.s.bound), "Participants report what they streamed, even as the call ends."),
    permit("p-rsvp", RSVP, lambda c: _member(c) and bool(c.s.invited), "Invitees answer."),
    permit("p-settings", SETTINGS, lambda c: _member(c) and (c.s.is_host or c.s.admin or _moderator(c)),
           "Host, admin or co-host adjusts settings."),

    # ---- join ----
    permit("p-join-host", JOIN, lambda c: _member(c) and bool(c.s.is_host), "Host joins."),
    permit("p-join-invited", JOIN, lambda c: _member(c) and bool(c.s.invited), "Invitee joins."),
    permit("p-join-org", JOIN, lambda c: _member(c) and c.r.access == "org", "Workspace-wide meeting."),
    permit("p-join-team", JOIN, lambda c: _member(c) and c.r.access == "team" and bool(c.s.team_visible),
           "Team member joins."),
    permit("p-join-guest", JOIN, lambda c: c.s.kind == "guest" and bool(c.r.link_enabled), "Guest joins by link."),
    permit("p-join-bound", JOIN, lambda c: bool(c.s.bound) and c.s.role is not None, "Rejoin."),
    permit("p-enter-moderator", ENTER, lambda c: bool(c.s.is_host) or c.s.invite_role == "cohost"
           or c.s.role in ("host", "cohost"), "Hosts skip the waiting room."),
    permit("p-enter-open", ENTER, lambda c: not c.r.waiting_room, "No waiting room."),
    permit("p-enter-rejoin", ENTER, lambda c: bool(c.s.was_admitted), "Previously admitted."),

    # ---- moderation ----
    permit("p-moderate", MODERATION, _moderator, "Hosts and co-hosts moderate."),
    permit("p-host-only", {PROMOTE, TRANSFER_HOST}, lambda c: c.s.state == "admitted" and c.s.role == "host",
           "Only the host."),

    # ---- in-meeting ----
    permit("p-in-meeting", IN_MEETING, _in_meeting, "Admitted participants."),
]

POLICY = Policy(RULES)


def decide(action: str, subject: dict, resource: dict, environment: dict | None = None) -> Decision:
    return POLICY.decide(action, subject, resource, environment)
