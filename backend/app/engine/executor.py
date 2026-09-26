"""Agent execution engine: runs one agent version on one input and records the full trace.

The loop is: call the model -> execute any tools it requested -> feed results back ->
repeat until it answers without tool calls, fails, or hits the iteration ceiling.
Failures never raise out of execute_run; they are recorded on the run instead, so every
run (successful or not) is persisted and inspectable.
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from opentelemetry import context as otel_context
from opentelemetry.trace import Status, StatusCode
from sqlalchemy.orm import Session

from app.config import get_settings
from app.costs.service import apply_costs
from app.engine.options import AgentOptions
from app.engine.router import route_by_classifier, route_by_rules
from app.engine.tracer import RunTracer
from app.models import AgentVersion, Run
from app.providers import get_provider
from app.providers.base import (
    LLMRequest,
    Message,
    ProviderError,
    ToolCallRequest,
    ToolResult,
)
from app.telemetry import tracer as otel
from app.tools import get_tool

logger = logging.getLogger(__name__)


def execute_run(
    db: Session,
    version: AgentVersion,
    user_input: str,
    experiment_id: str | None = None,
    evaluation_id: str | None = None,
    dataset_case_id: str | None = None,
    repetition: int = 0,
) -> Run:
    run = Run(
        agent_id=version.agent_id,
        agent_version_id=version.id,
        experiment_id=experiment_id,
        evaluation_id=evaluation_id,
        dataset_case_id=dataset_case_id,
        repetition=repetition,
        input=user_input,
        steps=[],  # start loaded, so appending steps mid-run never triggers a DB read
    )
    db.add(run)
    db.commit()  # persist immediately so an in-flight run is visible
    log = {"run_id": run.id, "agent_id": version.agent_id, "agent_version": version.version}
    logger.info("run started", extra=log)

    with otel.start_as_current_span(
        "agent.run",
        attributes={
            "agenteval.run.id": run.id,
            "agenteval.agent.id": version.agent_id,
            "agenteval.agent.version": version.version,
            "gen_ai.system": version.provider,
            "gen_ai.request.model": version.model,
        },
    ) as span:
        tracer = RunTracer(run)
        try:
            output, error = _agent_loop(tracer, version, user_input)
        except Exception as e:  # defensive: an engine bug must still close the run
            logger.exception("run crashed", extra=log)
            output, error = None, f"internal error: {type(e).__name__}"
        tracer.finish(output=output, error=error)
        db.flush()  # assigns ids/defaults the cost step reads
        try:
            apply_costs(db, run)
        except Exception:
            # Pricing problems must not lose the run itself; the trace is still saved.
            logger.exception("cost calculation failed", extra=log)
            run.cost_status = "error"
        db.commit()
        span.set_attributes(
            {
                "agenteval.run.status": run.status,
                "agenteval.run.llm_calls": run.llm_call_count,
                "agenteval.run.tool_calls": run.tool_call_count,
                "agenteval.run.retries": run.retry_count,
                "gen_ai.usage.input_tokens": run.input_tokens,
                "gen_ai.usage.output_tokens": run.output_tokens,
                "agenteval.cost.estimated": float(run.estimated_total_cost or 0),
                "agenteval.cost.status": run.cost_status or "",
            }
        )
        if error:
            span.set_status(Status(StatusCode.ERROR, error[:200]))

    logger.info(
        "run finished",
        extra={
            **log,
            "status": run.status,
            "latency_ms": round(run.total_latency_ms or 0, 1),
            "llm_calls": run.llm_call_count,
            "tool_calls": run.tool_call_count,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "estimated_cost": str(run.estimated_total_cost),
            "cost_status": run.cost_status,
        },
    )
    return run


def _agent_loop(
    tracer: RunTracer, version: AgentVersion, user_input: str
) -> tuple[str | None, str | None]:
    """Returns (final_output, error)."""
    provider = get_provider(version.provider)
    opts = AgentOptions.model_validate(version.options or {})
    model = _choose_model(tracer, provider, opts, version, user_input)
    tools = [get_tool(name).spec for name in version.tools]
    messages = [Message(role="user", text=user_input)]
    max_iterations = get_settings().max_agent_iterations
    tool_state = _ToolState(opts)

    for _ in range(max_iterations):
        tracer.turn += 1
        request = LLMRequest(
            model=model,
            system=version.system_prompt,
            messages=list(messages),
            max_tokens=version.max_tokens,
            temperature=version.temperature,
            tools=tools,
            cache=opts.prompt_caching,
        )
        response, call_error = _call_llm(tracer, provider, request)
        if response is None:
            return None, call_error

        if response.stop_reason == "refusal":
            return response.text or None, "model refused the request"

        if not response.tool_calls:
            if response.stop_reason == "max_tokens":
                return response.text, "output truncated: max_tokens reached"
            return response.text, None

        messages.append(
            Message(
                role="assistant",
                text=response.text,
                tool_calls=response.tool_calls,
                provider_content=response.provider_content,
            )
        )
        results = _run_tools(tracer, response.tool_calls, tool_state, opts.parallel_tool_calls)
        messages.append(Message(role="tool", tool_results=results))

    return None, f"max iterations ({max_iterations}) reached without a final answer"


def _call_llm(tracer: RunTracer, provider, request: LLMRequest, purpose: str = "agent"):
    """One logical LLM call with retries. Every attempt is a traced step, so retries show
    up in the timeline, the latency and the retry metrics instead of hiding in the SDK.
    Only transient failures (rate limits, overload, connection) are retried."""
    settings = get_settings()
    for attempt in range(settings.llm_max_retries + 1):
        with otel.start_as_current_span(
            f"chat {request.model}",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.system": provider.name,
                "gen_ai.request.model": request.model,
                "agenteval.purpose": purpose,
                "agenteval.turn": tracer.turn,
                "agenteval.attempt": attempt,
            },
        ) as span:
            with tracer.timed() as timing:
                try:
                    response, error, retryable = provider.complete(request), None, False
                except ProviderError as e:
                    response, error, retryable = None, str(e), e.retryable
            if response is not None:
                span.set_attributes(
                    {
                        "gen_ai.response.model": response.response_model or "",
                        "gen_ai.usage.input_tokens": response.input_tokens,
                        "gen_ai.usage.output_tokens": response.output_tokens,
                        "gen_ai.response.finish_reasons": [response.stop_reason or ""],
                    }
                )
            else:
                span.set_status(Status(StatusCode.ERROR, error[:200]))
        tracer.record_llm_call(
            timing,
            provider.name,
            request.model,
            response,
            error,
            request={"model": request.model, "messages": len(request.messages), "attempt": attempt},
            purpose=purpose,
        )
        if response is not None or not retryable or attempt == settings.llm_max_retries:
            return response, error
        tracer.run.retry_count += 1
        time.sleep(settings.llm_retry_base_seconds * 2**attempt)
    return None, "unreachable"


def _choose_model(tracer: RunTracer, provider, opts: AgentOptions, version, text: str) -> str:
    cfg = opts.routing
    if cfg is None:
        return version.model
    if cfg.mode == "rules":
        decision = route_by_rules(cfg, text, version.model)
    else:
        with tracer.timed() as timing:
            decision = route_by_classifier(cfg, text, version.model, provider)
        tracer.record_llm_call(
            timing,
            provider.name,
            cfg.classifier_model,
            decision.classifier_response,
            decision.classifier_error if decision.classifier_response is None else None,
            request={"model": cfg.classifier_model, "purpose": "routing"},
            purpose="routing",
        )
    tracer.run.route = decision.label
    tracer.run.routed_model = decision.model
    return decision.model


class _ToolState:
    """Per-run memory for the dedupe and tool-budget options."""

    def __init__(self, opts: AgentOptions):
        self.dedupe = opts.dedupe_tool_calls
        self.budget = opts.max_tool_calls
        self.executed = 0
        self.seen: dict[str, tuple[str | None, str | None]] = {}  # key -> (result, error)

    @staticmethod
    def key(call: ToolCallRequest) -> str:
        return f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"


def _run_tools(
    tracer: RunTracer, calls: list[ToolCallRequest], state: _ToolState, parallel: bool
) -> list[ToolResult]:
    """Execute one turn's tool calls and record each as a step, in the model's order."""
    # Decide what each call will do before running anything.
    plan: list[tuple[str, str | None]] = []  # (action, key): execute | duplicate | budget
    batch_keys: set[str] = set()
    for call in calls:
        key = state.key(call)
        if state.dedupe and (key in state.seen or key in batch_keys):
            plan.append(("duplicate", key))
        elif state.budget is not None and state.executed >= state.budget:
            plan.append(("budget", key))
        else:
            plan.append(("execute", key))
            batch_keys.add(key)
            state.executed += 1

    to_run = [c for c, (action, _) in zip(calls, plan, strict=True) if action == "execute"]
    with tracer.timed() as wall:
        if parallel and len(to_run) > 1:
            parent = otel_context.get_current()
            with ThreadPoolExecutor(max_workers=min(len(to_run), 8)) as pool:
                outcomes = list(pool.map(lambda c: _execute_tool(tracer, c, parent), to_run))
        else:
            outcomes = [_execute_tool(tracer, c) for c in to_run]
    tracer.add_tool_wall_time(wall.latency_ms)

    executed = iter(outcomes)
    results = []
    for call, (action, key) in zip(calls, plan, strict=True):
        if action == "execute":
            timing, result, error = next(executed)
            tracer.record_tool_call(timing, call, result, error)
            if state.dedupe:
                state.seen[key] = (result, error)
        else:
            if action == "duplicate":
                result, error = state.seen[key]
            else:
                result, error = (
                    None,
                    (
                        f"tool call budget exhausted ({state.budget} per run); answer with the "
                        "information you already have"
                    ),
                )
            with tracer.timed() as timing:
                pass  # not executed: zero-length step at this point in the timeline
            tracer.record_tool_call(timing, call, result, error, skip_reason=action)
        results.append(
            ToolResult(call.id, f"Error: {error}", is_error=True)
            if error
            else ToolResult(call.id, result)
        )
    return results


def _execute_tool(tracer: RunTracer, call: ToolCallRequest, parent=None):
    """Run one tool. Thread-safe: only reads the tracer's clock. `parent` is the span
    context of the calling thread (worker threads don't inherit it)."""
    token = otel_context.attach(parent) if parent is not None else None
    try:
        with otel.start_as_current_span(
            f"execute_tool {call.name}",
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": call.name,
                "agenteval.turn": tracer.turn,
            },
        ) as span:
            timing, result, error = _run_tool_fn(tracer, call)
            if error:
                span.set_status(Status(StatusCode.ERROR, error[:200]))
        return timing, result, error
    finally:
        if token is not None:
            otel_context.detach(token)


def _run_tool_fn(tracer: RunTracer, call: ToolCallRequest):
    with tracer.timed() as timing:
        try:
            tool = get_tool(call.name)
        except KeyError:
            tool = None
        if tool is None:
            result, error = None, f"unknown tool '{call.name}'"
        else:
            try:
                result, error = tool.fn(**call.arguments), None
            except Exception as e:
                result, error = None, f"{type(e).__name__}: {e}"
    return timing, result, error
