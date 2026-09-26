# Architecture

```text
            Dashboard (React/TS, nginx)            API clients / CI
                      │  /api/*                          │
                      ▼                                  ▼
        ┌──────────────────────────── FastAPI ────────────────────────────┐
        │ request id · API-key auth · rate limits · error envelope         │
        │ agents · runs · datasets · evaluators · evaluations · experiments│
        │ pricing · metrics · optimizations                                │
        └──────┬───────────────────────────┬──────────────────────┬───────┘
               │ POST /runs (sync)         │ evaluations,         │ reads
               ▼                           │ experiments          ▼
        Execution engine                   ▼                PostgreSQL
        (executor, tracer,        tasks.dispatch ──► Redis ──► Celery worker
         router, options)          (inline in dev)              │
               │                                               ▼
               ├──► Providers (Anthropic, mock)      Evaluation runner / Experiment runner
               ├──► Tools (registry)                  ├─ evaluators (deterministic, LLM judge)
               └──► Cost service ◄── pricing registry └─ paired comparison + verdict
               │
               └──► OpenTelemetry spans ──► OTLP collector (Jaeger, …)
```

## Components (plan §26.7: kept separate)

| Concern | Module | Responsibility |
|---|---|---|
| Agent execution | `engine/executor.py` | The model → tools → model loop; routing, parallel tools, dedupe, budget, retries |
| Tracing | `engine/tracer.py` | Timed steps (monotonic clock), turns, token and latency totals |
| Providers | `providers/` | Provider-neutral request/response; Anthropic adapter; deterministic mock |
| Cost | `costs/` | Append-only price registry; pure `Decimal` calculator; prices a run once, when it finishes |
| Evaluation | `evaluation/` | Evaluator configs, deterministic checks, LLM judge, resumable runner, summaries |
| Experiments | `experiments/` | Interleaved arms, paired statistics, acceptance criteria, verdict |
| Optimization | `optimization/` | Evidence-based recommendations from traces |
| Metrics | `metrics/stats.py` | Percentiles, summaries, % change, bootstrap confidence intervals |
| Security | `auth.py`, `ratelimit.py`, `main.py` | Hashed API keys, per-key limits, error envelope, request ids |
| Background work | `tasks.py`, `worker.py` | Inline or Celery dispatch; at-least-once delivery with resume |
| Observability | `telemetry.py`, `logging_config.py` | OTel spans (GenAI conventions), JSON logs with redaction |

## Data model

```text
Agent ─< AgentVersion (immutable config + options)
                │
Experiment ─────┼─ baseline/candidate versions, dataset, evaluators, criteria, snapshot, verdict
   └─< Evaluation (arm) ─< Run ─< RunStep ─┬─ LLMCall  (tokens, prices used, estimated cost)
          │                 │              └─ ToolCall (args, result, skip reason)
          │                 └── DatasetCase ── Dataset (versioned, immutable)
          └─< EvaluationResult (per run × evaluator: score, verdict, reason, judge audit)

ModelPricing / ToolPricing (append-only, effective-dated)     ApiKey (hash only)
OptimizationReport (recommendations + evidence)
```

Every run can be traced back through Agent → Version → Experiment → Dataset → Run →
Steps → Evaluations (plan §20).

## Request lifecycles

**`POST /runs`:** auth → execute rate limit → executor (route → LLM ⇄ tools, every
step timed and stored) → cost service prices the finished run → response. Synchronous.

**`POST /experiments/{id}/run`:** returns 202 → dispatched to the worker → for each case
and repetition, both arms run in alternating order → each run is scored by every evaluator
→ per-arm summaries → paired comparison → acceptance checks → PASS / FAIL / INCONCLUSIVE.
Progress is committed per case; a redelivered task resumes where it stopped.
