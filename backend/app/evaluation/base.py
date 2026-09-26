from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ToolCallView:
    name: str
    arguments: dict[str, Any]
    result: str | None
    success: bool


@dataclass(frozen=True)
class EvalInput:
    """Everything an evaluator may look at: the test case and what the agent did."""

    case_input: str
    expected_output: str | None
    labels: dict[str, Any]
    output: str | None
    run_status: str
    run_error: str | None
    tool_calls: list[ToolCallView] = field(default_factory=list)


@dataclass(frozen=True)
class JudgeAudit:
    provider: str
    model: str
    prompt_version: str
    prompt_sha256: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    raw_response: str | None = None


@dataclass
class Verdict:
    """ok: a real score. skipped: check not applicable (e.g. no reference answer).
    error: the evaluator itself failed; never counted as a score of 0."""

    status: Literal["ok", "skipped", "error"]
    score: float | None = None
    passed: bool | None = None
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    judge: JudgeAudit | None = None

    @classmethod
    def ok(cls, score: float, threshold: float, reason: str, **details: Any) -> "Verdict":
        return cls("ok", score=score, passed=score >= threshold, reason=reason, details=details)

    @classmethod
    def skipped(cls, reason: str) -> "Verdict":
        return cls("skipped", reason=reason)

    @classmethod
    def error(cls, reason: str) -> "Verdict":
        return cls("error", reason=reason)
