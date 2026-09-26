"""Deterministic evaluators: same input, same verdict, no model involved. Prefer these
wherever a property can be checked mechanically; they are cheap, fast and unbiased."""

import json
import re

from jsonschema import Draft202012Validator

from app.evaluation.base import EvalInput, Verdict
from app.evaluation.configs import (
    ContainsConfig,
    ExactMatchConfig,
    JsonSchemaConfig,
    RegexConfig,
    ToolCallsConfig,
)


def _normalize(text: str) -> str:
    return " ".join(text.split()).lower()


def exact_match(cfg: ExactMatchConfig, x: EvalInput) -> Verdict:
    if x.expected_output is None:
        return Verdict.skipped("case has no expected_output")
    out = x.output or ""
    a, b = (
        (_normalize(out), _normalize(x.expected_output))
        if cfg.normalize
        else (out, x.expected_output)
    )
    match = a == b
    return Verdict.ok(1.0 if match else 0.0, 1.0, "exact match" if match else "output differs")


def contains(cfg: ContainsConfig, x: EvalInput) -> Verdict:
    required = list(cfg.values)
    if cfg.labels_key:
        extra = x.labels.get(cfg.labels_key, [])
        required += [extra] if isinstance(extra, str) else list(extra)
    if not required:
        return Verdict.skipped(f"no required values (labels['{cfg.labels_key}'] missing)")

    out = x.output or ""
    haystack = out if cfg.case_sensitive else out.lower()
    found = [v for v in required if (v if cfg.case_sensitive else v.lower()) in haystack]
    missing = [v for v in required if v not in found]
    if cfg.mode == "any":
        score = 1.0 if found else 0.0
    else:
        score = len(found) / len(required)
    reason = "all required values present" if not missing else f"missing: {missing}"
    return Verdict.ok(score, cfg.pass_threshold, reason, found=found, missing=missing)


def regex(cfg: RegexConfig, x: EvalInput) -> Verdict:
    flags = re.IGNORECASE if cfg.ignore_case else 0
    matched = re.search(cfg.pattern, x.output or "", flags) is not None
    ok = matched == cfg.must_match
    reason = f"pattern {'found' if matched else 'not found'} (must_match={cfg.must_match})"
    return Verdict.ok(1.0 if ok else 0.0, 1.0, reason)


_FENCED = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


def _extract_json(text: str):
    """Parse the whole output as JSON, else the first ```json fenced block."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _FENCED.search(text)
    if m:
        return json.loads(m.group(1))  # may raise; caller reports it
    raise json.JSONDecodeError("no JSON found in output", text, 0)


def json_schema(cfg: JsonSchemaConfig, x: EvalInput) -> Verdict:
    try:
        data = _extract_json(x.output or "")
    except json.JSONDecodeError as e:
        return Verdict.ok(0.0, 1.0, f"output is not valid JSON: {e.msg}")
    errors = sorted(Draft202012Validator(cfg.json_schema).iter_errors(data), key=str)
    if not errors:
        return Verdict.ok(1.0, 1.0, "valid against schema")
    messages = [f"{'/'.join(map(str, e.absolute_path)) or '$'}: {e.message}" for e in errors]
    return Verdict.ok(0.0, 1.0, f"{len(errors)} schema violation(s)", errors=messages[:10])


def tool_calls(cfg: ToolCallsConfig, x: EvalInput) -> Verdict:
    used = [c.name for c in x.tool_calls]
    checks: dict[str, bool] = {}
    for name in cfg.required:
        checks[f"called {name}"] = name in used
    for name in cfg.forbidden:
        checks[f"did not call {name}"] = name not in used
    if cfg.max_calls is not None:
        checks[f"at most {cfg.max_calls} tool calls"] = len(used) <= cfg.max_calls
    if cfg.require_success:
        checks["all tool calls succeeded"] = all(c.success for c in x.tool_calls)

    failed = [k for k, v in checks.items() if not v]
    score = sum(checks.values()) / len(checks)
    reason = "all tool checks passed" if not failed else f"failed: {failed}"
    return Verdict.ok(score, cfg.pass_threshold, reason, checks=checks, tools_used=used)
