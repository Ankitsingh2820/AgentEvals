"""Dispatch an evaluator version to its implementation. Evaluator failures are converted
into `error` verdicts here so one broken evaluator never aborts an evaluation."""

import logging

from app.evaluation import deterministic, judge
from app.evaluation.base import EvalInput, Verdict
from app.evaluation.configs import LLMJudgeConfig, parse_config
from app.models import Evaluator
from app.providers import get_provider

logger = logging.getLogger(__name__)

_DETERMINISTIC = {
    "exact_match": deterministic.exact_match,
    "contains": deterministic.contains,
    "regex": deterministic.regex,
    "json_schema": deterministic.json_schema,
    "tool_calls": deterministic.tool_calls,
}


def evaluate(evaluator: Evaluator, x: EvalInput) -> Verdict:
    try:
        cfg = parse_config(evaluator.type, evaluator.config)
        if isinstance(cfg, LLMJudgeConfig):
            return judge.judge(cfg, x, get_provider(cfg.provider))
        return _DETERMINISTIC[evaluator.type](cfg, x)
    except Exception as e:
        logger.exception("evaluator crashed", extra={"evaluator_id": evaluator.id})
        return Verdict.error(f"evaluator error: {type(e).__name__}: {e}")
