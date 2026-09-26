"""Typed, validated configuration for each evaluator type.

Evaluator configs are stored as JSON on immutable evaluator versions; these models are
the single place their shape and defaults are defined.
"""

import re
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExactMatchConfig(_Config):
    """Output equals the case's expected_output. Skipped when the case has none."""

    normalize: bool = True  # trim, lowercase, collapse whitespace before comparing


class ContainsConfig(_Config):
    """Output contains required strings, given inline and/or per case via labels."""

    values: list[str] = Field(default_factory=list)
    labels_key: str | None = None  # read extra required strings from case.labels[key]
    case_sensitive: bool = False
    mode: Literal["all", "any"] = "all"
    pass_threshold: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def _needs_a_source(self):
        if not self.values and not self.labels_key:
            raise ValueError("set 'values' and/or 'labels_key'")
        return self


class RegexConfig(_Config):
    pattern: str
    must_match: bool = True
    ignore_case: bool = False

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, v: str) -> str:
        try:
            re.compile(v)
        except re.error as e:
            raise ValueError(f"invalid regex: {e}") from e
        return v


class JsonSchemaConfig(_Config):
    """Output is (or contains a fenced block of) JSON valid against `json_schema`. Covers
    'valid JSON', 'required fields' and 'expected number of items' (minItems/maxItems)."""

    json_schema: dict[str, Any]

    @field_validator("json_schema")
    @classmethod
    def _valid_schema(cls, v: dict[str, Any]) -> dict[str, Any]:
        try:
            Draft202012Validator.check_schema(v)
        except SchemaError as e:
            raise ValueError(f"invalid JSON schema: {e.message}") from e
        return v


class ToolCallsConfig(_Config):
    """Checks the run's tool usage from its trace."""

    required: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    max_calls: int | None = Field(default=None, ge=0)
    require_success: bool = False  # every tool call must have succeeded
    pass_threshold: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def _has_a_check(self):
        if not (
            self.required or self.forbidden or self.max_calls is not None or self.require_success
        ):
            raise ValueError("configure at least one check")
        return self


Criterion = Literal[
    "correctness",
    "relevance",
    "groundedness",
    "completeness",
    "instruction_following",
    "custom",
]


class LLMJudgeConfig(_Config):
    criterion: Criterion
    rubric: str | None = None  # required for "custom"; appended for the others
    provider: str
    model: str
    # Judge scores are on a 1-5 scale mapped to 0..1; 0.75 means "4 or better passes".
    pass_threshold: float = Field(default=0.75, ge=0, le=1)
    max_tokens: int = Field(default=4000, ge=256, le=64000)

    @model_validator(mode="after")
    def _custom_needs_rubric(self):
        if self.criterion == "custom" and not self.rubric:
            raise ValueError("criterion 'custom' requires a rubric")
        return self


CONFIG_TYPES: dict[str, type[_Config]] = {
    "exact_match": ExactMatchConfig,
    "contains": ContainsConfig,
    "regex": RegexConfig,
    "json_schema": JsonSchemaConfig,
    "tool_calls": ToolCallsConfig,
    "llm_judge": LLMJudgeConfig,
}


def parse_config(type_: str, config: dict[str, Any]) -> _Config:
    if type_ not in CONFIG_TYPES:
        raise ValueError(f"unknown evaluator type '{type_}'; available: {sorted(CONFIG_TYPES)}")
    return CONFIG_TYPES[type_].model_validate(config)
