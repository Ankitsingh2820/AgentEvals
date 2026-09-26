"""Bridge between the Streamlit demo and the AgentEval engine.

The demo runs the real engine (agent loop, evaluators, LLM judge, experiments, costs)
in-process, without the API server, PostgreSQL, Redis or the worker. Isolation is the
point of this module:

- Every visitor gets a private in-memory database, pre-loaded with the demo data. Nobody
  sees anyone else's agents or runs, and the data disappears with the session.
- A visitor's API key lives only in their session object and is applied through a
  context-scoped provider override (app.providers.use_providers). It is never put in the
  environment, the global registry, logs or the database.
- The host's own provider keys are removed before the engine loads, so the demo can
  never spend the host's money.
"""

import logging
import os
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Configure the engine before importing it.
os.environ["AGENTEVAL_DATABASE_URL"] = "sqlite://"  # the global engine is never used
os.environ["AGENTEVAL_TASK_BACKEND"] = "inline"
for _var in ("OPENAI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
    os.environ.pop(_var, None)

import anthropic  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from sqlalchemy import create_engine, select  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import models  # noqa: E402
from app.api.agents import create_agent as _create_agent  # noqa: E402
from app.api.agents import create_version as _create_version  # noqa: E402
from app.api.evaluators import create_evaluator as _create_evaluator  # noqa: E402
from app.api.experiments import create_experiment as _create_experiment  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.demo import load_demo  # noqa: E402
from app.engine.executor import execute_run  # noqa: E402
from app.evaluation.runner import run_evaluation as _run_evaluation  # noqa: E402
from app.experiments.runner import run_experiment as _run_experiment  # noqa: E402
from app.providers import use_providers  # noqa: E402
from app.providers.anthropic_provider import AnthropicProvider  # noqa: E402
from app.providers.base import LLMProvider, ProviderError  # noqa: E402
from app.providers.openai_compatible import OpenAICompatibleProvider  # noqa: E402
from app.schemas import AgentConfig, AgentCreate, EvaluatorIn, ExperimentIn  # noqa: E402

# The settings loader also reads a .env file from the working directory: make sure keys
# found there can never be used by the demo either.
_settings = get_settings()
_settings.openai_api_key = None
_settings.groq_api_key = None
logging.getLogger().setLevel(logging.WARNING)

REAL_PROVIDERS = {"groq": "Groq", "openai": "OpenAI", "anthropic": "Anthropic (Claude)"}
MOCK_MODELS = ["mock-model", "mock-model-small"]
TOOLS = ["company_lookup", "calculator"]
MAX_REPETITIONS = 2  # keeps real-model runs on a visitor's key small
AGENT_MAX_TOKENS = 1500


class DemoError(Exception):
    """A user-facing error (validation, provider problem)."""


@dataclass
class Workspace:
    """One visitor's private, temporary AgentEval."""

    factory: sessionmaker
    db: Session
    providers: dict[str, LLMProvider] = field(default_factory=dict)
    models: dict[str, list[str]] = field(default_factory=dict)  # models the key can use

    @contextmanager
    def engine(self):
        """Run engine code with this visitor's providers (and only theirs)."""
        with use_providers(self.providers):
            yield


def new_workspace() -> Workspace:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    db = factory()
    ws = Workspace(factory=factory, db=db)
    with ws.engine():
        load_demo(db, factory)
    return ws


# --- Real providers (visitor's own key) --------------------------------------------------


def connect(ws: Workspace, provider: str, key: str) -> list[str]:
    """Validate a key by listing models (free), then keep it in this workspace only."""
    key = key.strip()
    if not key:
        raise DemoError("Paste an API key first.")
    try:
        if provider in ("openai", "groq"):
            p = OpenAICompatibleProvider(
                provider,
                key,
                f"{provider.upper()}_API_KEY",
                base_url=_settings.groq_base_url if provider == "groq" else None,
                timeout=60,
            )
            available = p.list_models()
        elif provider == "anthropic":
            client = anthropic.Anthropic(api_key=key, max_retries=0, timeout=60)
            try:
                available = sorted(m.id for m in client.models.list())
            except anthropic.AuthenticationError as e:
                raise ProviderError("anthropic 401: invalid API key") from e
            except anthropic.APIError as e:
                raise ProviderError(f"anthropic: {e}") from e
            p = AnthropicProvider(client=client)
        else:
            raise DemoError(f"Unknown provider {provider}.")
    except ProviderError as e:
        raise DemoError(f"That key didn't work: {e}") from e
    ws.providers[provider] = p
    ws.models[provider] = available
    return available


def disconnect(ws: Workspace, provider: str) -> None:
    ws.providers.pop(provider, None)
    ws.models.pop(provider, None)


def connected(ws: Workspace) -> list[str]:
    return [p for p in REAL_PROVIDERS if p in ws.providers]


def model_choices(ws: Workspace, provider: str) -> list[str]:
    """Models to offer: priced ones the key can use (so costs are real), else all."""
    if provider == "mock":
        return MOCK_MODELS
    priced = set(
        ws.db.scalars(
            select(models.ModelPricing.model).where(models.ModelPricing.provider == provider)
        )
    )
    available = ws.models.get(provider, [])
    both = sorted(m for m in available if m in priced)
    return both or available


# --- Reads -------------------------------------------------------------------------------


def agents(ws: Workspace) -> list[models.Agent]:
    return list(ws.db.scalars(select(models.Agent).order_by(models.Agent.created_at)))


def versions(ws: Workspace, agent_id: str) -> list[models.AgentVersion]:
    agent = ws.db.get(models.Agent, agent_id)
    return list(agent.versions) if agent else []


def dataset(ws: Workspace) -> models.Dataset:
    return ws.db.scalars(select(models.Dataset)).first()


def evaluators(ws: Workspace) -> list[models.Evaluator]:
    return list(ws.db.scalars(select(models.Evaluator).order_by(models.Evaluator.created_at)))


def runs(ws: Workspace) -> list[models.Run]:
    return list(ws.db.scalars(select(models.Run).order_by(models.Run.started_at.desc())))


def experiments(ws: Workspace) -> list[models.Experiment]:
    return list(
        ws.db.scalars(select(models.Experiment).order_by(models.Experiment.created_at.desc()))
    )


def results(ws: Workspace, evaluation_id: str) -> list[dict]:
    rows = ws.db.execute(
        select(models.EvaluationResult, models.DatasetCase.case_key, models.Evaluator.name)
        .join(models.DatasetCase, models.DatasetCase.id == models.EvaluationResult.dataset_case_id)
        .join(models.Evaluator, models.Evaluator.id == models.EvaluationResult.evaluator_id)
        .where(models.EvaluationResult.evaluation_id == evaluation_id)
        .order_by(models.DatasetCase.position, models.Evaluator.name)
    )
    return [
        {
            "case": key,
            "evaluator": name,
            "verdict": ("PASS" if r.passed else "FAIL") if r.status == "ok" else r.status,
            "score": r.score,
            "reason": r.reason,
        }
        for r, key, name in rows
    ]


def step_rows(run: models.Run) -> list[dict]:
    out = []
    for s in run.steps:
        if s.llm_call:
            c = s.llm_call
            what = f"{'route' if c.purpose == 'routing' else 'LLM'} · {c.model}"
            tokens = c.total_tokens
            cost = c.estimated_cost
        else:
            c = s.tool_call
            what = f"tool · {c.tool_name}" + (f" ({c.skip_reason})" if c.skip_reason else "")
            tokens, cost = None, c.estimated_cost
        out.append(
            {
                "#": s.sequence,
                "turn": s.turn,
                "step": what,
                "status": s.status,
                "starts at (ms)": round(s.start_offset_ms, 1),
                "latency (ms)": round(s.latency_ms, 1),
                "tokens": tokens,
                "cost ($)": None if cost is None else float(cost),
                "error": s.error,
            }
        )
    return out


# --- Actions -----------------------------------------------------------------------------


def _check_provider(ws: Workspace, provider: str) -> None:
    if provider != "mock" and provider not in ws.providers:
        raise DemoError(
            f"Connect your {REAL_PROVIDERS.get(provider, provider)} key first (sidebar)."
        )


def _unwrap(e: HTTPException) -> DemoError:
    return DemoError(e.detail if isinstance(e.detail, str) else str(e.detail))


def create_agent(
    ws: Workspace, name: str, provider: str, model: str, prompt: str, tools: list[str]
) -> models.AgentVersion:
    _check_provider(ws, provider)
    try:
        agent = _create_agent(
            AgentCreate(
                name=name.strip() or f"My agent {uuid.uuid4().hex[:4]}",
                provider=provider,
                model=model,
                system_prompt=prompt,
                tools=tools,
                max_tokens=AGENT_MAX_TOKENS,
            ),
            ws.db,
        )
    except HTTPException as e:
        raise _unwrap(e) from e
    return agent.latest_version


def new_version(ws: Workspace, agent_id: str, provider: str, model: str) -> models.AgentVersion:
    """A copy of the agent's latest version with a different provider/model."""
    _check_provider(ws, provider)
    latest = ws.db.get(models.Agent, agent_id).latest_version
    try:
        return _create_version(
            agent_id,
            AgentConfig(
                provider=provider,
                model=model,
                system_prompt=latest.system_prompt,
                tools=latest.tools,
                max_tokens=AGENT_MAX_TOKENS,
            ),
            ws.db,
        )
    except HTTPException as e:
        raise _unwrap(e) from e


def run_agent(ws: Workspace, version_id: str, text: str) -> models.Run:
    version = ws.db.get(models.AgentVersion, version_id)
    _check_provider(ws, version.provider)
    if not text.strip():
        raise DemoError("Type an input for the agent.")
    with ws.engine():
        return execute_run(ws.db, version, text.strip())


def add_judge(ws: Workspace, provider: str, model: str, criterion: str) -> models.Evaluator:
    _check_provider(ws, provider)
    name = f"{criterion}-judge ({provider}/{model})"
    existing = ws.db.scalars(select(models.Evaluator).where(models.Evaluator.name == name))
    if found := existing.first():
        return found
    try:
        return _create_evaluator(
            EvaluatorIn(
                name=name,
                type="llm_judge",
                config={"criterion": criterion, "provider": provider, "model": model},
            ),
            ws.db,
        )
    except HTTPException as e:
        raise _unwrap(e) from e


def _check_evaluators(ws: Workspace, evaluator_ids: list[str]) -> None:
    if not evaluator_ids:
        raise DemoError("Pick at least one evaluator.")
    for ev in evaluators(ws):
        if ev.id in evaluator_ids and ev.type == "llm_judge":
            _check_provider(ws, ev.config["provider"])


def run_evaluation(
    ws: Workspace, version_id: str, evaluator_ids: list[str], repetitions: int
) -> models.Evaluation:
    version = ws.db.get(models.AgentVersion, version_id)
    _check_provider(ws, version.provider)
    _check_evaluators(ws, evaluator_ids)
    evaluation = models.Evaluation(
        agent_id=version.agent_id,
        agent_version_id=version.id,
        dataset_id=dataset(ws).id,
        evaluator_ids=evaluator_ids,
        repetitions=min(max(repetitions, 1), MAX_REPETITIONS),
    )
    ws.db.add(evaluation)
    ws.db.commit()
    with ws.engine():
        _run_evaluation(ws.factory, evaluation.id)
    ws.db.expire_all()
    return ws.db.get(models.Evaluation, evaluation.id)


def run_experiment(
    ws: Workspace,
    baseline_id: str,
    candidate_id: str,
    evaluator_ids: list[str],
    repetitions: int,
    criteria: dict,
) -> models.Experiment:
    for vid in (baseline_id, candidate_id):
        _check_provider(ws, ws.db.get(models.AgentVersion, vid).provider)
    _check_evaluators(ws, evaluator_ids)
    try:
        exp = _create_experiment(
            ExperimentIn(
                name=f"Experiment {uuid.uuid4().hex[:4]}",
                dataset_id=dataset(ws).id,
                baseline_agent_version_id=baseline_id,
                candidate_agent_version_id=candidate_id,
                evaluator_ids=evaluator_ids,
                repetitions=min(max(repetitions, 1), MAX_REPETITIONS),
                acceptance_criteria=criteria,
            ),
            ws.db,
        )
    except HTTPException as e:
        raise _unwrap(e) from e
    with ws.engine():
        _run_experiment(ws.factory, exp.id)
    ws.db.expire_all()
    return ws.db.get(models.Experiment, exp.id)
