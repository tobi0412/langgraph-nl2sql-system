"""Unit tests: Schema Agent routing."""

from langchain_core.messages import AIMessage, HumanMessage

from graph.schema_edges import route_after_schema_agent


def test_route_to_tools_when_tool_calls_and_under_limit():
    state = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "mcp_schema_inspect",
                        "args": {},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
        ],
        "iteration": 1,
        "max_iterations": 10,
    }
    assert route_after_schema_agent(state) == "tools"


def test_route_to_format_when_no_tool_calls():
    state = {
        "messages": [
            AIMessage(content='{"tables":[]}'),
        ],
        "iteration": 1,
        "max_iterations": 10,
    }
    assert route_after_schema_agent(state) == "format_draft"


def test_route_to_format_when_max_iterations_reached():
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "mcp_schema_inspect",
                        "args": {},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            ),
        ],
        "iteration": 10,
        "max_iterations": 10,
    }
    assert route_after_schema_agent(state) == "format_draft"
