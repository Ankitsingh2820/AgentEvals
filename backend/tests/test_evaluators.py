"""Unit tests for deterministic evaluators, evaluator configs and the LLM judge."""

import json

import pytest
from pydantic import ValidationError

from app.evaluation import deterministic as d
from app.evaluation import judge
from app.evaluation.base import EvalInput, ToolCallView
from app.evaluation.configs import (
    ContainsConfig,
    ExactMatchConfig,
    JsonSchemaConfig,
    LLMJudgeConfig,
    RegexConfig,
    ToolCallsConfig,
    parse_config,
)
from app.providers.base import LLMResponse, ProviderError


def x(output="", expected=None, labels=None, tools=(), status="succeeded", task="Do X"):
    return EvalInput(task, expected, labels or {}, output, status, None, list(tools))


LOOKUP = ToolCallView("company_lookup", {"name": "Acme"}, '{"industry": "Automation"}', True)
FAILED = ToolCallView("company_lookup", {"name": "Nope"}, None, False)


# --- deterministic --------------------------------------------------------------------


def test_exact_match_normalizes_and_skips_without_reference():
    cfg = ExactMatchConfig()
    assert d.exact_match(cfg, x("  Paris\n", expected="paris")).passed
    assert d.exact_match(ExactMatchConfig(normalize=False), x("Paris", "paris")).score == 0
    assert d.exact_match(cfg, x("Paris")).status == "skipped"


def test_contains_partial_score_and_labels():
    cfg = ContainsConfig(values=["alpha"], labels_key="must")
    v = d.contains(cfg, x("Alpha only", labels={"must": ["beta"]}))
    assert v.score == 0.5 and v.passed is False
    assert v.details["missing"] == ["beta"]
    assert d.contains(ContainsConfig(values=["a", "zzz"], mode="any"), x("a")).passed
    assert d.contains(ContainsConfig(labels_key="must"), x("a")).status == "skipped"
    assert d.contains(ContainsConfig(values=["A"], case_sensitive=True), x("a")).score == 0


def test_regex():
    assert d.regex(RegexConfig(pattern=r"\[\d+\]"), x("see [1]")).passed  # citation present
    assert d.regex(RegexConfig(pattern="sorry", must_match=False), x("Sorry!")).passed is True
    assert (
        d.regex(
            RegexConfig(pattern="sorry", must_match=False, ignore_case=True), x("Sorry!")
        ).passed
        is False
    )


SCHEMA = {
    "type": "object",
    "required": ["industry", "competitors"],
    "properties": {"competitors": {"type": "array", "minItems": 2}},
}


def test_json_schema_valid_fenced_invalid_and_violations():
    cfg = JsonSchemaConfig(json_schema=SCHEMA)
    good = {"industry": "x", "competitors": ["a", "b"]}
    assert d.json_schema(cfg, x(json.dumps(good))).passed
    assert d.json_schema(cfg, x(f"Here:\n```json\n{json.dumps(good)}\n```")).passed
    assert "not valid JSON" in d.json_schema(cfg, x("no json here")).reason

    v = d.json_schema(cfg, x(json.dumps({"competitors": ["a"]})))
    assert v.score == 0 and len(v.details["errors"]) == 2  # missing field + too few items


def test_tool_calls_checks():
    cfg = ToolCallsConfig(required=["company_lookup"], forbidden=["web_search"], max_calls=1)
    assert d.tool_calls(cfg, x(tools=[LOOKUP])).score == 1
    v = d.tool_calls(cfg, x(tools=[LOOKUP, LOOKUP]))
    assert v.score == pytest.approx(2 / 3) and not v.passed
    assert d.tool_calls(ToolCallsConfig(require_success=True), x(tools=[FAILED])).score == 0


@pytest.mark.parametrize(
    "type_,config",
    [
        ("regex", {"pattern": "("}),
        ("json_schema", {"json_schema": {"type": "not-a-type"}}),
        ("llm_judge", {"criterion": "custom", "provider": "mock", "model": "m"}),
        ("contains", {}),
        ("tool_calls", {}),
        ("exact_match", {"unknown_field": 1}),
    ],
)
def test_invalid_configs_rejected(type_, config):
    with pytest.raises(ValidationError):
        parse_config(type_, config)


def test_unknown_evaluator_type():
    with pytest.raises(ValueError, match="unknown evaluator type"):
        parse_config("vibes", {})


# --- LLM judge ------------------------------------------------------------------------


class FakeJudge:
    name = "fake"

    def __init__(self, text=None, exc=None):
        self.text, self.exc, self.requests = text, exc, []

    def complete(self, request):
        self.requests.append(request)
        if self.exc:
            raise self.exc
        return LLMResponse(
            text=self.text,
            tool_calls=[],
            stop_reason="end_turn",
            input_tokens=200,
            output_tokens=40,
            response_model=request.model,
            raw_request={},
            raw_response={},
        )


def cfg(criterion="correctness", **kw):
    return LLMJudgeConfig(criterion=criterion, provider="fake", model="judge-model", **kw)


def reply(score, reasoning="because"):
    return json.dumps({"reasoning": reasoning, "score": score})


def test_judge_maps_1_to_5_scale_and_records_audit():
    provider = FakeJudge(reply(5))
    v = judge.judge(cfg(), x("Paris", expected="Paris is the capital"), provider)
    assert (v.status, v.score, v.passed) == ("ok", 1.0, True)
    assert v.judge.prompt_version == judge.PROMPT_VERSION
    assert v.judge.prompt_sha256 == judge.prompt_sha256(cfg())
    assert (v.judge.input_tokens, v.judge.output_tokens) == (200, 40)

    req = provider.requests[0]
    assert req.json_schema == judge.OUTPUT_SCHEMA
    assert req.temperature == 0.0
    assert "<reference_answer>" in req.messages[0].text

    low = judge.judge(cfg(), x("Rome", expected="Paris"), FakeJudge(reply(2)))
    assert low.score == 0.25 and low.passed is False


@pytest.mark.parametrize("text", ["not json", '{"score": 9, "reasoning": "x"}', '{"score": 3}'])
def test_unparseable_judge_output_is_error_not_zero(text):
    v = judge.judge(cfg("relevance"), x("out"), FakeJudge(text))
    assert v.status == "error" and v.score is None
    assert v.judge.raw_response == text


def test_judge_provider_failure_is_error():
    v = judge.judge(cfg("relevance"), x("out"), FakeJudge(exc=ProviderError("529")))
    assert v.status == "error" and "529" in v.reason


def test_judge_skips_when_criterion_cannot_apply():
    provider = FakeJudge(reply(5))
    assert judge.judge(cfg("correctness"), x("out"), provider).status == "skipped"
    assert judge.judge(cfg("groundedness"), x("out"), provider).status == "skipped"
    assert provider.requests == []  # no paid call made for inapplicable checks


def test_groundedness_prompt_includes_tool_results_as_context():
    provider = FakeJudge(reply(4))
    judge.judge(cfg("groundedness"), x("It is automation", tools=[LOOKUP, FAILED]), provider)
    prompt = provider.requests[0].messages[0].text
    assert "<context>" in prompt and '"industry": "Automation"' in prompt
    assert "Nope" not in prompt  # failed tool calls are not evidence


def test_failed_run_scores_zero_without_calling_judge():
    provider = FakeJudge(reply(5))
    v = judge.judge(cfg("relevance"), x(None, status="failed"), provider)
    assert (v.status, v.score, v.passed) == ("ok", 0.0, False)
    assert provider.requests == []


def test_prompt_hash_changes_with_rubric_and_criterion():
    base = judge.prompt_sha256(cfg("custom", rubric="Be concise"))
    assert base == judge.prompt_sha256(cfg("custom", rubric="Be concise"))
    assert base != judge.prompt_sha256(cfg("custom", rubric="Be thorough"))
    assert judge.prompt_sha256(cfg("relevance")) != judge.prompt_sha256(cfg("completeness"))
