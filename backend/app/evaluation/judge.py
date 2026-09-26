"""LLM-as-a-judge.

Design choices that reduce judge noise and bias:
- A 1-5 integer scale with an anchored description per level, rather than asking for a
  free-form 0-1 float (models are poorly calibrated on continuous scales).
- The judge writes its reasoning before the score (it is asked for in that order).
- One criterion per judge call; no mixing "is it correct AND well written".
- The judge never sees which agent/configuration produced the output.
- Structured JSON output is requested; anything else is an evaluator error, never a 0.
- Every result records judge model, prompt version and a hash of the exact template, so
  scores from different prompt versions are never silently compared.
"""

import hashlib
import json
from dataclasses import dataclass

from app.evaluation.base import EvalInput, JudgeAudit, Verdict
from app.evaluation.configs import LLMJudgeConfig
from app.providers.base import LLMProvider, LLMRequest, Message, ProviderError

PROMPT_VERSION = "judge-v1"

SYSTEM_PROMPT = (
    "You are an impartial evaluator of AI assistant responses. You judge exactly one "
    "criterion, using only the material provided. Do not reward length, confidence or "
    "style unless the criterion asks for it. Think through the evidence first, then give "
    "an integer score from 1 to 5 using the scale provided."
)


@dataclass(frozen=True)
class CriterionSpec:
    question: str
    scale: str
    needs_reference: bool = False
    needs_context: bool = False


CRITERIA: dict[str, CriterionSpec] = {
    "correctness": CriterionSpec(
        "Is the response factually consistent with the reference answer?",
        "5 = fully consistent with the reference; no errors\n"
        "4 = consistent; only trivial omissions or imprecision\n"
        "3 = partly consistent; at least one material error or omission\n"
        "2 = mostly inconsistent with the reference\n"
        "1 = contradicts the reference or is wrong",
        needs_reference=True,
    ),
    "relevance": CriterionSpec(
        "Does the response directly address the task that was asked?",
        "5 = entirely focused on the task\n"
        "4 = addresses the task with minor digressions\n"
        "3 = partly addresses the task\n"
        "2 = mostly off-topic\n"
        "1 = does not address the task",
    ),
    "groundedness": CriterionSpec(
        "Is every factual claim in the response supported by the context (tool results)? "
        "Claims not found in the context count as unsupported even if they may be true.",
        "5 = every claim is supported by the context\n"
        "4 = nearly all claims supported; one minor unsupported detail\n"
        "3 = some material claims are unsupported\n"
        "2 = most claims are unsupported\n"
        "1 = the response contradicts the context or is fabricated",
        needs_context=True,
    ),
    "completeness": CriterionSpec(
        "Does the response cover every part of the task (and of the reference answer, if "
        "one is given)?",
        "5 = covers every requested part\n"
        "4 = misses only a minor detail\n"
        "3 = misses at least one requested part\n"
        "2 = covers only a small part of what was asked\n"
        "1 = covers none of what was asked",
    ),
    "instruction_following": CriterionSpec(
        "Does the response follow every explicit instruction in the task (format, length, "
        "required items, constraints)?",
        "5 = follows every instruction\n"
        "4 = one minor deviation\n"
        "3 = at least one clear instruction not followed\n"
        "2 = most instructions not followed\n"
        "1 = ignores the instructions",
    ),
    "custom": CriterionSpec(
        "Does the response satisfy the rubric below?",
        "5 = fully satisfies the rubric\n"
        "4 = satisfies it with a minor shortfall\n"
        "3 = partly satisfies it\n"
        "2 = mostly fails it\n"
        "1 = fails it entirely",
    ),
}

USER_TEMPLATE = """Evaluate the response on this criterion.

<criterion>
{question}
{rubric}
</criterion>

<scale>
{scale}
</scale>

<task>
{task}
</task>
{reference}{context}
<response>
{response}
</response>

Reply with JSON: first "reasoning" (your analysis of the evidence), then "score" (1-5)."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "score": {"type": "integer", "enum": [1, 2, 3, 4, 5]},
    },
    "required": ["reasoning", "score"],
    "additionalProperties": False,
}


def prompt_sha256(cfg: LLMJudgeConfig) -> str:
    """Hash of everything that defines the judge prompt except the case data."""
    spec = CRITERIA[cfg.criterion]
    material = "\x1f".join(
        [PROMPT_VERSION, SYSTEM_PROMPT, USER_TEMPLATE, spec.question, spec.scale, cfg.rubric or ""]
    )
    return hashlib.sha256(material.encode()).hexdigest()


def _context(x: EvalInput) -> str:
    parts = [
        f"[{c.name}({json.dumps(c.arguments, sort_keys=True)})]\n{c.result}"
        for c in x.tool_calls
        if c.success and c.result
    ]
    return "\n\n".join(parts)


def render(cfg: LLMJudgeConfig, x: EvalInput) -> str | Verdict:
    """The judge prompt, or a skipped Verdict when the criterion can't apply."""
    spec = CRITERIA[cfg.criterion]
    if spec.needs_reference and not x.expected_output:
        return Verdict.skipped("criterion needs a reference answer; case has none")
    context = _context(x)
    if spec.needs_context and not context:
        return Verdict.skipped("criterion needs context; the run produced no tool results")
    return USER_TEMPLATE.format(
        question=spec.question,
        rubric=f"\nRubric:\n{cfg.rubric}" if cfg.rubric else "",
        scale=spec.scale,
        task=x.case_input,
        reference=(
            f"\n<reference_answer>\n{x.expected_output}\n</reference_answer>\n"
            if x.expected_output and cfg.criterion in ("correctness", "completeness")
            else ""
        ),
        context=f"\n<context>\n{context}\n</context>\n" if spec.needs_context else "",
        response=x.output,
    )


def judge(cfg: LLMJudgeConfig, x: EvalInput, provider: LLMProvider) -> Verdict:
    if x.run_status != "succeeded" or x.output is None:
        # The agent failed; there is nothing to judge, and failing is a quality failure.
        return Verdict.ok(0.0, cfg.pass_threshold, f"agent run failed: {x.run_error}")

    prompt = render(cfg, x)
    if isinstance(prompt, Verdict):
        return prompt

    audit = dict(
        provider=cfg.provider,
        model=cfg.model,
        prompt_version=PROMPT_VERSION,
        prompt_sha256=prompt_sha256(cfg),
    )
    request = LLMRequest(
        model=cfg.model,
        system=SYSTEM_PROMPT,
        messages=[Message(role="user", text=prompt)],
        max_tokens=cfg.max_tokens,
        temperature=0.0,
        json_schema=OUTPUT_SCHEMA,
    )
    try:
        resp = provider.complete(request)
    except ProviderError as e:
        v = Verdict.error(f"judge call failed: {e}")
        v.judge = JudgeAudit(**audit)
        return v

    audit.update(
        input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens,
        cache_creation_input_tokens=resp.cache_creation_input_tokens,
        cache_read_input_tokens=resp.cache_read_input_tokens,
        raw_response=resp.text,
    )
    try:
        data = json.loads(resp.text)
        raw_score, reasoning = data["score"], data["reasoning"]
        if not (isinstance(raw_score, int) and 1 <= raw_score <= 5):
            raise ValueError(f"score out of range: {raw_score!r}")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        v = Verdict.error(f"unparseable judge output ({type(e).__name__}: {e})")
        v.judge = JudgeAudit(**audit)
        return v

    v = Verdict.ok(
        (raw_score - 1) / 4,
        cfg.pass_threshold,
        str(reasoning),
        raw_score=raw_score,
        criterion=cfg.criterion,
    )
    v.judge = JudgeAudit(**audit)
    return v
