"""Forecast analyst agent: a hand-written tool-calling loop on Amazon Bedrock's Converse API.

Originally a ✋ hand-written file; written by Claude at Chirayu's request (2026-11-22).

The loop (docs/SCOPE.md, Step 6):
1. Send the conversation + tool definitions to the model (Converse with toolConfig).
2. Check stopReason:
   - "tool_use": the model wants data. Run each requested tool, send the results back as a
     user message of toolResult blocks, and go to 1.
   - "end_turn": the model has answered. Return its text.
   - anything else (max_tokens, guardrail/content filter): stop and say so.
3. Give up after MAX_TURNS model calls, so a confused model can't loop forever.

The model is one setting (BEDROCK_MODEL_ID). Nothing here is specific to Nova or Claude.
"""

import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from functools import cache
from typing import Any, Protocol

import psycopg
from psycopg.types.json import Jsonb

from agents.prompts import system_prompt
from agents.tools import AnalystTools, ToolError

JSON = dict[str, Any]

AGENT_NAME = "forecast_analyst"
DEFAULT_MODEL_ID = "us.amazon.nova-2-lite-v1:0"
MAX_TURNS = 8  # model calls per question
MAX_TOKENS = 1024  # always explicit: an unset value reserves the model's maximum in quota

# Some models (Nova included) narrate their reasoning in <thinking> tags. Users see the answer only.
THINKING = re.compile(r"<thinking>.*?</thinking>", re.DOTALL)


class ConverseClient(Protocol):
    """The one boto3 bedrock-runtime method the loop uses (tests pass a fake)."""

    def converse(self, **kwargs: Any) -> JSON: ...


class AgentUnavailable(RuntimeError):
    """Bedrock can't be reached or refused us (expired AWS login, no model access, ...)."""


@dataclass
class ToolCall:
    name: str
    input: JSON
    ok: bool
    error: str | None = None


@dataclass
class AgentResult:
    question: str
    answer: str
    status: str  # answered | max_turns | stopped
    model_id: str
    turns: int = 0
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


def model_id() -> str:
    return os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)


@cache
def bedrock_client() -> ConverseClient:
    """One shared client (boto3 clients are thread-safe). Credentials come from the AWS CLI
    profile (AWS_PROFILE, refreshed with `aws sso login`), never from code or .env."""
    import boto3
    from botocore.config import Config

    session = boto3.Session(
        profile_name=os.environ.get("AWS_PROFILE") or None,
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )
    config = Config(retries={"max_attempts": 5, "mode": "adaptive"}, read_timeout=60)
    client: ConverseClient = session.client("bedrock-runtime", config=config)
    return client


def call_model(client: ConverseClient, model: str, messages: list[JSON], today: date) -> JSON:
    """One Converse call. Turns AWS login and access problems into AgentUnavailable."""
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

    try:
        return client.converse(
            modelId=model,
            system=[{"text": system_prompt(today)}],
            messages=messages,
            toolConfig={"tools": AnalystTools.specs},
            inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": 0},
        )
    except NoCredentialsError as exc:
        raise AgentUnavailable("No AWS credentials. Run: aws sso login --profile revops") from exc
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("ExpiredTokenException", "UnrecognizedClientException"):
            raise AgentUnavailable(
                "AWS login expired. Run: aws sso login --profile revops"
            ) from exc
        raise AgentUnavailable(f"Bedrock refused the request ({code}): {exc}") from exc
    except BotoCoreError as exc:
        # SSO token missing/expired surfaces here (TokenRetrievalError, SSOTokenLoadError, ...).
        raise AgentUnavailable(
            f"Can't reach Bedrock ({type(exc).__name__}). Try: aws sso login --profile revops"
        ) from exc


def run_tool(tools: AnalystTools, use: JSON) -> tuple[ToolCall, JSON]:
    """Run one requested tool. Errors go back to the model as an error result it can fix."""
    name, tool_input = str(use.get("name", "")), use.get("input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    try:
        output = tools.run(name, tool_input)
        call, status = ToolCall(name, tool_input, ok=True), "success"
    except ToolError as exc:
        output = {"error": str(exc)}
        call, status = ToolCall(name, tool_input, ok=False, error=str(exc)), "error"
    result = {"toolUseId": use["toolUseId"], "content": [{"json": output}], "status": status}
    return call, {"toolResult": result}


def answer_text(message: JSON) -> str:
    text = "".join(block.get("text", "") for block in message.get("content", []))
    return THINKING.sub("", text).strip()


def run_agent(
    question: str,
    *,
    client: ConverseClient,
    tools: AnalystTools,
    model: str,
    today: date,
) -> AgentResult:
    started = time.perf_counter()
    result = AgentResult(question=question, answer="", status="max_turns", model_id=model)
    messages: list[JSON] = [{"role": "user", "content": [{"text": question}]}]

    for turn in range(1, MAX_TURNS + 1):
        response = call_model(client, model, messages, today)
        result.turns = turn
        usage = response.get("usage", {})
        result.input_tokens += int(usage.get("inputTokens", 0))
        result.output_tokens += int(usage.get("outputTokens", 0))

        message = response["output"]["message"]
        messages.append(message)  # the assistant turn, including any toolUse blocks
        stop = response.get("stopReason")

        if stop == "tool_use":
            tool_results = []
            for block in message.get("content", []):
                if "toolUse" in block:
                    call, tool_result = run_tool(tools, block["toolUse"])
                    result.tool_calls.append(call)
                    tool_results.append(tool_result)
            messages.append({"role": "user", "content": tool_results})
            continue

        result.answer = answer_text(message)
        if stop == "end_turn" or stop == "stop_sequence":
            result.status = "answered"
        else:  # max_tokens, guardrail_intervened, content_filtered, ...
            result.status = "stopped"
            result.answer = result.answer or f"I had to stop early ({stop})."
        break
    else:
        result.answer = f"I couldn't finish within {MAX_TURNS} steps. Try a more specific question."

    result.latency_ms = int((time.perf_counter() - started) * 1000)
    return result


def ask(question: str, *, today: date | None = None) -> AgentResult:
    """Answer one question with the real Bedrock client and the real read-only tools."""
    return run_agent(
        question,
        client=bedrock_client(),
        tools=AnalystTools(),
        model=model_id(),
        today=today or date.today(),
    )


# ---------- Audit log ----------

INSERT_AGENT_LOG = """
INSERT INTO ops.agent_logs (agent, model_id, question, answer, status, turns, tool_calls,
                            input_tokens, output_tokens, latency_ms, error)
VALUES (%(agent)s, %(model_id)s, %(question)s, %(answer)s, %(status)s, %(turns)s, %(tool_calls)s,
        %(input_tokens)s, %(output_tokens)s, %(latency_ms)s, %(error)s)
RETURNING log_id
"""


def log_result(
    result: AgentResult | None,
    *,
    question: str,
    error: str | None = None,
    database_url: str | None = None,
) -> int:
    """Write one row to ops.agent_logs (as the main user: the agent role can't write)."""
    data = asdict(result) if result else {"answer": None, "status": "error", "turns": 0,
                                          "tool_calls": [], "input_tokens": 0,
                                          "output_tokens": 0, "latency_ms": 0}  # fmt: skip
    row = {
        **data,
        "agent": AGENT_NAME,
        "model_id": result.model_id if result else model_id(),
        "question": question,
        "tool_calls": Jsonb(json.loads(json.dumps(data["tool_calls"], default=str))),
        "error": error,
    }
    url = database_url or os.environ["DATABASE_URL"]
    with psycopg.connect(url, connect_timeout=5) as conn:
        inserted = conn.execute(INSERT_AGENT_LOG, row).fetchone()
    assert inserted is not None
    return int(inserted[0])
