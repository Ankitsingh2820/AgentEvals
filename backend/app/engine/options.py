"""Optimization options on an agent version. Each is off by default, so turning one on
creates a new version that an experiment can compare against the old one."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=100)
    description: str | None = None  # shown to the classifier in "classifier" mode
    # Conditions for "rules" mode; all set conditions must hold. None = no condition.
    max_input_chars: int | None = Field(default=None, ge=0)
    min_input_chars: int | None = Field(default=None, ge=0)
    input_regex: str | None = None

    @field_validator("input_regex")
    @classmethod
    def _compiles(cls, v):
        if v is not None:
            try:
                re.compile(v)
            except re.error as e:
                raise ValueError(f"invalid regex: {e}") from e
        return v


class RoutingConfig(BaseModel):
    """Pick the model per request.

    rules: the first route whose conditions match the input wins. No match means the
    version's own model is used.
    classifier: a (cheap) model labels the input with one of the route labels. The
    classifier call is traced and costed like any other LLM call, so its overhead is part
    of every comparison. If it fails, the version's own model is used.
    """

    model_config = ConfigDict(extra="forbid")

    mode: Literal["rules", "classifier"]
    routes: list[Route] = Field(min_length=1)
    classifier_model: str | None = None

    @model_validator(mode="after")
    def _check(self):
        labels = [r.label for r in self.routes]
        if len(set(labels)) != len(labels):
            raise ValueError("route labels must be unique")
        if self.mode == "classifier":
            if not self.classifier_model:
                raise ValueError("classifier mode requires classifier_model")
            if len(self.routes) < 2:
                raise ValueError("classifier mode needs at least two routes")
        return self


class AgentOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routing: RoutingConfig | None = None
    # Run the tool calls from one model turn concurrently instead of one after another.
    parallel_tool_calls: bool = False
    # Reuse the result of an identical tool call (same name + arguments) within a run.
    dedupe_tool_calls: bool = False
    # Hard cap on tool executions per run; further calls get an error result instead.
    max_tool_calls: int | None = Field(default=None, ge=0)
    # Ask the provider to cache the prompt prefix between the calls of one run.
    prompt_caching: bool = False
