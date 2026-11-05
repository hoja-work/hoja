"""Contract tests for the deliberately fixed, safe local MCP surface."""

from __future__ import annotations

import pytest


def test_local_mcp_exposes_only_guide_tool_and_topic_enum() -> None:
    pytest.importorskip("mcp")
    from hoja.mcp.server import _build_server

    server = _build_server()
    tools = server._tool_manager.list_tools()
    assert [tool.name for tool in tools] == ["hoja_guide"]
    schema = tools[0].parameters
    assert set(schema["properties"]) == {"topic"}
    assert schema["$defs"]["Topic"]["enum"] == [
        "overview", "commands", "workflows", "filters", "output-formats", "examples"
    ]


def test_guide_rejects_unknown_topic_and_returns_valid_guide() -> None:
    from hoja.mcp.content import Topic, load_topic

    result = load_topic(Topic.OVERVIEW)
    assert result.topic is Topic.OVERVIEW
    assert result.instructions and result.reminder
    with pytest.raises(ValueError):
        Topic("delete-everything")
