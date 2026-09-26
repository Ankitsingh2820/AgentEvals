"""Load a small demo dataset so a fresh install has something to explore.

    python -m app.demo                       # locally
    docker compose exec api python -m app.demo   # in Docker

Everything uses the offline `mock` provider: free, no API keys, deterministic. Scores and
costs are placeholders that demonstrate the pipeline, not real model quality. Running it
again is a no-op once the demo agent exists.

It goes through the same functions as the HTTP API, so the demo data passes the same
validation as anything a user creates.
"""

import logging
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.api.agents import create_agent, create_version
from app.api.datasets import _create as create_dataset
from app.api.evaluators import create_evaluator
from app.api.experiments import create_experiment
from app.costs.seed import seed_pricing
from app.engine.executor import execute_run
from app.evaluation.runner import run_evaluation
from app.experiments.runner import run_experiment
from app.models import Agent, Evaluation, ModelPricing
from app.schemas import (
    AgentConfig,
    AgentCreate,
    DatasetCaseIn,
    DatasetIn,
    EvaluatorIn,
    ExperimentIn,
)

logger = logging.getLogger(__name__)

DEMO_AGENT = "Company Research Agent (demo)"
DATASET_FILE = Path(__file__).resolve().parent.parent / "datasets" / "company_research.jsonl"
PROMPT = "Research the company with your tools, then report what it does, its industry and size."


def load_demo(db: Session, session_factory: sessionmaker) -> dict:
    if db.scalar(select(Agent.id).where(Agent.name == DEMO_AGENT)):
        return {"loaded": False, "reason": "demo data already present"}

    # Prices first (idempotent): without them costs are "unpriced" and the experiment's
    # cost criterion can only come out INCONCLUSIVE. Docker loads them at startup, but a
    # local run might not have.
    seed_pricing(db)
    # A cheaper fictional model, so the experiment has a cost difference to measure.
    if not db.scalar(select(ModelPricing.id).where(ModelPricing.model == "mock-model-small")):
        db.add(
            ModelPricing(
                provider="mock",
                model="mock-model-small",
                input_price_per_mtok=Decimal("0.4"),
                output_price_per_mtok=Decimal("0.8"),
                effective_date=date(2026, 1, 1),
                source="Fictional demo price (mock provider)",
            )
        )
        db.commit()

    base = dict(provider="mock", system_prompt=PROMPT, tools=["company_lookup"])
    agent = create_agent(
        AgentCreate(
            name=DEMO_AGENT,
            model="mock-model",
            **base,
            description="Looks up a company and reports on it. Runs on the offline mock model.",
            tags=["demo"],
        ),
        db,
    )
    baseline = agent.latest_version
    candidate = create_version(agent.id, AgentConfig(model="mock-model-small", **base), db)
    long_prompt = create_agent(
        AgentCreate(
            name="Long-prompt Agent (demo)",
            provider="mock",
            model="mock-model",
            system_prompt="You research companies carefully. " * 400,
            tools=["company_lookup"],
            description="Has a long system prompt, so 'Analyze runs' recommends prompt caching.",
            tags=["demo"],
        ),
        db,
    )

    for company in (
        "Acme Corp",
        "Globex Analytics",
        "Initech Health",
        "Umbrella Logistics",
        "Nonexistent Inc",
    ):
        execute_run(db, baseline, company)
    for company in ("Acme Corp", "Globex Analytics", "Initech Health"):
        execute_run(db, long_prompt.latest_version, company)

    import json

    cases = [
        DatasetCaseIn.model_validate(json.loads(line))
        for line in DATASET_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    dataset = create_dataset(
        db,
        DatasetIn(
            name="company_research",
            cases=cases,
            description="5 companies, including one that does not exist.",
        ),
    )

    mentions = create_evaluator(
        EvaluatorIn(
            name="mentions-industry",
            type="contains",
            config={"labels_key": "must_mention"},
            description="The answer names the company's industry.",
        ),
        db,
    )
    uses_lookup = create_evaluator(
        EvaluatorIn(
            name="uses-lookup",
            type="tool_calls",
            config={"required": ["company_lookup"], "max_calls": 2},
            description="The agent used the lookup tool, at most twice.",
        ),
        db,
    )
    judge = create_evaluator(
        EvaluatorIn(
            name="relevance-judge (mock)",
            type="llm_judge",
            config={"criterion": "relevance", "provider": "mock", "model": "mock-model"},
            description="Mock judge: always scores 4/5. Use a real provider for real scores.",
        ),
        db,
    )

    evaluation = Evaluation(
        agent_id=agent.id,
        agent_version_id=baseline.id,
        dataset_id=dataset.id,
        evaluator_ids=[mentions.id, uses_lookup.id, judge.id],
    )
    db.add(evaluation)
    db.commit()
    run_evaluation(session_factory, evaluation.id)

    experiment = create_experiment(
        ExperimentIn(
            name="Cheaper model for the research agent",
            description="Baseline mock-model vs the cheaper mock-model-small.",
            dataset_id=dataset.id,
            baseline_agent_version_id=baseline.id,
            candidate_agent_version_id=candidate.id,
            evaluator_ids=[mentions.id, uses_lookup.id],
            repetitions=2,
            isolate=["model"],
            acceptance_criteria={
                "max_quality_drop_points": 2,
                "min_cost_reduction_pct": 20,
                "max_error_rate": 0.05,
            },
        ),
        db,
    )
    run_experiment(session_factory, experiment.id)
    db.refresh(experiment)
    return {
        "loaded": True,
        "agents": 2,
        "evaluation_id": evaluation.id,
        "experiment_id": experiment.id,
        "experiment_verdict": experiment.verdict,
    }


if __name__ == "__main__":
    from app.config import get_settings
    from app.db import SessionLocal
    from app.logging_config import configure_logging

    configure_logging(get_settings().log_level)
    with SessionLocal() as session:
        result = load_demo(session, SessionLocal)
    if result["loaded"]:
        print(
            "Demo data loaded: 2 agents, 8 runs, a dataset, 3 evaluators, 1 evaluation and "
            f"1 experiment (verdict: {result['experiment_verdict']}). Open the dashboard."
        )
    else:
        print(f"Nothing to do: {result['reason']}.")
