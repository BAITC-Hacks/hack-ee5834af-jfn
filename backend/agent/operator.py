"""Small bounded Responses API loop; numerical work stays in ForecastService."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from backend.workflow import ForecastService

from .prompts import SYSTEM_PROMPT
from .tools import AgentTools, RunContext, tool_schemas


class OpenAIUnavailable(RuntimeError):
    pass


class OpenAIResponses:
    """Minimal HTTP transport; the API key is only read from the process environment."""

    def __init__(self, model: str = "gpt-4.1-mini"):
        self.model = model

    def respond(self, input_items: list[dict[str, Any]],
                tools: list[dict[str, Any]], timeout: float) -> dict[str, Any]:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise OpenAIUnavailable("OPENAI_API_KEY is not set in the process environment")
        payload = {"model": self.model, "instructions": SYSTEM_PROMPT,
                   "input": input_items, "tools": tools, "store": False,
                   "parallel_tool_calls": False, "max_output_tokens": 600}
        data = json.dumps(payload).encode("utf-8")
        http_request = request.Request("https://api.openai.com/v1/responses", data=data,
                                       headers={"Authorization": "Bearer " + key,
                                                "Content-Type": "application/json"},
                                       method="POST")
        try:
            with request.urlopen(http_request, timeout=timeout) as response:
                return json.load(response)
        except error.HTTPError as exc:
            # Avoid logging response bodies or request headers containing secrets.
            raise OpenAIUnavailable(f"OpenAI HTTP {exc.code}") from exc
        except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise OpenAIUnavailable("OpenAI request failed") from exc


@dataclass
class OperatorResult:
    execution: str
    published: bool
    run_id: str | None
    reason: str
    trace: list[dict[str, Any]]
    forecast: dict[str, Any] | None

    def to_json(self) -> dict[str, Any]:
        return {"execution": self.execution, "published": self.published,
                "run_id": self.run_id, "reason": self.reason,
                "trace": self.trace, "forecast": self.forecast}


class ForecastOperator:
    def __init__(self, service: ForecastService, context: RunContext,
                 client: Any | None = None, *, max_tool_calls: int = 10,
                 max_seconds: float = 45, unavailable: frozenset[str] = frozenset()):
        if max_tool_calls < 1 or max_seconds <= 0:
            raise ValueError("positive tool and time limits are required")
        self.service = service
        self.context = context
        self.client = client or OpenAIResponses()
        self.max_tool_calls = max_tool_calls
        self.max_seconds = max_seconds
        self.unavailable = unavailable

    def _tools(self) -> AgentTools:
        return AgentTools(self.service, self.context, self.unavailable)

    def _result(self, tools: AgentTools, execution: str, reason: str) -> OperatorResult:
        tools.decision(reason, execution)
        forecast = self.service.get_forecast(tools.run_id) if tools.run_id else None
        return OperatorResult(execution, tools.published, tools.run_id, reason,
                              list(tools.trace), forecast)

    def run_live(self, *, allow_fallback: bool = True) -> OperatorResult:
        tools = self._tools()
        deadline = time.monotonic() + self.max_seconds
        input_items: list[dict[str, Any]] = [{"role": "user", "content": json.dumps({
            "as_of": self.context.as_of, "horizon": self.context.horizon,
            "model_id": self.context.model_id, "weather_candidates": self.context.candidates,
            "instruction": "Choose a valid candidate and complete the workflow."})}]
        calls = 0
        try:
            while calls < self.max_tool_calls and time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                response = self.client.respond(input_items, tool_schemas(self.context.candidates,
                                                                          self.context.model_id),
                                              max(0.1, remaining))
                output = response.get("output", [])
                if not isinstance(output, list):
                    raise OpenAIUnavailable("OpenAI output is malformed")
                input_items.extend(output)
                function_calls = [item for item in output if isinstance(item, dict)
                                  and item.get("type") == "function_call"]
                if not function_calls:
                    break
                for call in function_calls:
                    if calls >= self.max_tool_calls or time.monotonic() >= deadline:
                        raise OpenAIUnavailable("operator budget exceeded")
                    calls += 1
                    try:
                        arguments = json.loads(call["arguments"])
                        if not isinstance(arguments, dict):
                            arguments = {}
                    except (KeyError, TypeError, json.JSONDecodeError):
                        arguments = {}
                    outcome = tools.invoke(str(call.get("name", "")), arguments,
                                           call_id=call["call_id"],
                                           response_id=response.get("id"))
                    input_items.append({"type": "function_call_output",
                                        "call_id": call["call_id"],
                                        "output": json.dumps(outcome, sort_keys=True)})
                    if tools.published:
                        return self._result(tools, "live_openai",
                                            "OpenAI tool sequence passed the deterministic publication gate")
            reason = "OpenAI did not complete a publishable tool sequence within its budget"
        except (OpenAIUnavailable, KeyError, TypeError, ValueError) as exc:
            reason = str(exc)[:300]
        if allow_fallback:
            return self._fallback(tools, reason, self.max_tool_calls - calls, deadline)
        return self._result(tools, "live_openai", reason)

    def run_recorded(self, calls: list[tuple[str, dict[str, Any]]]) -> OperatorResult:
        """Replay an explicitly recorded tool sequence; never label it as live OpenAI."""
        tools = self._tools()
        started = time.monotonic()
        for name, arguments in calls[:self.max_tool_calls]:
            if time.monotonic() - started >= self.max_seconds:
                break
            tools.invoke(name, arguments)
            if tools.published:
                break
        reason = "recorded tool sequence completed" if tools.published else "recorded tool sequence did not publish"
        return self._result(tools, "recorded", reason)

    def _fallback(self, tools: AgentTools, cause: str, budget: int,
                  deadline: float) -> OperatorResult:
        def invoke(name: str, args: dict[str, Any]) -> dict[str, Any]:
            nonlocal budget
            if budget <= 0 or time.monotonic() >= deadline:
                return {"ok": False, "reason": "operator budget exceeded"}
            budget -= 1
            return tools.invoke(name, args)

        if tools.run_id is None:
            for snapshot_id in self.context.candidates:
                if not invoke("get_weather_forecast", {"snapshot_id": snapshot_id})["ok"]:
                    continue
                if not invoke("validate_inputs", {})["ok"]:
                    continue
                if invoke("run_forecast", {"model_id": self.context.model_id})["ok"]:
                    break
        if tools.run_id is not None and invoke("validate_forecast", {})["ok"]:
            invoke("publish_forecast", {})
        reason = ("deterministic fallback after OpenAI failure: " + cause)[:500]
        return self._result(tools, "deterministic_fallback", reason)
