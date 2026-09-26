"""OpenTelemetry setup.

AgentEval's own trace tables are the source of truth for evaluation. OpenTelemetry mirrors
the same structure (run -> LLM calls / tool calls) as spans, so runs also show up in
whatever tracing backend a team already uses (Jaeger, Tempo, Honeycomb, ...). Span
attributes follow the GenAI semantic conventions (gen_ai.*) where they exist. Prompt and
output content is never put on spans, only identifiers, models, token counts and costs.

Without an exporter endpoint the SDK is not installed and every span is a cheap no-op.
"""

import logging
import os

from opentelemetry import trace

from app.config import get_settings

tracer = trace.get_tracer("agenteval")
logger = logging.getLogger(__name__)
_configured = False


def setup_telemetry(app=None, span_exporter=None) -> bool:
    """Install the SDK and instrument FastAPI. `span_exporter` is for tests. Returns
    whether tracing is active."""
    global _configured
    settings = get_settings()
    endpoint = settings.otel_exporter_endpoint or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if _configured or (endpoint is None and span_exporter is None):
        return _configured

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))
    if span_exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    else:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        base = endpoint.rstrip("/")
        url = base if base.endswith("/v1/traces") else f"{base}/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=url)))
    trace.set_tracer_provider(provider)
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health")
    _configured = True
    logger.info("opentelemetry enabled", extra={"exporter": "test" if span_exporter else endpoint})
    return True
