"""Model routing: choose which model handles a request, before the agent loop starts."""

import json
import re
from dataclasses import dataclass

from app.engine.options import Route, RoutingConfig
from app.providers.base import LLMProvider, LLMRequest, LLMResponse, Message, ProviderError

CLASSIFIER_SYSTEM = (
    "You route tasks to the right model. Read the task and pick the single route whose "
    "description fits it best. Answer with JSON: a short reasoning, then the label."
)


@dataclass
class RouteDecision:
    label: str
    model: str
    # Present when a classifier call was made (it must be traced and costed).
    classifier_request: LLMRequest | None = None
    classifier_response: LLMResponse | None = None
    classifier_error: str | None = None


def _matches(route: Route, text: str) -> bool:
    if route.max_input_chars is not None and len(text) > route.max_input_chars:
        return False
    if route.min_input_chars is not None and len(text) < route.min_input_chars:
        return False
    if route.input_regex is not None and not re.search(route.input_regex, text):
        return False
    return True


def route_by_rules(cfg: RoutingConfig, text: str, default_model: str) -> RouteDecision:
    for route in cfg.routes:
        if _matches(route, text):
            return RouteDecision(route.label, route.model)
    return RouteDecision("default", default_model)


def classifier_request(cfg: RoutingConfig, text: str) -> LLMRequest:
    options = "\n".join(f"- {r.label}: {r.description or '(no description)'}" for r in cfg.routes)
    return LLMRequest(
        model=cfg.classifier_model,
        system=CLASSIFIER_SYSTEM,
        messages=[
            Message(role="user", text=f"<routes>\n{options}\n</routes>\n\n<task>\n{text}\n</task>")
        ],
        max_tokens=1024,
        temperature=0.0,
        json_schema={
            "type": "object",
            "properties": {
                "reasoning": {"type": "string"},
                "label": {"type": "string", "enum": [r.label for r in cfg.routes]},
            },
            "required": ["reasoning", "label"],
            "additionalProperties": False,
        },
    )


def route_by_classifier(
    cfg: RoutingConfig, text: str, default_model: str, provider: LLMProvider
) -> RouteDecision:
    request = classifier_request(cfg, text)
    try:
        response = provider.complete(request)
    except ProviderError as e:
        return RouteDecision("fallback", default_model, request, None, str(e))
    by_label = {r.label: r for r in cfg.routes}
    try:
        label = json.loads(response.text)["label"]
        route = by_label[label]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        # A broken classifier must not break the run: fall back to the default model.
        return RouteDecision(
            "fallback",
            default_model,
            request,
            response,
            f"unusable classifier output: {type(e).__name__}",
        )
    return RouteDecision(route.label, route.model, request, response)
