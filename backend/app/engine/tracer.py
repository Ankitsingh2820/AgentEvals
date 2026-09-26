"""Records the steps of a run as trace rows, with latency measured on a monotonic clock.

Wall-clock timestamps (run.started_at/ended_at) are for display; every latency figure
comes from time.perf_counter(), which is immune to system clock adjustments.
"""

import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from app.models import LLMCall, Run, RunStep, ToolCall
from app.providers.base import LLMResponse, ToolCallRequest


@dataclass
class Timing:
    start_offset_ms: float = 0.0
    latency_ms: float = 0.0


class RunTracer:
    def __init__(self, run: Run):
        self.run = run
        self._t0 = time.perf_counter()
        self._seq = 0
        self.turn = 0  # advanced by the executor at each agent-loop LLM call

    def _offset_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000

    @contextmanager
    def timed(self):
        """Time a block; the Timing is filled in when the block exits (even on error)."""
        timing = Timing(start_offset_ms=self._offset_ms())
        try:
            yield timing
        finally:
            timing.latency_ms = self._offset_ms() - timing.start_offset_ms

    def _add_step(self, type_: str, timing: Timing, error: str | None) -> RunStep:
        self._seq += 1
        step = RunStep(
            sequence=self._seq,
            type=type_,
            status="failed" if error else "succeeded",
            start_offset_ms=timing.start_offset_ms,
            latency_ms=timing.latency_ms,
            error=error,
            turn=self.turn,
        )
        self.run.steps.append(step)
        return step

    def record_llm_call(
        self,
        timing: Timing,
        provider: str,
        model: str,
        response: LLMResponse | None,
        error: str | None = None,
        request: dict | None = None,
        purpose: str = "agent",
    ) -> None:
        step = self._add_step("llm_call", timing, error)
        self.run.llm_call_count += 1
        self.run.llm_latency_ms += timing.latency_ms
        if response is None:
            step.llm_call = LLMCall(
                provider=provider,
                model=model,
                purpose=purpose,
                request=request or {},
                response=None,
            )
            return

        step.llm_call = LLMCall(
            provider=provider,
            model=model,
            purpose=purpose,
            request=response.raw_request,
            response=response.raw_response,
            response_model=response.response_model,
            stop_reason=response.stop_reason,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cache_creation_input_tokens=response.cache_creation_input_tokens,
            cache_read_input_tokens=response.cache_read_input_tokens,
            total_tokens=(
                response.input_tokens
                + response.output_tokens
                + response.cache_creation_input_tokens
                + response.cache_read_input_tokens
            ),
            provider_request_id=response.provider_request_id,
        )
        self.run.input_tokens += response.input_tokens
        self.run.output_tokens += response.output_tokens
        self.run.cache_creation_input_tokens += response.cache_creation_input_tokens
        self.run.cache_read_input_tokens += response.cache_read_input_tokens

    def record_tool_call(
        self,
        timing: Timing,
        call: ToolCallRequest,
        result: str | None,
        error: str | None,
        skip_reason: str | None = None,
    ) -> None:
        """Record one tool call. Latency totals are added per batch (add_tool_wall_time),
        because calls in a parallel batch overlap in time."""
        step = self._add_step("tool_call", timing, error)
        step.tool_call = ToolCall(
            tool_name=call.name,
            tool_call_id=call.id,
            arguments=call.arguments,
            result=result,
            success=error is None,
            skip_reason=skip_reason,
        )
        if skip_reason:
            self.run.tool_calls_skipped += 1
            return
        self.run.tool_call_count += 1
        if error:
            self.run.tool_failure_count += 1

    def add_tool_wall_time(self, ms: float) -> None:
        self.run.tool_latency_ms += ms

    def finish(self, *, output: str | None, error: str | None) -> None:
        self.run.final_output = output
        self.run.error = error
        self.run.status = "failed" if error else "succeeded"
        self.run.ended_at = datetime.now(UTC)
        self.run.total_latency_ms = self._offset_ms()
