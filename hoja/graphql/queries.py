"""GraphQL query strings.

These return read-only data. Each query template includes the fragments
it needs at the bottom.
"""

from hoja.graphql.fragments import (
    CYCLE_CORE,
    ISSUE_CORE,
    ISSUE_FULL,
    LABEL_CORE,
    PAGE_INFO,
    PROJECT_CORE,
    STATE_CORE,
    TEAM_CORE,
    USER_CORE,
    assemble,
)

# ---------------------------------------------------------------------------
# Viewer / Me
# ---------------------------------------------------------------------------

VIEWER = (
    """
    query Viewer {
      viewer {
        ...UserCore
        admin
        url
        timezone
        statusEmoji
        statusLabel
      }
    }
    """
    + assemble(USER_CORE)
)

# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------

TEAMS = (
    """
    query Teams($first: Int = 50, $after: String) {
      teams(first: $first, after: $after) {
        nodes { ...TeamCore }
        pageInfo { ...PageInfoCore }
      }
    }
    """
    + assemble(TEAM_CORE, PAGE_INFO)
)

TEAM_BY_KEY = (
    """
    query TeamByKey($key: String!) {
      teams(filter: { key: { eq: $key } }, first: 1) {
        nodes { ...TeamCore }
      }
    }
    """
    + assemble(TEAM_CORE)
)

TEAM_STATES = (
    """
    query TeamStates($teamId: String!) {
      team(id: $teamId) {
        id
        key
        name
        states {
          nodes { ...StateCore }
        }
      }
    }
    """
    + assemble(STATE_CORE)
)

TEAM_MEMBERS = (
    """
    query TeamMembers($teamId: String!, $first: Int = 100) {
      team(id: $teamId) {
        id
        key
        name
        members(first: $first) {
          nodes { ...UserCore }
        }
      }
    }
    """
    + assemble(USER_CORE)
)

# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------

ISSUES_LIST = (
    """
    query Issues($filter: IssueFilter, $first: Int = 50, $after: String, $orderBy: PaginationOrderBy) {
      issues(filter: $filter, first: $first, after: $after, orderBy: $orderBy) {
        nodes { ...IssueCore }
        pageInfo { ...PageInfoCore }
      }
    }
    """
    + assemble(ISSUE_CORE, USER_CORE, STATE_CORE, PAGE_INFO)
)

ISSUE_BY_IDENTIFIER = (
    """
    query IssueByIdentifier($id: String!) {
      issue(id: $id) {
        ...IssueFull
      }
    }
    """
    + assemble(ISSUE_FULL, ISSUE_CORE, USER_CORE, STATE_CORE, PROJECT_CORE, CYCLE_CORE, LABEL_CORE)
)

# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

PROJECTS_LIST = (
    """
    query Projects($filter: ProjectFilter, $first: Int = 50, $after: String) {
      projects(filter: $filter, first: $first, after: $after) {
        nodes { ...ProjectCore }
        pageInfo { ...PageInfoCore }
      }
    }
    """
    + assemble(PROJECT_CORE, PAGE_INFO)
)

PROJECT_BY_ID = (
    """
    query Project($id: String!) {
      project(id: $id) {
        ...ProjectCore
        lead { ...UserCore }
        creator { ...UserCore }
        members { nodes { ...UserCore } }
      }
    }
    """
    + assemble(PROJECT_CORE, USER_CORE)
)

# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------

TEAM_CYCLES = (
    """
    query TeamCycles($teamId: String!, $first: Int = 20) {
      team(id: $teamId) {
        id
        key
        cycles(first: $first) {
          nodes { ...CycleCore }
        }
        activeCycle { ...CycleCore }
      }
    }
    """
    + assemble(CYCLE_CORE)
)

# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

ISSUE_COMMENTS = (
    """
    query IssueComments($issueId: String!, $first: Int = 50) {
      issue(id: $issueId) {
        id
        identifier
        comments(first: $first) {
          nodes {
            id
            body
            url
            createdAt
            updatedAt
            user { ...UserCore }
          }
        }
      }
    }
    """
    + assemble(USER_CORE)
)

# ---------------------------------------------------------------------------
# Workflow / Labels
# ---------------------------------------------------------------------------

ISSUE_LABELS = (
    """
    query IssueLabels($filter: IssueLabelFilter, $first: Int = 100) {
      issueLabels(filter: $filter, first: $first) {
        nodes { ...LabelCore }
      }
    }
    """
    + assemble(LABEL_CORE)
)

# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

# searchIssues returns IssueSearchResult which extends Issue with metadata.
# We use inline fragment to spread Issue fields onto IssueSearchResult.
SEARCH_ISSUES = (
    """
    query SearchIssues($term: String!, $first: Int = 20) {
      searchIssues(term: $term, first: $first) {
        nodes {
          id
          identifier
          title
          priority
          priorityLabel
          url
          number
          createdAt
          updatedAt
          state { ...StateCore }
          assignee { ...UserCore }
          team { id key name }
        }
      }
    }
    """
    + assemble(USER_CORE, STATE_CORE)
)
