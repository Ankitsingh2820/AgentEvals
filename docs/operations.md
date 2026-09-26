# Operations

## Configuration

All settings are environment variables with the `AGENTEVAL_` prefix (or a `.env` file).
They are defined in `backend/app/config.py`.

| Variable | Default | Purpose |
|---|---|---|
| `AGENTEVAL_DATABASE_URL` | local PostgreSQL | SQLAlchemy URL (`postgresql+psycopg://…`) |
| `AGENTEVAL_AUTH_ENABLED` | `true` | Require an API key on every endpoint but `/health` |
| `AGENTEVAL_BOOTSTRAP_API_KEY` | – | Registered (hashed) at startup if no key exists |
| `AGENTEVAL_RATE_LIMIT_PER_MINUTE` | `120` | Per key, all endpoints |
| `AGENTEVAL_EXECUTE_RATE_LIMIT_PER_MINUTE` | `20` | Per key, endpoints that run agents (`POST /runs`, `/evaluations`, `/experiments/{id}/run`) |
| `AGENTEVAL_TASK_BACKEND` | `inline` | `inline` (in the API process) or `celery` (worker queue) |
| `AGENTEVAL_REDIS_URL` | – | Celery broker; also makes rate limits shared across API replicas |
| `AGENTEVAL_TASK_LEASE_SECONDS` | `600` | Lease on a running evaluation/experiment, renewed after every case; must exceed the slowest single case |
| `AGENTEVAL_TASK_RECOVERY_INTERVAL_SECONDS` | `30` | How often each worker re-queues work abandoned by a dead worker |
| `AGENTEVAL_TASK_VISIBILITY_TIMEOUT_SECONDS` | `900` | Redis redelivery of unacknowledged tasks (a slower backstop) |
| `AGENTEVAL_OTEL_EXPORTER_ENDPOINT` | – | OTLP/HTTP endpoint; tracing is off when unset (`OTEL_EXPORTER_OTLP_ENDPOINT` also works) |
| `AGENTEVAL_SERVICE_NAME` | `agenteval-api` | OpenTelemetry `service.name` |
| `AGENTEVAL_LLM_MAX_RETRIES` | `2` | Retries of transient provider failures |
| `AGENTEVAL_LLM_RETRY_BASE_SECONDS` | `1.0` | Exponential backoff base |
| `AGENTEVAL_MAX_AGENT_ITERATIONS` | `10` | LLM↔tool round trips per run before giving up |
| `AGENTEVAL_LLM_TIMEOUT_SECONDS` | `120` | Per provider request |
| `AGENTEVAL_LOG_LEVEL` | `INFO` | JSON logs on stdout |
| `ANTHROPIC_API_KEY` | – | Provider `anthropic`; read by the Anthropic SDK only, never stored or logged |
| `OPENAI_API_KEY` | – | Provider `openai` |
| `GROQ_API_KEY` | – | Provider `groq` (OpenAI-compatible API) |
| `AGENTEVAL_OPENAI_BASE_URL` / `AGENTEVAL_GROQ_BASE_URL` | OpenAI default / `https://api.groq.com/openai/v1` | Override for proxies or compatible gateways |

Check that a provider key works before spending anything (listing models is free):
`GET /providers/openai/models`, `GET /providers/groq/models`.

## API keys

```bash
python -m app.auth create "dashboard"   # prints the key once
python -m app.auth list                  # id, prefix, state, last use
python -m app.auth revoke <id>
# in Docker: docker compose exec api python -m app.auth …
```

Keys are 256-bit random tokens with an `ae_` prefix. Only their SHA-256 hash is stored.
`AGENTEVAL_BOOTSTRAP_API_KEY` is registered at startup whenever no *active* key exists.
So if every key has been revoked, setting a new bootstrap key restores access. A key
that was itself revoked is never re-activated.
Clients send `Authorization: Bearer <key>` (or `X-API-Key`). The dashboard asks for a
key at sign-in and keeps it in that browser's local storage. There is one tenant: every
valid key sees all data. Multi-tenant isolation would scope every query by an owner id.

## Errors

Every error response has the shape `{"detail": …, "request_id": …}`, and every response
carries an `X-Request-ID` header (taken from the request's own header if present). The same id
appears on the server's log lines for that request. Unhandled exceptions return a generic
500; the details stay in the server log. `429` responses include `Retry-After`.

## Background work

With `AGENTEVAL_TASK_BACKEND=celery` (the Docker default), evaluations and experiments
are queued on Redis and executed by:

```bash
celery -A app.worker worker --loglevel=INFO --concurrency=2
```

Delivery is at-least-once, and three mechanisms make that safe and fast:

- **Lease.** A task claims its evaluation or experiment with an atomic update and renews
  the claim after every case. A second delivery that finds a live lease re-queues itself
  for when the lease would lapse, so two workers never run the same work at once.
- **Resume.** A finished evaluation or experiment is skipped if delivered again. An
  interrupted one resumes: fully scored cases are reused, and a half-finished run is
  detached (kept as a standalone run, since it cost money) and its case runs again.
- **Recovery sweep.** Every worker re-queues work that is `running` but whose lease has
  lapsed, meaning its worker died. Recovery takes about *lease + sweep interval*. This
  matters because the broker's own redelivery is slow. kombu's Redis transport restores
  timed-out messages only about every 100 s, on top of the visibility timeout. We
  measured it: 103 s to recover without the sweep, 20 s with it (8 s lease, 5 s sweep).

Verified by hard-killing (`SIGKILL`) the worker in the middle of a 200-run experiment: it
completed with exactly one run per (case, repetition) per arm and all 200 verdicts. With
`inline`, work runs in the API process; a restart abandons it, and a Celery worker (if
configured) will pick it up. Use `inline` only for development and tests.

## Logging

JSON lines on stdout, with `request_id`, `trace_id`/`span_id` (when tracing is on), and
run/agent/evaluation ids and metrics. Prompts and outputs are never logged; they live in
the database trace. As a second line of defence, anything shaped like an Anthropic key, an
AgentEval key or a bearer token is replaced with `[REDACTED]` before a line is written.

## Tracing (OpenTelemetry)

Set `AGENTEVAL_OTEL_EXPORTER_ENDPOINT` (e.g. `http://jaeger:4318` with
`docker compose --profile observability up`). Each run emits:

```
agent.run                          agenteval.run.id, status, tool/llm counts, retries, cost
├── chat <model>                   gen_ai.system, gen_ai.request.model, gen_ai.usage.*, attempt
├── execute_tool <name>            gen_ai.tool.name (parallel tools keep this parent)
└── chat <model>
```

HTTP requests are instrumented too. Spans carry identifiers, models, token counts and
costs, never prompt or response content.

## Retries

The Anthropic SDK's own retries are disabled. The executor retries transient failures
(429, 5xx, connection errors) with exponential backoff. Every attempt is a failed step in
the run's trace, `run.retry_count` counts them, and experiments compare retry rates.
Permanent errors (400, auth) are not retried.

## CI

`.github/workflows/ci.yml` runs on pushes to `main` and on pull requests:

1. **Lint:** ruff check + format (backend), oxlint + `tsc` (frontend)
2. **Unit tests:** `pytest -m "not integration and not evaluation"`
3. **Integration tests:** `pytest -m integration`, then migrations on a real PostgreSQL:
   upgrade → `alembic check` (models match) → downgrade to base → upgrade → seed
4. **Evaluation:** the regression suite over `backend/datasets/company_research.jsonl`
5. **Docker:** build the API and dashboard images
6. **End-to-end:** start the full stack with `docker compose` and a throwaway key, then run
   the Playwright test that clicks through the whole optimization loop in the dashboard

CI uses only the mock provider, so it needs no secrets and spends nothing.

Run the end-to-end test against a local stack:

```bash
cd frontend
E2E_API_KEY=<key> E2E_BASE_URL=http://localhost:3000 PW_CHANNEL=chrome npm run e2e
# PW_CHANNEL=chrome uses an installed Chrome; omit it after `npx playwright install chromium`
```

## Migrations

```bash
uv run alembic upgrade head          # apply
uv run alembic revision --autogenerate -m "..."   # then review: name constraints, add
                                                  # server defaults for new NOT NULL columns
uv run alembic check                 # models and schema agree
```

The API container runs `alembic upgrade head` and the idempotent price seed on start.
