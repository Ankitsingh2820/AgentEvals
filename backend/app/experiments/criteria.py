from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AcceptanceCriteria(BaseModel):
    """When does a candidate count as a successful optimization?

    Every threshold is optional, but at least one must be set: "cheaper" alone is never
    enough (plan §2). Quality thresholds are in points on a 0-100 scale; cost/latency
    reductions are relative percentages.
    """

    model_config = ConfigDict(extra="forbid")

    min_quality: float | None = Field(default=None, ge=0, le=1)  # candidate mean score
    max_quality_drop_points: float | None = Field(default=None, ge=0, le=100)
    min_pass_rate: float | None = Field(default=None, ge=0, le=1)
    min_cost_reduction_pct: float | None = Field(default=None, ge=-100, le=100)
    min_latency_reduction_pct: float | None = Field(default=None, ge=-100, le=100)
    latency_stat: Literal["mean", "median", "p95"] = "mean"
    max_error_rate: float | None = Field(default=None, ge=0, le=1)

    # Guards against claims from tiny samples.
    min_pairs: int = Field(default=10, ge=1)
    # When true, improvements must be statistically supported, not just point estimates:
    # quality uses a non-inferiority test (CI lower bound above -max_quality_drop), cost and
    # latency a superiority test (CI of the paired difference entirely below zero).
    require_significance: bool = True
    confidence: float = Field(default=0.95, gt=0.5, lt=1)

    @model_validator(mode="after")
    def _at_least_one_threshold(self):
        thresholds = (
            self.min_quality,
            self.max_quality_drop_points,
            self.min_pass_rate,
            self.min_cost_reduction_pct,
            self.min_latency_reduction_pct,
            self.max_error_rate,
        )
        if all(t is None for t in thresholds):
            raise ValueError("set at least one acceptance threshold")
        return self
