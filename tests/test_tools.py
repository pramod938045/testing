import pytest

from app.tools import HANDLERS, TOOL_SPECS, execute_tool, tool_definitions

# Anything that would change Jira. None of these may exist as a tool.
WRITE_WORDS = ("create", "update", "delete", "add_", "transition_", "assign", "log_work", "comment_")


def test_every_spec_has_a_handler_and_vice_versa():
    assert {spec["name"] for spec in TOOL_SPECS} == set(HANDLERS)


def test_no_tool_can_change_jira():
    """The model cannot call what it is not given."""
    names = [spec["name"] for spec in TOOL_SPECS]
    offenders = [n for n in names if any(word in n for word in WRITE_WORDS)]
    assert offenders == [], f"write-shaped tools exposed to the model: {offenders}"
    # get_comments reads; there must be no tool for posting one.
    assert "add_comment" not in names
    assert "get_comments" in names


def test_schemas_satisfy_the_strict_tool_use_requirements():
    for spec in TOOL_SPECS:
        schema = spec["input_schema"]
        assert spec["description"], f"{spec['name']} needs a description"
        assert schema["additionalProperties"] is False, spec["name"]
        assert set(schema["required"]) <= set(schema["properties"]), spec["name"]
        for name, prop in schema["properties"].items():
            assert "type" in prop, f"{spec['name']}.{name}"


def test_definitions_are_strict():
    definitions = tool_definitions()
    assert all(spec["strict"] is True for spec in definitions)
    assert len(definitions) == len(TOOL_SPECS)


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
    result, is_error = await execute_tool(client, "create_issue", {})

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


@pytest.mark.parametrize("tool", ["get_issue", "get_comments", "list_transitions"])
async def test_read_tools_still_work(tool, make_client):
    client, _ = make_client(
        {
            "GET /rest/api/3/issue/ABC-1": {"key": "ABC-1", "fields": {"summary": "Fix login"}},
            "GET /rest/api/3/issue/ABC-1/comment": {"comments": []},
            "GET /rest/api/3/issue/ABC-1/transitions": {"transitions": []},
        }
    )
    _, is_error = await execute_tool(client, tool, {"key": "ABC-1"})
    assert is_error is False
    await client.aclose()
