from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.engine.options import AgentOptions


class AgentConfig(BaseModel):
    """The versioned part of an agent. Any change produces a new AgentVersion."""

    provider: str = Field(examples=["anthropic"])
    model: str = Field(min_length=1, max_length=100, examples=["claude-opus-5"])
    system_prompt: str = ""
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int = Field(default=16000, ge=1, le=128000)
    tools: list[str] = Field(default_factory=list)
    rag_config: dict[str, Any] | None = None
    eval_config: dict[str, Any] | None = None
    options: AgentOptions = Field(default_factory=AgentOptions)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentCreate(AgentConfig):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    tags: list[str] = Field(default_factory=list)


class AgentVersionOut(AgentConfig):
    model_config = ConfigDict(from_attributes=True)

    id: str
    version: int
    created_at: datetime
    metadata: dict[str, Any] = Field(validation_alias="metadata_")


class AgentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    tags: list[str]
    created_at: datetime
    latest_version: AgentVersionOut


class RunCreate(BaseModel):
    agent_id: str
    input: str = Field(min_length=1, max_length=200_000)
    # Pin a specific version; defaults to the agent's latest.
    agent_version_id: str | None = None


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    agent_version_id: str
    experiment_id: str | None
    evaluation_id: str | None
    dataset_case_id: str | None
    repetition: int
    status: str
    input: str
    final_output: str | None
    error: str | None
    started_at: datetime
    ended_at: datetime | None
    total_latency_ms: float | None
    llm_call_count: int
    tool_call_count: int
    tool_failure_count: int
    tool_calls_skipped: int
    retry_count: int
    route: str | None
    routed_model: str | None
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    llm_latency_ms: float
    tool_latency_ms: float
    # Estimates from the pricing registry, not provider-billed amounts. Decimals serialize
    # as strings to stay exact. Totals are null unless cost_status is "complete".
    estimated_llm_cost: Decimal | None
    estimated_tool_cost: Decimal | None
    estimated_total_cost: Decimal | None
    currency: str | None
    cost_status: str | None


class LLMCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    model: str
    purpose: str
    response_model: str | None
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    total_tokens: int
    provider_request_id: str | None
    pricing_snapshot: dict[str, Any] | None
    input_cost: Decimal | None
    output_cost: Decimal | None
    cache_write_cost: Decimal | None
    cache_read_cost: Decimal | None
    estimated_cost: Decimal | None
    request: dict[str, Any]
    response: dict[str, Any] | None


class ToolCallOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tool_name: str
    tool_call_id: str | None
    arguments: dict[str, Any]
    result: str | None
    success: bool
    skip_reason: str | None
    estimated_cost: Decimal | None


class RunStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sequence: int
    turn: int
    type: str
    status: str
    start_offset_ms: float
    latency_ms: float
    error: str | None
    llm_call: LLMCallOut | None
    tool_call: ToolCallOut | None


class LatencyBreakdown(BaseModel):
    """Where the run spent its time. `other_ms` is engine overhead between steps."""

    total_ms: float | None
    llm_ms: float
    tool_ms: float
    other_ms: float | None


class RunTraceOut(BaseModel):
    run: RunOut
    agent_version: AgentVersionOut
    latency: LatencyBreakdown
    steps: list[RunStepOut]


class ModelPricingIn(BaseModel):
    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=100)
    input_price_per_mtok: Decimal = Field(ge=0)
    output_price_per_mtok: Decimal = Field(ge=0)
    cache_write_price_per_mtok: Decimal | None = Field(default=None, ge=0)
    cache_read_price_per_mtok: Decimal | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    effective_date: date
    source: str | None = None


class ModelPricingOut(ModelPricingIn):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


class ToolPricingIn(BaseModel):
    tool_name: str = Field(min_length=1, max_length=100)
    price_per_call: Decimal = Field(ge=0)
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    effective_date: date
    source: str | None = None


class ToolPricingOut(ToolPricingIn):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime


class SummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    count: int
    mean: float | None
    median: float | None
    p95: float | None
    p99: float | None
    min: float | None
    max: float | None


class AgentMetricsOut(BaseModel):
    agent_id: str
    agent_version_id: str | None
    runs: int
    succeeded: int
    failed: int
    success_rate: float | None
    latency_ms: SummaryOut
    llm_latency_ms: SummaryOut
    tool_latency_ms: SummaryOut
    estimated_cost: SummaryOut
    estimated_total_spend: Decimal
    runs_without_cost: int
    input_tokens: SummaryOut
    output_tokens: SummaryOut
    llm_calls: SummaryOut
    tool_calls: SummaryOut
    tool_failure_rate: float | None


class DatasetCaseIn(BaseModel):
    id: str | None = Field(default=None, max_length=200)  # becomes case_key
    input: str = Field(min_length=1, max_length=200_000)
    expected_output: str | None = None
    labels: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    cases: list[DatasetCaseIn] = Field(min_length=1, max_length=10_000)


class DatasetCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_key: str
    input: str
    expected_output: str | None
    labels: dict[str, Any]
    metadata: dict[str, Any] = Field(validation_alias="metadata_")


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    version: int
    description: str | None
    created_at: datetime
    case_count: int


class DatasetDetailOut(DatasetOut):
    cases: list[DatasetCaseOut]


class EvaluatorIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: str
    config: dict[str, Any]
    description: str | None = None


class EvaluatorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    version: int
    type: str
    config: dict[str, Any]
    description: str | None
    created_at: datetime


class EvaluationIn(BaseModel):
    agent_id: str
    agent_version_id: str | None = None
    dataset_id: str
    evaluator_ids: list[str] = Field(min_length=1)
    repetitions: int = Field(default=1, ge=1, le=20)


class EvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    agent_version_id: str
    dataset_id: str
    evaluator_ids: list[str]
    repetitions: int
    experiment_id: str | None
    arm: str | None
    status: str
    error: str | None
    summary: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None


class EvaluationResultOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    dataset_case_id: str
    case_key: str
    evaluator_id: str
    evaluator_name: str
    status: str
    score: float | None
    passed: bool | None
    reason: str | None
    details: dict[str, Any]
    latency_ms: float
    judge_provider: str | None
    judge_model: str | None
    judge_prompt_version: str | None
    judge_prompt_sha256: str | None
    judge_input_tokens: int
    judge_output_tokens: int
    judge_estimated_cost: Decimal | None
    created_at: datetime


class ExperimentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    dataset_id: str
    baseline_agent_version_id: str
    candidate_agent_version_id: str
    evaluator_ids: list[str] = Field(min_length=1)
    repetitions: int = Field(default=1, ge=1, le=20)
    acceptance_criteria: dict[str, Any]
    # Controlled experiment: only these config fields may differ between the arms
    # (e.g. ["system_prompt"] for a pure prompt comparison). None = no restriction.
    isolate: list[str] | None = None


class ExperimentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    dataset_id: str
    baseline_agent_version_id: str
    candidate_agent_version_id: str
    evaluator_ids: list[str]
    repetitions: int
    acceptance_criteria: dict[str, Any]
    config_snapshot: dict[str, Any]
    reproduction_of: str | None
    status: str
    error: str | None
    baseline_evaluation_id: str | None
    candidate_evaluation_id: str | None
    verdict: str | None
    comparison: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None


class ExperimentSideOut(BaseModel):
    run_id: str
    status: str
    quality: float | None
    passed: bool | None
    estimated_cost: Decimal | None
    latency_ms: float | None
    tool_calls: int


class ExperimentCaseOut(BaseModel):
    case_key: str | None
    repetition: int
    baseline: ExperimentSideOut | None
    candidate: ExperimentSideOut | None
    quality_delta: float | None


class OptimizationAnalyzeIn(BaseModel):
    agent_id: str
    agent_version_id: str | None = None
    limit: int = Field(default=500, ge=1, le=5000)


class OptimizationReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    agent_version_id: str | None
    runs_analyzed: int
    recommendations: list[dict[str, Any]]
    created_at: datetime


class ApplyExperimentIn(BaseModel):
    name: str | None = None
    dataset_id: str
    evaluator_ids: list[str] = Field(min_length=1)
    repetitions: int = Field(default=1, ge=1, le=20)
    acceptance_criteria: dict[str, Any]


class ApplyOptimizationIn(BaseModel):
    type: str
    # Adjust the suggestion before applying, e.g. a different routing model.
    options_override: dict[str, Any] | None = None
    experiment: ApplyExperimentIn | None = None


class ApplyOptimizationOut(BaseModel):
    agent_version: AgentVersionOut
    experiment: ExperimentOut | None


class OverviewKpis(BaseModel):
    runs: int
    success_rate: float | None
    avg_cost: float | None
    runs_with_cost: int
    avg_latency_ms: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    avg_quality: float | None
    runs_with_quality: int


class TrendPoint(BaseModel):
    date: date
    runs: int
    success_rate: float | None
    avg_cost: float | None
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    avg_quality: float | None


class OverviewOut(BaseModel):
    days: int
    agent_id: str | None
    start: datetime
    end: datetime
    kpis: OverviewKpis
    previous: OverviewKpis
    trend: list[TrendPoint]
