"""GraphQL mutation strings.

Mutations that modify state. All return `success` and the affected entity
so we can echo the result back to the user.
"""

from hoja.graphql.fragments import (
    CYCLE_CORE,
    ISSUE_CORE,
    ISSUE_FULL,
    LABEL_CORE,
    PROJECT_CORE,
    STATE_CORE,
    USER_CORE,
    assemble,
)

# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

ISSUE_CREATE = (
    """
    mutation IssueCreate($input: IssueCreateInput!) {
      issueCreate(input: $input) {
        success
        issue { ...IssueFull }
      }
    }
    """
    + assemble(ISSUE_FULL, ISSUE_CORE, USER_CORE, STATE_CORE, PROJECT_CORE, CYCLE_CORE, LABEL_CORE)
)

ISSUE_UPDATE = (
    """
    mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
      issueUpdate(id: $id, input: $input) {
        success
        issue { ...IssueCore }
      }
    }
    """
    + assemble(ISSUE_CORE, USER_CORE, STATE_CORE)
)

ISSUE_DELETE = """
    mutation IssueDelete($id: String!) {
      issueDelete(id: $id) {
        success
      }
    }
    """

ISSUE_ARCHIVE = """
    mutation IssueArchive($id: String!) {
      issueArchive(id: $id) {
        success
      }
    }
    """

# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

COMMENT_CREATE = """
    mutation CommentCreate($input: CommentCreateInput!) {
      commentCreate(input: $input) {
        success
        comment {
          id
          body
          url
          createdAt
        }
      }
    }
    """

COMMENT_UPDATE = """
    mutation CommentUpdate($id: String!, $input: CommentUpdateInput!) {
      commentUpdate(id: $id, input: $input) {
        success
        comment {
          id
          body
          url
          updatedAt
        }
      }
    }
    """

COMMENT_DELETE = """
    mutation CommentDelete($id: String!) {
      commentDelete(id: $id) {
        success
      }
    }
    """

# ---------------------------------------------------------------------------
# Labels
# ---------------------------------------------------------------------------

LABEL_CREATE = (
    """
    mutation LabelCreate($input: IssueLabelCreateInput!) {
      issueLabelCreate(input: $input) {
        success
        issueLabel { ...LabelCore }
      }
    }
    """
    + assemble(LABEL_CORE)
)

LABEL_DELETE = """
    mutation LabelDelete($id: String!) {
      issueLabelDelete(id: $id) {
        success
      }
    }
    """

# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

PROJECT_CREATE = (
    """
    mutation ProjectCreate($input: ProjectCreateInput!) {
      projectCreate(input: $input) {
        success
        project { ...ProjectCore }
      }
    }
    """
    + assemble(PROJECT_CORE)
)

PROJECT_UPDATE = (
    """
    mutation ProjectUpdate($id: String!, $input: ProjectUpdateInput!) {
      projectUpdate(id: $id, input: $input) {
        success
        project { ...ProjectCore }
      }
    }
    """
    + assemble(PROJECT_CORE)
)

PROJECT_ARCHIVE = """
    mutation ProjectArchive($id: String!) {
      projectArchive(id: $id) {
        success
      }
    }
    """
