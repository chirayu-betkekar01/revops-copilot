"""The agent loop, driven by a scripted fake Bedrock client: no AWS, no cost, exact checks."""

import copy
from datetime import date
from typing import Any

import pytest
from botocore.exceptions import NoCredentialsError

from agents.analyst import MAX_TOKENS, MAX_TURNS, AgentUnavailable, run_agent
from agents.prompts import SCHEMA, system_prompt
from agents.sql_guard import ALLOWED_TABLES
from agents.tools import AnalystTools, ToolError

TODAY = date(2026, 11, 22)


def tool_use(name: str, tool_input: dict[str, Any], use_id: str = "t1") -> dict[str, Any]:
    return {
        "stopReason": "tool_use",
        "usage": {"inputTokens": 100, "outputTokens": 20},
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {"text": "<thinking>I need the forecast.</thinking>"},
                    {"toolUse": {"toolUseId": use_id, "name": name, "input": tool_input}},
                ],
            }
        },
    }


def final(text: str, stop: str = "end_turn") -> dict[str, Any]:
    return {
        "stopReason": stop,
        "usage": {"inputTokens": 150, "outputTokens": 30},
        "output": {"message": {"role": "assistant", "content": [{"text": text}]}},
    }


class FakeBedrock:
    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        # Snapshot the request like boto3 does when it sends it; the loop keeps appending to
        # the same messages list afterwards.
        self.calls.append(copy.deepcopy(kwargs))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


class FakeTools(AnalystTools):
    def __init__(self) -> None:
        self.ran: list[tuple[str, dict[str, Any]]] = []

    def run(self, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        self.ran.append((name, tool_input))
        if name == "run_sql" and "DELETE" in tool_input.get("query", ""):
            raise ToolError("Query rejected: Only SELECT queries are allowed")
        if name not in ("run_sql", "get_forecast_run", "compare_forecast_runs"):
            raise ToolError(f"Unknown tool {name!r}")
        return {"run_id": 2, "p50": "1631300.00"}


def ask(client: FakeBedrock, tools: FakeTools | None = None) -> Any:
    return run_agent(
        "What's our forecast?", client=client, tools=tools or FakeTools(), model="m", today=TODAY
    )


def test_tool_call_then_answer() -> None:
    client = FakeBedrock(tool_use("get_forecast_run", {}), final("<thinking>ok</thinking>$1.63M"))
    tools = FakeTools()
    result = ask(client, tools)

    assert result.status == "answered"
    assert result.answer == "$1.63M"  # <thinking> stripped
    assert tools.ran == [("get_forecast_run", {})]
    assert result.turns == 2
    assert (result.input_tokens, result.output_tokens) == (250, 50)
    # The second call carries: question, assistant toolUse turn, user toolResult turn.
    sent = client.calls[1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    tool_result = sent[2]["content"][0]["toolResult"]
    assert tool_result["toolUseId"] == "t1" and tool_result["status"] == "success"
    assert tool_result["content"][0]["json"]["p50"] == "1631300.00"


def test_every_call_sets_max_tokens_and_tools() -> None:
    client = FakeBedrock(tool_use("get_forecast_run", {}), final("done"))
    ask(client)
    for call in client.calls:
        assert call["inferenceConfig"]["maxTokens"] == MAX_TOKENS
        assert {t["toolSpec"]["name"] for t in call["toolConfig"]["tools"]} == {
            "run_sql", "get_forecast_run", "compare_forecast_runs",
        }  # fmt: skip


def test_rejected_tool_call_goes_back_to_the_model_as_an_error() -> None:
    client = FakeBedrock(
        tool_use("run_sql", {"query": "DELETE FROM marts.fct_deals"}),
        final("I can't delete data; I have read-only access."),
    )
    result = ask(client)

    assert result.status == "answered"
    assert result.tool_calls[0].ok is False
    assert "Only SELECT" in (result.tool_calls[0].error or "")
    tool_result = client.calls[1]["messages"][2]["content"][0]["toolResult"]
    assert tool_result["status"] == "error"


def test_unknown_tool_is_reported_not_crashed() -> None:
    result = ask(FakeBedrock(tool_use("drop_everything", {}), final("Sorry.")))
    assert result.tool_calls[0].ok is False


def test_loop_stops_after_max_turns() -> None:
    result = ask(FakeBedrock(tool_use("get_forecast_run", {})))  # never stops asking
    assert result.status == "max_turns"
    assert result.turns == MAX_TURNS
    assert "couldn't finish" in result.answer


def test_other_stop_reasons_stop_the_loop() -> None:
    result = ask(FakeBedrock(final("Partial ans", stop="max_tokens")))
    assert result.status == "stopped"
    assert result.answer == "Partial ans"


def test_missing_aws_login_becomes_agent_unavailable() -> None:
    class NoLogin(FakeBedrock):
        def converse(self, **kwargs: Any) -> dict[str, Any]:
            raise NoCredentialsError()

    with pytest.raises(AgentUnavailable, match="aws sso login"):
        ask(NoLogin(final("never")))


def test_prompt_describes_exactly_the_allowed_tables() -> None:
    for table in ALLOWED_TABLES:
        assert f"marts.{table}:" in SCHEMA
    assert "2026-11-22" in system_prompt(TODAY)


@pytest.mark.parametrize(
    ("today", "current", "last_completed"),
    [
        (date(2026, 11, 22), "2026 Q4", "2026 Q3"),
        (date(2026, 1, 1), "2026 Q1", "2025 Q4"),  # first day of a quarter
        (date(2026, 3, 31), "2026 Q1", "2025 Q4"),
    ],
)
def test_prompt_grounds_the_current_and_last_completed_quarter(
    today: date, current: str, last_completed: str
) -> None:
    prompt = system_prompt(today)
    assert f"The current quarter is {current}" in prompt
    assert f"The last completed quarter is {last_completed}" in prompt
