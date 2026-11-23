"""Ask the forecast analyst a question from the terminal.

    just ask "What's our forecast this quarter?"

Prints the answer, the tools it used (including the exact SQL) and the token cost,
and logs the call to ops.agent_logs.
"""

import argparse
import sys

from agents.analyst import AgentUnavailable, ask, log_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the forecast analyst agent")
    parser.add_argument("question")
    args = parser.parse_args()

    try:
        result = ask(args.question)
    except AgentUnavailable as exc:
        log_result(None, question=args.question, error=str(exc))
        print(f"Agent unavailable: {exc}", file=sys.stderr)
        return 1

    log_id = log_result(result, question=args.question)
    print(result.answer)
    print(f"\n--- {result.status}, {result.turns} model calls, "
          f"{result.input_tokens} in / {result.output_tokens} out tokens, "
          f"{result.latency_ms} ms, {result.model_id}, log {log_id}")  # fmt: skip
    for call in result.tool_calls:
        detail = call.input.get("query", call.input) if call.name == "run_sql" else call.input
        print(f"  {'ok ' if call.ok else 'ERR'} {call.name}: {detail}")
        if call.error:
            print(f"      {call.error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
