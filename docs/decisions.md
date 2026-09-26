# Architectural decisions

## ADR-001: Immutable agent versions

Agent configuration is never updated in place. `POST /agents/{id}/versions` creates a new
`agent_versions` row, and every run stores the `agent_version_id` it executed. Historical
runs therefore always show the exact model, prompt, tools and parameters they used, which
is the basis for reproducible experiments (plan §21).

## ADR-002: Provider-neutral interface, with the provider's native turn kept for replay

The engine only uses `LLMRequest`/`LLMResponse`/`Message` from `app/providers/base.py`.
Adapters translate to each SDK. Each assistant turn also carries `provider_content` (the
provider's native blocks), which the same adapter replays unchanged. This is needed
because Claude requires thinking blocks to round-trip unchanged during tool use; rebuilding
them from neutral fields would drop them.

## ADR-003: Manual agent loop instead of an SDK tool runner

The engine drives the call-model → run-tools → repeat loop itself, rather than using an
SDK helper or a framework such as LangGraph. That way the engine can time and record every
LLM call and tool call as its own trace step, and later phases can change the loop (for
example parallel tools or model routing) as an experiment variable. A framework can be
added later if an agent actually needs one (plan §19: no frameworks for their own sake).

## ADR-004: Latency uses a monotonic clock; steps store offsets

All latencies come from `time.perf_counter()`. Each step stores `start_offset_ms` from run
start plus `latency_ms`, so a trace renders directly as a timeline. The trace endpoint
reports `llm_ms`, `tool_ms` and `other_ms` (engine/DB overhead) so "where did the time
go?" is answerable. The run's step collection starts loaded, so tracing never triggers a
database read in the middle of a run.

## ADR-005: Failures are recorded, not raised

A provider error, refusal, truncation (`max_tokens`), unknown tool or iteration ceiling
marks the run `failed` with an `error` and keeps the partial trace. Tool exceptions are
returned to the model as error results and counted in `tool_failure_count`. That count
feeds reliability metrics later. Transport retries (429/5xx) are left to the provider SDK.

## ADR-006: No silent model fallback; unsupported params are dropped visibly

Server-side refusal fallbacks are not enabled. They would quietly switch the model under
test mid-run and make runs impossible to compare. Each LLM call stores both the requested
`model` and the provider-reported `response_model`. Models that reject `temperature` (Claude
Opus 5 and later) have it omitted. The stored request shows exactly what was sent, and a
warning is logged.

## ADR-007: Logs hold identifiers and metrics, never content

Structured JSON logs contain run/agent IDs, status, token counts and latency. Prompts,
outputs and tool payloads live only in the database trace, so logs can go to external sinks
without leaking sensitive data. API keys come from the environment only.

## ADR-008: Append-only pricing registry; prices are snapshotted onto each call

`model_pricing` and `tool_pricing` rows are never updated or deleted, and the API has no
PUT or DELETE for them. A price change is a new row with a later `effective_date`. A run is
priced once, when it finishes, using the entries in effect on its start date. Each LLM call
stores the resulting costs, the `pricing_id` and a JSON `pricing_snapshot` of the exact
prices used. Historical costs therefore never change when prices do, and every figure can
be explained. Money is stored as `Numeric` and computed with `Decimal`, so summing many
tiny per-call costs does not drift.

## ADR-009: Costs are labelled estimates; incomplete totals are withheld

Every cost field is named `estimated_*`, because it comes from list prices rather than a
provider invoice. Reconciling with billed amounts would be a separate, explicitly named
field. If any LLM call in a run cannot be priced (no registry entry, or cache tokens with
no cache price), the run's `cost_status` is `partial` or `unpriced` and its run-level totals
are NULL. A partial sum would understate the cost and quietly skew averages and
comparisons. Tools with no price entry count as free (most tools run locally).

## ADR-010: Latency stats use succeeded runs; cost stats use fully-priced runs

Agent metrics report mean, median, p95 and p99, not just the mean: a few slow runs can
hide behind a good average. Latency statistics exclude failed runs, since a fast failure
would make the agent look faster. Cost statistics include failed runs (they still cost
money) but only those with `cost_status = complete`. The rest are counted in
`runs_without_cost`. Percentiles use linear interpolation (numpy's default). With small
samples p95/p99 sit close to the maximum, so `count` is always returned alongside them.

## ADR-011: Datasets and evaluators are versioned and immutable

Like agents, datasets and evaluators are never edited in place. Posting an existing name
creates the next version. An evaluation pins one agent version, one dataset version and an
ordered list of evaluator versions, and each evaluator version stores its full config with
defaults filled in. Every score can therefore be reproduced from exactly what produced it,
and two configurations can be compared on exactly the same test cases.

## ADR-012: Three verdict states; evaluator failure is never a zero

Each verdict is `ok` (scored 0–1), `skipped` (the check doesn't apply, e.g. no reference
answer or no context) or `error` (the evaluator itself failed, e.g. unparseable judge
output). Only `ok` verdicts feed quality scores. Averaging errors in as zeros would make an
unreliable judge look like a bad agent. Error and skip counts are reported per evaluator
instead. An agent run that *fails* is different: that is the agent's fault, so it scores 0.

## ADR-013: LLM-as-a-judge design

One criterion per call, rated on an anchored 1–5 integer scale (mapped to 0–1).
Continuous scores from a model are poorly calibrated, and a described scale is steadier.
The judge gives its reasoning before its score, returns JSON through the provider's
structured-output mode, and never sees which configuration produced the output.
Groundedness is judged only against tool results the run actually received.
Criteria that can't apply are skipped before any paid call is made. Each verdict records
the judge provider and model, `prompt_version` and a SHA-256 of the full prompt template
and rubric, so scores from different judge prompts are never silently compared. Judge
token costs are priced from the registry and reported apart from the agent's cost.
Scores from a judge are evidence, not ground truth. Deterministic evaluators are
preferred wherever a property can be checked mechanically.

## ADR-014: Evaluations run as in-process background tasks, for now

`POST /evaluations` returns 202 and runs through FastAPI `BackgroundTasks`, using its own
DB session and committing after each case. This avoids a queue dependency while
evaluations are small. The known gap: a server restart mid-run leaves the evaluation
`running`. Moving `run_evaluation` onto a worker (Celery/Redis, plan §19) needs no change
to it, since it only takes a session factory and an evaluation id.

## ADR-015: Experiments are two evaluations, interleaved and paired

An experiment creates one evaluation per arm (`arm = baseline | candidate`), both on the
same dataset version, evaluator versions and repetition count. That is enforced by
construction, since a single experiment record defines both. Cases run interleaved and
alternate which arm goes first, so provider latency drift, warm caches or rate limiting
affect both arms equally. Runs are paired by (case, repetition), and the analysis uses
paired differences: case difficulty cancels out, so real effects show with fewer cases.
There is no separate `experiment_runs` table; runs carry `experiment_id`, `evaluation_id`
(which gives the arm), `dataset_case_id` and `repetition`.

## ADR-016: PASS / FAIL / INCONCLUSIVE, and significance by default

An optimization passes only if every configured threshold is met (plan §2, §17). A missed
threshold is FAIL. A threshold met on average but without statistical support is
INCONCLUSIVE, as is any result with fewer than `min_pairs` pairs. "Supported" means a 95%
percentile-bootstrap CI of the paired mean difference (2,000 resamples, fixed seed so
reports are reproducible). Cost and latency use superiority (CI entirely below zero), and
quality uses non-inferiority (CI lower bound above −margin). For latency the threshold can
apply to mean, median or p95, but significance is always tested on the paired mean. We
chose the bootstrap over a t-test because costs and latencies are skewed. Setting
`require_significance: false` judges on averages alone, and the reasons say which checks
relied on that.

## ADR-017: Reproducibility = pinned config + snapshot + reproduce

At creation an experiment freezes a `config_snapshot`: both agent configs, the dataset
name/version/case count, each evaluator's full config and version, the judge prompt
version, repetitions and criteria. An experiment runs once. `POST /reproduce` creates a
new experiment with the identical pinned references (`reproduction_of` links them), so a
re-run never overwrites the original result and the two can be compared.

## ADR-018: Optimizations are versioned agent options, not code paths

Routing, parallel tools, dedupe, tool budget and prompt caching are fields of a typed
`options` object on the agent version, and all are off by default. Enabling one creates a
new version, so every optimization is automatically an experiment variable with a clean
`config_diff`, and `isolate` can prove only that variable changed. The recommendation
`apply` endpoint only creates a candidate version (and optionally its experiment). It
never promotes anything; the experiment verdict decides (plan §2).

## ADR-019: Routing overhead and skipped work are measured, not hidden

A classifier router makes a real LLM call. It is recorded as its own step (`turn 0`,
`purpose: routing`), counted in LLM calls, latency and cost, so a routing experiment
compares the full cost of routing, not just the cheaper model. If the classifier fails,
the run uses the version's default model (`route: fallback`) instead of failing.
Deduplicated or budget-refused tool calls are recorded as steps with a `skip_reason` and
excluded from `tool_call_count`, which counts executions. With parallel tools, step
latencies overlap, so the run's `tool_latency_ms` is wall-clock time per batch, not the sum
of step latencies.

## ADR-020: Steps carry the agent-loop turn

Each step records its turn: an LLM call and the tool calls it requested share one. That
is what makes "these sequential calls could have run in parallel" detectable from a trace.
Existing traces were backfilled in the migration from step order.

## ADR-021: Recommendations state their evidence and their limits

Each recommendation includes the observed evidence, the arithmetic behind any estimate
(`basis`), caveats, a `confidence` of `low` under 20 runs, and a suggested config. Latency
estimates are upper bounds from observed step latencies. Caching savings use the registry's
cache prices and only count prefixes of at least 1,024 tokens, a conservative stand-in for
the model-dependent minimum of 512–4,096. Routing savings are a cost-only repricing of
recorded tokens and are explicitly marked quality-unverified. Nothing is presented as an
achieved improvement.

## ADR-022: The mock provider simulates caching, routing and multi-tool turns, labelled

To exercise these paths offline, the mock provider can call several tools per turn, returns
a fixed routing label, and simulates cache hits when `cache` is set. Everything simulated
is marked as such in code and output. Mock-provider numbers demonstrate the pipeline; they
are never evidence about real models.

## ADR-023: Dashboard stack and data access

React + TypeScript + Tailwind + Recharts (plan §19), built with Vite. There's no data-fetching
library: a small `useApi` hook keeps the previous data while refetching (charts dim
instead of flashing a skeleton) and polls running evaluations and experiments. The
dashboard only calls `/api/*`. In development Vite proxies it to FastAPI; in Docker nginx
serves the static build and proxies `/api/` to the API container. Being same-origin, it
needs no CORS configuration. Money stays a decimal string from the API and is parsed
only for display.

## ADR-024: Charts follow a validated palette and fixed chart rules

Colors come from a validated reference palette, defined as CSS roles with separately
tuned light and dark values. Blue and orange are the only series colors, and their pair
passed the colorblind-separation and contrast checks in both modes. Verdicts and statuses
use status colors paired with an icon and label, so meaning never rides on color alone.
The rules: one y-axis per chart (latency median and p95 share a unit; cost and latency
never share a plot), 2px lines, bars at most 24px wide with rounded data-ends, hairline
solid grid, a crosshair tooltip listing every series, a legend only for 2+ series, and a
Chart/Table toggle so every value is readable without hovering. Headline numbers are stat
tiles, not charts. Days without data are gaps, not zeros.

## ADR-025: API keys, hashed; single tenant

Clients authenticate with random 256-bit keys (`ae_…`) in `Authorization: Bearer`. Only
SHA-256 hashes are stored. A fast hash is right for high-entropy random tokens, unlike
passwords, which need a slow KDF. Keys are managed through a CLI, not the API, so a leaked
key cannot mint more keys. An optional bootstrap key makes fresh Docker deployments usable.
There is one tenant (every key sees all data); multi-tenancy would add an owner id to every
table and query. Auth can be disabled only by explicit configuration, and startup logs a
warning when it is.

## ADR-026: Two rate-limit scopes, per key

A general limit applies to every authenticated request. A much stricter `execute` limit
applies to endpoints that run agents, because those spend provider money and one runaway
script could otherwise burn a budget. It uses a token bucket in memory by default, or fixed
one-minute windows in Redis when configured, so limits hold across API replicas.

## ADR-027: One error envelope and a request id everywhere

Every response carries `X-Request-ID`, and every error body is `{detail, request_id}`.
Unhandled exceptions are converted inside the request middleware, not by a
framework-level handler, because Starlette's outermost error middleware runs after the
request context is gone. The id would be missing exactly when it's most needed (found by
a test). Clients never see internals.

## ADR-028: Retries belong to the engine, not the SDK

SDK retries are invisible: a call that succeeded on the third attempt looks like one slow
call. The executor retries only transient failures (rate limit, overload, connection),
with exponential backoff. It records each attempt as a failed step and counts
`run.retry_count`, so retries show up in traces, latency and experiment reliability
metrics. Permanent errors fail fast.

## ADR-029: Durable background work with at-least-once delivery

Evaluations and experiments run on a Celery worker via Redis (the Docker default), with
late acknowledgement and re-queue on worker loss. At-least-once delivery requires
idempotent tasks. A finished evaluation is skipped if delivered again, and an interrupted
one resumes: fully scored cases are reused, while a half-finished run is detached rather
than deleted, because it happened and cost money. Its case then runs again. Results are
committed per case. The in-process backend remains for development and tests.

## ADR-030: OpenTelemetry mirrors the trace; the database stays the source of truth

Runs, LLM calls and tool calls are emitted as OTel spans named and attributed per the
GenAI semantic conventions (`gen_ai.*`), plus `agenteval.*` ids, cost and retry counts.
The database trace remains authoritative for evaluation; OTel lets the same runs appear
in a team's existing tracing backend. No prompt or response content goes on spans. Worker
threads for parallel tools get the parent context explicitly, since thread pools don't
propagate it. Without an exporter configured, the SDK is not installed and spans cost
nothing.

## ADR-031: CI mirrors the plan's pipeline and never spends money

Lint → unit → integration (plus migrations on real PostgreSQL: upgrade, `alembic check`,
downgrade, upgrade) → evaluation regression suite → Docker builds. Test stages are pytest
markers assigned in `conftest.py` by module, so a new test lands in a stage automatically.
Everything uses the mock provider: CI needs no secrets, costs nothing, and is
deterministic.

## ADR-032: Leases plus a recovery sweep, because broker redelivery alone is not enough

At-least-once delivery has two failure modes that idempotent resume alone doesn't cover:

1. **Concurrent duplicates.** Redis redelivers a task that outlives the visibility
   timeout even while it is still running.
2. **Slow recovery.** kombu's Redis transport restores timed-out messages only about every
   100 s (its restore check runs every 10 s but acts on every 10th call). We measured 103 s
   from a `SIGKILL` to redelivery with a 10 s visibility timeout.

A lease on the evaluation or experiment row fixes the first: an atomic claim, renewed after
every case, released at the end. A delivery that can't claim re-queues itself for when the
lease would lapse. The lease owner is unique per *execution*, not the Celery task id,
because a redelivered message keeps the original id and would otherwise share the live
lease. A per-worker sweep fixes the second: anything `running` with a lapsed lease is
re-queued, bounding recovery by lease + sweep interval (measured: 20 s). The sweep skips an
experiment's arm evaluations, which only the experiment may drive. Pool processes discard
inherited DB connections, because the sweeper opens connections in the parent process.

## ADR-033: Frontend tests at two levels

Vitest + Testing Library cover formatting and the components whose correctness is a rule
(status never shown by color alone; the direction of a delta; the sign-in flow including
key storage and the bearer header). One Playwright test drives the real dashboard against
the real stack through the whole loop: sign in, analyze, apply, experiment, verdict,
inspect a run, reproduce, sign out. It creates its own data through the API with the mock
provider, so it is deterministic and free. A text-matcher bug in an early version passed
without checking anything whenever data loaded slowly; assertions now use exact matches
that force a wait for the element that matters.

## ADR-034: One OpenAI-compatible adapter for OpenAI and Groq

OpenAI and Groq share the Chat Completions API, so one adapter
(`providers/openai_compatible.py`, official `openai` SDK) serves both, differing only in
base URL and key. Other compatible endpoints need just a registration. Model capabilities
vary and change, so the adapter doesn't keep model lists. If the API rejects `temperature`
it retries once without it. If strict JSON-schema output isn't supported (some Groq
models) it falls back to JSON mode. The stored request always records what was finally
sent. Keys are `SecretStr` settings under the standard variable names, so they never
appear in reprs or logs, and a missing key fails the run with a clear message, not a
crash. Cached prompt tokens reported by these APIs are recorded as cache reads.

## ADR-035: Prompt caching is opt-in for some providers and automatic for others

Anthropic caches only when asked (`cache_control`). OpenAI and Groq cache long prompt
prefixes by themselves. Each provider declares its mode at registration (`opt_in` or
`automatic`), readable without credentials. Where caching is automatic, the
`prompt_caching` option is rejected: a version differing only by a switch that does
nothing would make an experiment claim a change that never happened. For the same reason
the analyzer recommends caching only for opt-in providers; for automatic ones the usage
already reports the tokens served from cache, so an estimated "saving" would count
savings that were already received. Cached tokens are recorded and priced for every
provider.
