# Design FAQ

Answers to the questions in plan §30, each pointing at the code that implements it.

## Architecture

**How does tracing work?** `RunTracer` (`engine/tracer.py`) records every LLM call and tool
call as a `RunStep`. Each step has a start offset from the run's start and a latency, both
from `time.perf_counter()`, plus the agent-loop turn it belongs to. Typed child rows hold
details: tokens, prices and cost for LLM calls; arguments, result and skip reason for tool
calls. The same structure is mirrored as OpenTelemetry spans for external tracing tools.

**Why FastAPI?** Typed request/response models (Pydantic) give validation and OpenAPI docs
for free. Dependency injection makes auth, rate limits and DB sessions composable per
router, and the same Pydantic types validate agent options and evaluator configs.

**Why PostgreSQL?** The data is relational (agent → version → run → steps → results),
comparisons need joins and aggregates, money needs exact `NUMERIC`, and JSON columns cover
the semi-structured parts (requests, configs, summaries).

**How are agent runs modeled?** A run pins an immutable `AgentVersion`. Steps are ordered
by sequence and grouped by turn. Run-level counters and latency totals are stored, not
derived, so metrics can be aggregated directly.

## LLM

**How is token cost calculated?** `tokens / 1,000,000 × price per million`, separately for
input, output, cache writes and cache reads (`costs/calculator.py`), in `Decimal`.

**How are different model prices handled?** An append-only, effective-dated registry. A
run is priced once, when it finishes, with the prices in effect on its start date. The
prices used are copied onto each call, so later price changes never rewrite history.
Missing prices make a run's cost `partial`/`unpriced` instead of silently undercounting.

**How are models compared?** An experiment with two agent versions that differ only in
`model` (`isolate: ["model"]` enforces that), the same dataset, evaluators and repetitions,
judged on paired differences.

## Evaluation

**How does LLM-as-a-judge work?** One criterion per call, rated on a 1–5 scale where each
level has a written description, reasoning before the score, and structured JSON output.
The judge never sees which configuration produced the answer (`evaluation/judge.py`).

**How is evaluator bias reduced?**
- Deterministic checks are used wherever possible.
- Each judge call rates one criterion on a scale with a written description per level.
- The judge can be a different model from the agent.
- The judge prompt is versioned and hashed, so scores from different prompts are never
  mixed silently.
- A judge failure is recorded as an error, not a zero.
- A/A experiments measure the noise floor.

**How is hallucination / groundedness evaluated?** The `groundedness` criterion gives the
judge only the tool results the run actually received and counts any claim not in them as
unsupported. It is skipped when a run has no context, instead of guessing.

## Agents

**How are tool calls traced?** Each is a step with arguments, result, success, latency and
turn. Deduplicated or budget-refused calls are recorded with a `skip_reason`.

**How are unnecessary tool calls detected?** From traces (`optimization/analyzer.py`):
- identical calls repeated within a run
- runs far above the median tool count
- several calls in one turn that ran sequentially

**How are agent workflows optimized?** Each optimization (routing, parallel tools, dedupe,
tool budget, prompt caching) is an option on a new version. An experiment measures it
against the old version, and only a PASS justifies switching.

## Performance

**How is latency measured?** A monotonic clock at run, step and batch level. Each run
splits into LLM time, tool time (wall clock per batch) and other overhead. Aggregates report
the mean, p50, p95 and p99.

**Why p95 and not only the average?** A few slow runs hide in a mean (tested in
`test_stats.py`: 95 fast runs + 5 slow ones give a median of 100 ms but a p99 of 5 s).
Users feel the tail.

**How does parallel execution reduce latency?** Independent tool calls from one model turn
run concurrently, so the batch takes as long as its slowest call instead of the sum. The
analyzer's estimate (sum − max) is an upper bound; the experiment measures what happens.

## Optimization

**When is an optimization safe?** When every acceptance threshold holds with statistical
support. Quality must pass a non-inferiority test; cost and latency must show a real
improvement (confidence interval below zero); error rate must stay under its limit; and
there must be enough paired runs. Otherwise the verdict is FAIL or INCONCLUSIVE.

**How is quality traded against cost?** Explicitly, in the criteria: e.g. accept a quality
drop of at most 2 points only if cost falls by at least 20%.

**How do you stop cheaper models degrading quality?** Routing is only switched on through
an experiment gated on quality. The per-case view lists regressions first, so an average
can't hide a broken category.

## Production

**How are retries handled?** In the executor, only for transient failures, with exponential
backoff. Each attempt is visible in the trace and counted in retry metrics.

**How are provider failures handled?** Retries first. After that the run fails with the
provider's error recorded. Refusals and truncation are explicit failure reasons. A failing
routing classifier falls back to the default model.

**How are API keys managed?** Provider keys: environment only, read by the SDK, redacted
from logs. AgentEval keys: random tokens stored as SHA-256 hashes, created and revoked with
a CLI.

**How do you test nondeterministic AI systems?**
- A deterministic mock provider for unit and integration tests.
- A fixed regression dataset run in CI.
- Repetitions per case in experiments to measure variance.
- Paired statistics with seeded bootstrap intervals.
- A/A experiments to measure the noise floor.
