"""Read-only credential check for /graphql, kept free of optional dependencies.

`app.graphql_server` runs this on every request. It used to import it from `agent_chat`, which pulls in the
optional `agent` extra (openai, fastmcp, pydantic-ai), so a plain `pip install 'hoja-cli[server]'` answered every
GraphQL request with HTTP 500 (HOJA-757-A-2). Only graphql-core is needed here.
"""
from __future__ import annotations

from graphql import GraphQLError, OperationDefinitionNode, OperationType, parse


def read_only_violation(query: str, access: str) -> bool:
    """True when a read-only credential submits any operation other than a query."""
    if access != "read":
        return False
    try:
        document = parse(query)
    except GraphQLError:
        return False  # syntax errors are reported by graphql() without executing anything
    return any(
        isinstance(definition, OperationDefinitionNode) and definition.operation != OperationType.QUERY
        for definition in document.definitions
    )
