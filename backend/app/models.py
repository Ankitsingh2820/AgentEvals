"""ORM models for Phase 1: agents, immutable agent versions, and run traces.

Trace hierarchy:  Agent -> AgentVersion -> Run -> RunStep -> (LLMCall | ToolCall)

Agent configuration is never edited in place. Every change creates a new AgentVersion,
and every Run pins the exact version it executed, so a run can always be reproduced
from the configuration it actually used.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


# Money is stored as exact decimals, never floats. 10 decimal places keeps a single cached
# token (fractions of a micro-dollar) representable without rounding.
Money = Numeric(20, 10)
Price = Numeric(14, 6)  # USD (or `currency`) per unit


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    versions: Mapped[list["AgentVersion"]] = relationship(
        back_populates="agent", order_by="AgentVersion.version", cascade="all, delete-orphan"
    )

    @property
    def latest_version(self) -> "AgentVersion":
        return self.versions[-1]


class AgentVersion(Base):
    """An immutable snapshot of an agent's configuration."""

    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    temperature: Mapped[float | None] = mapped_column(Float)
    max_tokens: Mapped[int] = mapped_column(Integer)
    tools: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Reserved for later phases; stored now so versions stay complete snapshots.
    rag_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    eval_config: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # Optimization switches (routing, parallel tools, dedupe, caching); see engine/options.
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    agent: Mapped[Agent] = relationship(back_populates="versions")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"), index=True)
    experiment_id: Mapped[str | None] = mapped_column(ForeignKey("experiments.id"), index=True)
    # Set when the run was produced by an evaluation over a dataset case.
    evaluation_id: Mapped[str | None] = mapped_column(ForeignKey("evaluations.id"), index=True)
    dataset_case_id: Mapped[str | None] = mapped_column(ForeignKey("dataset_cases.id"))
    # 0-based repeat index when a case is run several times to measure run-to-run variance.
    repetition: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|succeeded|failed
    input: Mapped[str] = mapped_column(Text)
    final_output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_latency_ms: Mapped[float | None] = mapped_column(Float)
    llm_call_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    tool_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    # Tool calls the model requested but that were not executed (deduplicated or over the
    # tool budget). tool_call_count counts executions only.
    tool_calls_skipped: Mapped[int] = mapped_column(Integer, default=0)
    # Transient provider failures that were retried (each attempt is its own trace step).
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    # Model routing outcome: which route fired and the model the agent loop then used.
    route: Mapped[str | None] = mapped_column(String(50))
    routed_model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # Sums of step latencies; stored (not derived) so metrics can aggregate in SQL.
    llm_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    tool_latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    # Costs are *estimates* computed from the pricing registry at run time. They are not
    # provider-billed amounts; reconciling against invoices would be a separate field.
    estimated_llm_cost: Mapped[Decimal | None] = mapped_column(Money)
    estimated_tool_cost: Mapped[Decimal | None] = mapped_column(Money)
    estimated_total_cost: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str | None] = mapped_column(String(3))
    # complete: every LLM call was priced | partial: some were not | unpriced: none were
    cost_status: Mapped[str | None] = mapped_column(String(20))

    agent_version: Mapped[AgentVersion] = relationship()
    steps: Mapped[list["RunStep"]] = relationship(
        back_populates="run", order_by="RunStep.sequence", cascade="all, delete-orphan"
    )


class RunStep(Base):
    """One timed unit of work inside a run. Details live in the typed child table."""

    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(20))  # llm_call | tool_call
    status: Mapped[str] = mapped_column(String(20))  # succeeded | failed
    # Agent-loop turn (1-based): an LLM call and the tool calls it requested share a turn.
    # 0 = before the loop (e.g. routing). Needed to spot tool calls that could run in parallel.
    turn: Mapped[int] = mapped_column(Integer, default=0)
    # Offset from run start, so the trace renders as a timeline.
    start_offset_ms: Mapped[float] = mapped_column(Float)
    latency_ms: Mapped[float] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)

    run: Mapped[Run] = relationship(back_populates="steps")
    llm_call: Mapped["LLMCall | None"] = relationship(
        back_populates="step", uselist=False, cascade="all, delete-orphan"
    )
    tool_call: Mapped["ToolCall | None"] = relationship(
        back_populates="step", uselist=False, cascade="all, delete-orphan"
    )


class LLMCall(Base):
    __tablename__ = "llm_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    step_id: Mapped[str] = mapped_column(
        ForeignKey("run_steps.id", ondelete="CASCADE"), unique=True
    )
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))  # model requested
    purpose: Mapped[str] = mapped_column(String(20), default="agent")  # agent | routing
    response_model: Mapped[str | None] = mapped_column(String(100))  # model the provider reports
    request: Mapped[dict[str, Any]] = mapped_column(JSON)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    stop_reason: Mapped[str | None] = mapped_column(String(50))
    # input_tokens excludes cached tokens (Anthropic convention); total_tokens includes them.
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    provider_request_id: Mapped[str | None] = mapped_column(String(100))

    # Estimated cost. `pricing_snapshot` copies the exact prices used, so the figure stays
    # explainable even if the pricing registry changes later. All null when unpriced.
    pricing_id: Mapped[str | None] = mapped_column(ForeignKey("model_pricing.id"))
    pricing_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    input_cost: Mapped[Decimal | None] = mapped_column(Money)
    output_cost: Mapped[Decimal | None] = mapped_column(Money)
    cache_write_cost: Mapped[Decimal | None] = mapped_column(Money)
    cache_read_cost: Mapped[Decimal | None] = mapped_column(Money)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Money)

    step: Mapped[RunStep] = relationship(back_populates="llm_call")


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    step_id: Mapped[str] = mapped_column(
        ForeignKey("run_steps.id", ondelete="CASCADE"), unique=True
    )
    tool_name: Mapped[str] = mapped_column(String(100))
    tool_call_id: Mapped[str | None] = mapped_column(String(100))  # provider's tool_use id
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[str | None] = mapped_column(Text)
    success: Mapped[bool] = mapped_column(Boolean)
    # Set when the call was not executed: "duplicate" (result reused from an identical
    # earlier call, dedupe option) or "budget" (refused, run's tool budget exhausted).
    skip_reason: Mapped[str | None] = mapped_column(String(20))
    pricing_id: Mapped[str | None] = mapped_column(ForeignKey("tool_pricing.id"))
    estimated_cost: Mapped[Decimal | None] = mapped_column(Money)

    step: Mapped[RunStep] = relationship(back_populates="tool_call")


class ModelPricing(Base):
    """One price list entry for a model, valid from `effective_date` until superseded.

    Entries are append-only: a price change is a new row with a later effective date, so
    the price that applied to any past run can always be looked up.
    """

    __tablename__ = "model_pricing"
    __table_args__ = (UniqueConstraint("provider", "model", "effective_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(100))
    input_price_per_mtok: Mapped[Decimal] = mapped_column(Price)
    output_price_per_mtok: Mapped[Decimal] = mapped_column(Price)
    cache_write_price_per_mtok: Mapped[Decimal | None] = mapped_column(Price)
    cache_read_price_per_mtok: Mapped[Decimal | None] = mapped_column(Price)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    effective_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ToolPricing(Base):
    """Per-call price for a tool that costs money (e.g. a paid search API). Append-only."""

    __tablename__ = "tool_pricing"
    __table_args__ = (UniqueConstraint("tool_name", "effective_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tool_name: Mapped[str] = mapped_column(String(100))
    price_per_call: Mapped[Decimal] = mapped_column(Price)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    effective_date: Mapped[date] = mapped_column(Date)
    source: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Dataset(Base):
    """A named, versioned, immutable set of test cases. Editing a dataset means creating a
    new version, so an evaluation always refers to exactly the cases it ran."""

    __tablename__ = "datasets"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    cases: Mapped[list["DatasetCase"]] = relationship(
        back_populates="dataset", order_by="DatasetCase.position", cascade="all, delete-orphan"
    )


class DatasetCase(Base):
    __tablename__ = "dataset_cases"
    __table_args__ = (UniqueConstraint("dataset_id", "case_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("datasets.id", ondelete="CASCADE"), index=True
    )
    case_key: Mapped[str] = mapped_column(String(200))  # user-facing id, e.g. "case_001"
    position: Mapped[int] = mapped_column(Integer)
    input: Mapped[str] = mapped_column(Text)
    expected_output: Mapped[str | None] = mapped_column(Text)
    # Free-form per-case data evaluators can read (e.g. required keywords, category).
    labels: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)

    dataset: Mapped[Dataset] = relationship(back_populates="cases")


class Evaluator(Base):
    """A versioned, immutable scoring rule. `config` is validated per `type`.
    For LLM judges it pins the judge model, criterion and prompt version."""

    __tablename__ = "evaluators"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    version: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(50))
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Evaluation(Base):
    """One agent version run over every case of one dataset, scored by a fixed list of
    evaluator versions."""

    __tablename__ = "evaluations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"))
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    evaluator_ids: Mapped[list[str]] = mapped_column(JSON)
    repetitions: Mapped[int] = mapped_column(Integer, default=1)
    experiment_id: Mapped[str | None] = mapped_column(ForeignKey("experiments.id"), index=True)
    arm: Mapped[str | None] = mapped_column(String(20))  # baseline | candidate (experiments)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending | running | completed | failed
    # Worker lease: which task execution owns this object, until when (see app/leases.py).
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    results: Mapped[list["EvaluationResult"]] = relationship(
        back_populates="evaluation", cascade="all, delete-orphan"
    )


class EvaluationResult(Base):
    """One evaluator's verdict on one run. `status` separates real scores from evaluator
    failures (error) and inapplicable checks (skipped); only `ok` rows carry a score."""

    __tablename__ = "evaluation_results"
    __table_args__ = (UniqueConstraint("run_id", "evaluator_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("evaluations.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    dataset_case_id: Mapped[str] = mapped_column(ForeignKey("dataset_cases.id"))
    evaluator_id: Mapped[str] = mapped_column(ForeignKey("evaluators.id"))
    status: Mapped[str] = mapped_column(String(20))  # ok | skipped | error
    score: Mapped[float | None] = mapped_column(Float)  # 0..1
    passed: Mapped[bool | None] = mapped_column(Boolean)
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    # Judge audit trail (null for deterministic evaluators).
    judge_provider: Mapped[str | None] = mapped_column(String(50))
    judge_model: Mapped[str | None] = mapped_column(String(100))
    judge_prompt_version: Mapped[str | None] = mapped_column(String(50))
    judge_prompt_sha256: Mapped[str | None] = mapped_column(String(64))
    judge_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    judge_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    judge_estimated_cost: Mapped[Decimal | None] = mapped_column(Money)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    evaluation: Mapped[Evaluation] = relationship(back_populates="results")


class Experiment(Base):
    """Baseline vs candidate agent version on the same dataset, the same evaluator
    versions and the same repetitions, judged against acceptance criteria.

    Each arm is an Evaluation (arm = baseline | candidate). Runs from both arms are
    interleaved case by case, so time-varying provider latency affects both equally.
    """

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    dataset_id: Mapped[str] = mapped_column(ForeignKey("datasets.id"))
    baseline_agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"))
    candidate_agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"))
    evaluator_ids: Mapped[list[str]] = mapped_column(JSON)
    repetitions: Mapped[int] = mapped_column(Integer, default=1)
    acceptance_criteria: Mapped[dict[str, Any]] = mapped_column(JSON)
    # Everything that defines the experiment, frozen at creation, for reproducibility.
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    reproduction_of: Mapped[str | None] = mapped_column(ForeignKey("experiments.id"))
    status: Mapped[str] = mapped_column(String(20), default="created")
    # created | running | completed | failed
    # Worker lease: which task execution owns this object, until when (see app/leases.py).
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    baseline_evaluation_id: Mapped[str | None] = mapped_column(String(36))
    candidate_evaluation_id: Mapped[str | None] = mapped_column(String(36))
    comparison: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    verdict: Mapped[str | None] = mapped_column(String(20))  # PASS | FAIL | INCONCLUSIVE
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OptimizationReport(Base):
    """Evidence-based optimization recommendations for an agent, computed from its
    recorded runs. Stored so a recommendation can be traced to the data behind it."""

    __tablename__ = "optimization_recommendations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    agent_version_id: Mapped[str | None] = mapped_column(ForeignKey("agent_versions.id"))
    runs_analyzed: Mapped[int] = mapped_column(Integer)
    recommendations: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApiKey(Base):
    """An API credential. Only the SHA-256 hash of the key is stored; the plaintext is
    shown once at creation. `key_prefix` (first characters) identifies a key in listings."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200))
    key_prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
