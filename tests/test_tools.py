import pytest

from app.tools import HANDLERS, TOOL_SPECS, WRITE_TOOLS, execute_tool, tool_definitions


def test_every_spec_has_a_handler_and_vice_versa():
    spec_names = {spec["name"] for spec in TOOL_SPECS}
    assert spec_names == set(HANDLERS)
    assert WRITE_TOOLS <= spec_names


def test_schemas_satisfy_the_strict_tool_use_requirements():
    for spec in TOOL_SPECS:
        schema = spec["input_schema"]
        assert spec["description"], f"{spec['name']} needs a description"
        assert schema["additionalProperties"] is False, spec["name"]
        assert set(schema["required"]) <= set(schema["properties"]), spec["name"]
        for name, prop in schema["properties"].items():
            assert "type" in prop, f"{spec['name']}.{name}"


def test_definitions_are_strict_and_writes_can_be_withheld():
    full = tool_definitions(allow_writes=True)
    assert all(spec["strict"] is True for spec in full)
    assert len(full) == len(TOOL_SPECS)

    read_only = {spec["name"] for spec in tool_definitions(allow_writes=False)}
    assert not (read_only & WRITE_TOOLS)
    assert "search_issues" in read_only


async def test_read_only_mode_refuses_write_tools_without_touching_jira(make_client):
    client, seen = make_client({})
    result, is_error = await execute_tool(
        client, "add_comment", {"key": "ABC-1", "body": "hi"}, allow_writes=False
    )

    assert is_error is True
    assert "read-only" in result
    assert seen == []
    await client.aclose()


async def test_jira_failures_come_back_as_tool_errors_not_exceptions(make_client):
    client, _ = make_client({"GET /rest/api/3/issue/ABC-1": (404, {"errorMessages": ["Issue does not exist"]})})
    result, is_error = await execute_tool(client, "get_issue", {"key": "ABC-1"})

    assert is_error is True
    assert "Issue does not exist" in result
    await client.aclose()


async def test_missing_required_argument_is_reported_as_a_tool_error(make_client):
    client, _ = make_client({})
    result, is_error = await execute_tool(client, "get_issue", {})

    assert is_error is True
    assert "Invalid arguments" in result
    await client.aclose()


async def test_unknown_tool_name_is_reported(make_client):
    client, _ = make_client({})
    result, is_error = await execute_tool(client, "delete_everything", {})

    assert is_error is True
    assert "Unknown tool" in result
    await client.aclose()


async def test_search_results_are_capped_at_100(make_client):
    import json

    client, seen = make_client({"POST /rest/api/3/search/jql": {"issues": []}})
    _, is_error = await execute_tool(client, "search_issues", {"jql": "order by created", "max_results": 5000})

    assert is_error is False
    assert json.loads(seen[0].content)["maxResults"] == 100
    await client.aclose()
