"""GraphQL surface for decisions (docs/decisions.md).

The SDL lives here rather than in schema.graphql for the same reason views_api.py does: a
feature owns its types, its resolvers, and its errors in one module that build_schema()
composes. Authorization and the issue flag live in Writer/Store, so both the browser and an
agent's API key go through the same rules.
"""
from __future__ import annotations

import base64
import binascii
from typing import Any

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError

from hoja_server import decisions as decisiondocs
from hoja_server import uploads

SDL = gql(
    """
    type DecisionOption {
      key: String!
      label: String!
      description: String
      mediaUrl: String
      mediaType: String
    }
    type IssueDecision {
      id: ID!
      issue: Issue!
      question: String!
      options: JSON!
      typedOptions: [DecisionOption!]!
      context: String
      status: String!
      "True once a human answered; the poll an agent waits on."
      answered: Boolean!
      askedBy: User
      "Free-form agent/session identity the caller supplied, e.g. claw:run-30d5."
      askedByAgent: String
      answer: String
      chosenOption: String
      answeredBy: User
      answeredAt: DateTime
      canceledAt: DateTime
      createdAt: DateTime!
      updatedAt: DateTime!
    }
    type IssueDecisionPayload { success: Boolean! decision: IssueDecision }
    type IssueDecisionAnswerPayload {
      success: Boolean!
      decision: IssueDecision!
      "True when this answer repeated the stored one and wrote nothing."
      replayed: Boolean!
    }
    type UploadMediaPayload {
      success: Boolean!
      url: String!
      filename: String!
      contentType: String!
      size: Int!
      expiresAt: String
      attachment: Attachment
    }
    input DecisionOptionInput {
      "Stable key the answer refers to; generated from the label when omitted."
      key: String
      label: String!
      description: String
      "Optional media URL (/api/uploads/... or http(s)://...) for visual decision options."
      mediaUrl: String
      "Optional media kind: 'image', 'video', or 'link' (inferred from mediaUrl when omitted)."
      mediaType: String
    }
    input IssueDecisionCreateInput {
      issueId: String!
      question: String!
      options: [DecisionOptionInput!]
      context: String
      agent: String
    }
    input IssueDecisionAnswerInput {
      "Key (or label) of the chosen option; required when the decision offers options."
      option: String
      "Answer text; defaults to the chosen option's label."
      answer: String
    }
    input ReviewArtifactInput {
      title: String
      url: String
      filename: String
      contentType: String
      dataBase64: String
      ttlDays: Int
    }
    input ReviewRequestInput {
      issueId: String!
      summary: String!
      artifacts: [ReviewArtifactInput!]
      question: String
      options: [DecisionOptionInput!]
      moveToInReview: Boolean
      agent: String
    }
    type ReviewRequestPayload {
      success: Boolean!
      issue: Issue
      decision: IssueDecision
      attachmentUrls: [String!]!
    }
    extend type Query {
      issueDecisions(issueId: String!, status: String, first: Int = 50): [IssueDecision!]! @deprecated(reason: "Use the task* field instead; this name is kept as a wire-compatible alias.")
      issueDecision(id: String!): IssueDecision! @deprecated(reason: "Use the task* field instead; this name is kept as a wire-compatible alias.")
    }
    extend type Mutation {
      issueDecisionCreate(input: IssueDecisionCreateInput!): IssueDecisionPayload! @deprecated(reason: "Use the task* field instead; this name is kept as a wire-compatible alias.")
      issueDecisionAnswer(id: String!, input: IssueDecisionAnswerInput!): IssueDecisionAnswerPayload! @deprecated(reason: "Use the task* field instead; this name is kept as a wire-compatible alias.")
      issueDecisionCancel(id: String!): IssueDecisionPayload! @deprecated(reason: "Use the task* field instead; this name is kept as a wire-compatible alias.")
      uploadMedia(filename: String!, contentType: String, dataBase64: String!, ttlDays: Int, issueId: String, title: String): UploadMediaPayload!
      issueReviewRequest(input: ReviewRequestInput!): ReviewRequestPayload! @deprecated(reason: "Use the task* field instead; this name is kept as a wire-compatible alias.")
    }
    """
)

query = QueryType()
mutation = MutationType()
decision_type = ObjectType("IssueDecision")

_MAX_PAGE = 200


def _ctx(info):
    ctx = info.context
    return ctx["store"], ctx["writer"], ctx["org_id"], ctx["user_id"]


def _bad(exc: Exception) -> GraphQLError:
    if isinstance(exc, decisiondocs.DecisionError):
        code = "ALREADY_ANSWERED" if isinstance(exc, decisiondocs.AlreadyAnsweredError) else "BAD_USER_INPUT"
        return GraphQLError(str(exc), extensions={"code": code, "field": exc.field})
    if isinstance(exc, GraphQLError):
        return exc
    return GraphQLError(str(exc), extensions={"code": "INTERNAL_SERVER_ERROR"})


def _not_found() -> GraphQLError:
    return GraphQLError("Decision not found.", extensions={"code": "NOT_FOUND"})


def _page(first) -> int:
    if first is None:
        return 50
    return min(max(int(first), 1), _MAX_PAGE)


def _decode_and_store_upload(
    org: str,
    *,
    filename: str | None,
    content_type: str | None,
    data_base64: str | None,
    ttl_days: int | None,
    field_prefix: str = "",
) -> dict[str, Any]:
    prefix = f"{field_prefix}." if field_prefix else ""
    clean_name = (filename or "").strip()
    if not clean_name:
        raise GraphQLError(
            f"{prefix}filename is required.",
            extensions={"code": "BAD_USER_INPUT", "field": f"{prefix}filename"},
        )
    resolved = uploads.resolve_content_type(clean_name, content_type or "")
    if not resolved:
        raise GraphQLError(
            "File type not allowed.",
            extensions={"code": "BAD_USER_INPUT", "field": f"{prefix}contentType"},
        )
    if not isinstance(data_base64, str) or not data_base64.strip():
        raise GraphQLError(
            f"{prefix}dataBase64 is required.",
            extensions={"code": "BAD_USER_INPUT", "field": f"{prefix}dataBase64"},
        )
    raw_b64 = data_base64.strip()
    if raw_b64.startswith("data:") and "," in raw_b64:
        raw_b64 = raw_b64.split(",", 1)[1].strip()
    try:
        body = base64.b64decode(raw_b64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise GraphQLError(
            f"{prefix}dataBase64 must be valid base64.",
            extensions={"code": "BAD_USER_INPUT", "field": f"{prefix}dataBase64"},
        ) from exc
    if not body:
        raise GraphQLError(
            "Uploaded file is empty.",
            extensions={"code": "BAD_USER_INPUT", "field": f"{prefix}dataBase64"},
        )
    if len(body) > uploads.MAX_BYTES:
        raise GraphQLError(
            "File too large (10 MB max).",
            extensions={"code": "BAD_USER_INPUT", "field": f"{prefix}dataBase64"},
        )
    if ttl_days is not None and (
        isinstance(ttl_days, bool) or not isinstance(ttl_days, int) or ttl_days < 1 or ttl_days > 365
    ):
        raise GraphQLError(
            f"{prefix}ttlDays must be between 1 and 365.",
            extensions={"code": "BAD_USER_INPUT", "field": f"{prefix}ttlDays"},
        )
    return uploads.save_blob_with_meta(
        org,
        clean_name,
        resolved,
        body,
        ttl_days=ttl_days,
    )


@query.field("issueDecisions")
def r_issue_decisions(_, info, issueId, status=None, first=None):  # noqa: N803
    store, _w, org, uid = _ctx(info)
    if status is not None and status not in decisiondocs.STATUSES:
        raise _bad(decisiondocs.DecisionError(
            f"status must be one of {list(decisiondocs.STATUSES)}.", "status"))
    return store.decisions_for_issue(org, uid, issueId, status=status, limit=_page(first))


@query.field("issueDecision")
def r_issue_decision(_, info, id):
    store, _w, org, uid = _ctx(info)
    row = store.decision_by_id(org, uid, id)
    if not row:
        raise _not_found()
    return row


@mutation.field("issueDecisionCreate")
def m_issue_decision_create(_, info, input):
    store, writer, org, uid = _ctx(info)
    try:
        decision_id = writer.decision_create(org, uid, input or {})
    except Exception as exc:  # DecisionError, InvalidReferenceError and GraphQLError share one wire shape
        raise _bad(exc) from exc
    return {"success": True, "decision": store.decision_by_id(org, uid, decision_id)}


@mutation.field("issueDecisionAnswer")
def m_issue_decision_answer(_, info, id, input):
    store, writer, org, uid = _ctx(info)
    try:
        _changed, replayed = writer.decision_answer(org, uid, id, input or {})
    except Exception as exc:
        raise _bad(exc) from exc
    return {"success": True, "replayed": replayed,
            "decision": store.decision_by_id(org, uid, id)}


@mutation.field("issueDecisionCancel")
def m_issue_decision_cancel(_, info, id):
    store, writer, org, uid = _ctx(info)
    try:
        changed = writer.decision_cancel(org, uid, id)
    except Exception as exc:
        raise _bad(exc) from exc
    if not changed:
        raise GraphQLError(
            "Only an open decision can be canceled.",
            extensions={"code": "BAD_USER_INPUT", "field": "id"})
    return {"success": True, "decision": store.decision_by_id(org, uid, id)}


@mutation.field("uploadMedia")
def m_upload_media(
    _,
    info,
    filename,
    dataBase64,  # noqa: N803
    contentType=None,  # noqa: N803
    ttlDays=None,  # noqa: N803
    issueId=None,  # noqa: N803
    title=None,
):
    store, _writer, org, uid = _ctx(info)
    issue_row = None
    if issueId is not None and str(issueId).strip():
        issue_row = store.issue_by_id_or_identifier(org, str(issueId).strip(), viewer_id=uid)
        if not issue_row:
            raise GraphQLError(
                "Issue not found.",
                extensions={"code": "NOT_FOUND", "field": "issueId"},
            )
    saved = _decode_and_store_upload(
        org,
        filename=filename,
        content_type=contentType,
        data_base64=dataBase64,
        ttl_days=ttlDays,
    )
    attachment = None
    if issue_row is not None:
        att_title = (title or "").strip() or saved["filename"]
        attachment = store.create_attachment(
            org,
            uid,
            {"issueId": issue_row["id"], "url": saved["url"], "title": att_title},
        )
    return {
        "success": True,
        "url": saved["url"],
        "filename": saved["filename"],
        "contentType": saved["content_type"],
        "size": saved["size"],
        "expiresAt": saved["expires_at"],
        "attachment": attachment,
    }


@mutation.field("issueReviewRequest")
def m_issue_review_request(_, info, input):
    store, writer, org, uid = _ctx(info)
    inp = input or {}
    issue_ref = (inp.get("issueId") or "").strip()
    if not issue_ref:
        raise GraphQLError(
            "issueId is required.",
            extensions={"code": "BAD_USER_INPUT", "field": "issueId"},
        )
    issue_row = store.issue_by_id_or_identifier(org, issue_ref, viewer_id=uid)
    if not issue_row:
        raise GraphQLError(
            "Issue not found.",
            extensions={"code": "NOT_FOUND", "field": "issueId"},
        )
    summary = (inp.get("summary") or "").strip()
    if not summary:
        raise GraphQLError(
            "summary is required.",
            extensions={"code": "BAD_USER_INPUT", "field": "summary"},
        )

    raw_question = inp.get("question")
    raw_options = inp.get("options")
    has_decision = bool((raw_question and str(raw_question).strip()) or raw_options)
    if has_decision:
        question_to_check = (
            raw_question
            if (raw_question is not None and str(raw_question).strip())
            else "Review request"
        )
        try:
            decisiondocs.parse_question(question_to_check)
            decisiondocs.parse_options(raw_options)
            decisiondocs.parse_context(summary)
            decisiondocs.parse_agent(inp.get("agent"))
        except Exception as exc:
            raise _bad(exc) from exc

    artifacts = inp.get("artifacts") or []
    attachment_urls: list[str] = []
    for idx, art in enumerate(artifacts):
        if not isinstance(art, dict):
            raise GraphQLError(
                f"artifacts[{idx}] must be an object.",
                extensions={"code": "BAD_USER_INPUT", "field": f"artifacts[{idx}]"},
            )
        if art.get("dataBase64") is not None:
            saved = _decode_and_store_upload(
                org,
                filename=art.get("filename") or art.get("title") or f"artifact-{idx + 1}.png",
                content_type=art.get("contentType"),
                data_base64=art.get("dataBase64"),
                ttl_days=art.get("ttlDays"),
                field_prefix=f"artifacts[{idx}]",
            )
            att_url = saved["url"]
            att_title = (art.get("title") or "").strip() or saved["filename"]
        elif art.get("url") is not None:
            try:
                validated_url = decisiondocs.validate_media_url(
                    art.get("url"), f"artifacts[{idx}].url"
                )
            except Exception as exc:
                raise _bad(exc) from exc
            if not validated_url:
                raise GraphQLError(
                    f"artifacts[{idx}].url must not be empty.",
                    extensions={"code": "BAD_USER_INPUT", "field": f"artifacts[{idx}].url"},
                )
            att_url = validated_url
            att_title = (
                (art.get("title") or "").strip()
                or (art.get("filename") or "").strip()
                or att_url.rsplit("/", 1)[-1]
                or f"artifact-{idx + 1}"
            )
        else:
            raise GraphQLError(
                f"artifacts[{idx}] requires either dataBase64 or url.",
                extensions={"code": "BAD_USER_INPUT", "field": f"artifacts[{idx}]"},
            )
        store.create_attachment(
            org,
            uid,
            {"issueId": issue_row["id"], "url": att_url, "title": att_title},
        )
        attachment_urls.append(att_url)

    if inp.get("moveToInReview") is not False:
        states = store.team_states(org, issue_row["_team_id"], viewer_id=uid)
        in_review = next(
            (s for s in states if (s.get("name") or "").strip().lower() == "in review"),
            None,
        )
        if in_review and issue_row.get("_state_id") != in_review["id"]:
            writer.issue_update(
                org,
                issue_row["id"],
                {"stateId": in_review["id"]},
                actor_id=uid,
            )

    writer.comment_create(
        org,
        uid,
        {"issueId": issue_row["id"], "body": summary},
    )

    decision = None
    if has_decision:
        question_text = (
            str(raw_question).strip()
            if (raw_question is not None and str(raw_question).strip())
            else "Review request"
        )
        try:
            decision_id = writer.decision_create(
                org,
                uid,
                {
                    "issueId": issue_row["id"],
                    "question": question_text,
                    "options": raw_options,
                    "context": summary,
                    "agent": inp.get("agent"),
                },
            )
        except Exception as exc:
            raise _bad(exc) from exc
        decision = store.decision_by_id(org, uid, decision_id)

    return {
        "success": True,
        "issue": store.issue_by_id_or_identifier(org, issue_row["id"], viewer_id=uid),
        "decision": decision,
        "attachmentUrls": attachment_urls,
    }


# ------------------------------------------------------------------- fields

def _user(store, org, user_id):
    return store.get_user(org, user_id) if user_id else None


@decision_type.field("issue")
def d_issue(obj, info):
    store, _w, org, uid = _ctx(info)
    return store.issue_by_id_or_identifier(org, obj["_issue_id"], viewer_id=uid)


@decision_type.field("askedBy")
def d_asked_by(obj, info):
    store, _w, org, _uid = _ctx(info)
    return _user(store, org, obj.get("_asked_by_id"))


@decision_type.field("answeredBy")
def d_answered_by(obj, info):
    store, _w, org, _uid = _ctx(info)
    return _user(store, org, obj.get("_answered_by_id"))
