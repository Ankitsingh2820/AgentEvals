"""Deterministic offline provider, for tests and for running the platform without API keys.

Behaviour: on the first turn, if tools are available, it calls the first tool, passing
the user's input as the tool's first required argument (or scripted arguments). Once tool
results are present, it answers with a summary of them. Token counts are word counts, so traces are
reproducible byte-for-byte.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from app.providers.base import LLMRequest, LLMResponse, ToolCallRequest, ToolSpec


def _default_arguments(tool: ToolSpec, user_text: str) -> dict[str, Any]:
    required = tool.input_schema.get("required") or list(tool.input_schema.get("properties", {}))
    return {required[0]: user_text} if required else {}


@dataclass
class MockProvider:
    name: str = "mock"
    prompt_caching: str = "opt_in"  # simulates an opt-in cache (see complete())
    # Optional fixed arguments per tool name, e.g. {"calculator": {"expression": "2+2"}}.
    tool_arguments: dict[str, dict[str, Any]] = field(default_factory=dict)
    # How many of the available tools to call in the first turn (all in one turn).
    calls_per_turn: int = 1
    judge_score: int = 4
    # Label returned to a routing classifier; None = the first allowed label.
    route_label: str | None = None

    def complete(self, request: LLMRequest) -> LLMResponse:
        if request.json_schema is not None:
            return self._structured_reply(request)
        last = request.messages[-1]
        prompt_words = len(request.system.split()) + sum(_words(m) for m in request.messages)

        if last.role == "user" and request.tools:
            calls = [
                ToolCallRequest(
                    id=f"mock_call_{len(request.messages)}_{i}",
                    name=tool.name,
                    arguments=self.tool_arguments.get(tool.name)
                    or _default_arguments(tool, last.text),
                )
                for i, tool in enumerate(request.tools[: self.calls_per_turn])
            ]
            text, stop = "", "tool_use"
        else:
            results = [r.content for r in last.tool_results]
            text = "Mock answer. " + (" | ".join(results) if results else last.text)
            calls, stop = [], "end_turn"

        output_words = len(text.split()) + sum(len(str(c.arguments).split()) for c in calls)
        uncached, created, read = prompt_words, 0, 0
        if request.cache:
            # SIMULATED prompt cache: the previous turns are a cache hit, the newest
            # message is written to the cache. Real providers report their own numbers.
            newest = _words(last)
            if len(request.messages) == 1:
                uncached, created = 0, prompt_words
            else:
                uncached, created, read = 0, newest, prompt_words - newest
        return LLMResponse(
            text=text,
            tool_calls=calls,
            stop_reason=stop,
            input_tokens=uncached,
            output_tokens=output_words,
            cache_creation_input_tokens=created,
            cache_read_input_tokens=read,
            response_model=request.model,
            raw_request={"model": request.model, "messages": len(request.messages)},
            raw_response={"text": text, "tool_calls": [c.__dict__ for c in calls]},
        )

    def _structured_reply(self, request: LLMRequest) -> LLMResponse:
        """Structured-output requests get fixed, clearly labelled placeholder answers: a
        judge verdict, or a routing label. They exercise the pipeline and measure nothing."""
        props = request.json_schema.get("properties", {})
        if "label" in props:
            labels = props["label"]["enum"]
            payload = {
                "reasoning": "MOCK ROUTER: fixed label.",
                "label": self.route_label or labels[0],
            }
        else:
            payload = {
                "reasoning": "MOCK JUDGE: placeholder verdict, not a real assessment.",
                "score": self.judge_score,
            }
        text = json.dumps(payload)
        return LLMResponse(
            text=text,
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=len(request.messages[-1].text.split()),
            output_tokens=len(text.split()),
            response_model=request.model,
            raw_request={"model": request.model, "json_schema": True},
            raw_response={"text": text},
        )


def _words(m) -> int:
    return len(m.text.split()) + sum(len(r.content.split()) for r in m.tool_results)
