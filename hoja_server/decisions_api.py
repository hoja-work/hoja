"""GraphQL surface for decisions (docs/decisions.md).

The SDL lives here rather than in schema.graphql for the same reason views_api.py does: a
feature owns its types, its resolvers, and its errors in one module that build_schema()
composes. Authorization and the issue flag live in Writer/Store, so both the browser and an
agent's API key go through the same rules.
"""
from __future__ import annotations

from ariadne import MutationType, ObjectType, QueryType, gql
from graphql import GraphQLError

from hoja_server import decisions as decisiondocs

SDL = gql(
    """
    type IssueDecision {
      id: ID!
      issue: Issue!
      question: String!
      options: JSON!
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
    input DecisionOptionInput {
      "Stable key the answer refers to; generated from the label when omitted."
      key: String
      label: String!
      description: String
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
    extend type Query {
      issueDecisions(issueId: String!, status: String, first: Int = 50): [IssueDecision!]!
      issueDecision(id: String!): IssueDecision!
    }
    extend type Mutation {
      issueDecisionCreate(input: IssueDecisionCreateInput!): IssueDecisionPayload!
      issueDecisionAnswer(id: String!, input: IssueDecisionAnswerInput!): IssueDecisionAnswerPayload!
      issueDecisionCancel(id: String!): IssueDecisionPayload!
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
