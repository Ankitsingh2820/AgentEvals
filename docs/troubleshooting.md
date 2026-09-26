# Troubleshooting

Problems people actually hit, and the fix. For anything else, `docker compose logs -f api
worker` shows what the backend is doing; every error response includes a `request_id`
that appears in those logs.

## Docker

**`failed to connect to the docker API` / `cannot find the file specified`**
Docker Desktop isn't running. Start it, wait until it says "running", and retry.

**`ports are not available` / `port is already allocated`**
Another program uses 3000, 8000 or 5433. Pick free ports in `.env` and restart:

```ini
AGENTEVAL_WEB_PORT=3080
AGENTEVAL_API_PORT=8080
AGENTEVAL_DB_PORT=5434
```

**A service keeps restarting**
`docker compose ps` shows which one; `docker compose logs <service>` shows why. After
changing code or dependencies, rebuild with `docker compose up -d --build`.

## Signing in

**The dashboard says "That key was not accepted"**
Create a fresh key with `docker compose exec api python -m app.auth create "me"` and use
the `ae_...` value it prints. `python -m app.auth list` shows which keys exist and whether
they're active.

**`{"detail": "missing or invalid API key"}` in the API docs page**
The request was sent without a key. Click **Authorize** (top right of
http://localhost:8000/docs), paste the key into **HTTPBearer** (just `ae_...`, without
"Bearer"), click **Authorize**, then **Close**, and send the request again.

**`rate limit exceeded`**
Too many requests per minute for one key. Wait for the `Retry-After` seconds, or raise the
limits in `.env` (`AGENTEVAL_RATE_LIMIT_PER_MINUTE`, `AGENTEVAL_EXECUTE_RATE_LIMIT_PER_MINUTE`).

## AI providers

**`OPENAI_API_KEY is not set` (or `GROQ_API_KEY`, `ANTHROPIC_API_KEY`)**
The key isn't reaching the containers. Check in `.env` that:

- the name is **exactly** `OPENAI_API_KEY`, `GROQ_API_KEY` or `ANTHROPIC_API_KEY` (a key
  saved under another name, such as `OPENAI_SECRET`, is ignored),
- there are no quotes or spaces around the value,
- you restarted afterwards with `docker compose up -d`.

Then confirm with **GET /providers/{name}/models**, which costs nothing.

**`401` / `invalid api key` from the provider**
The key itself is wrong or revoked. Create a new one in the provider's console.

**`prompt caching is automatic for provider 'groq'`**
OpenAI and Groq cache long prompts on their own; remove `options.prompt_caching` from that
agent. The option only applies to Anthropic.

**The run shows `retry_count` above 0**
The provider returned a temporary error (rate limit, overload) and AgentEval retried it.
Each attempt is visible in the run's timeline.

## Results

**Cost shows `unpriced` or `partial`**
The model has no entry in the price list. Add one with **POST /pricing/models** (see the
[user guide](user-guide.md#9-costs-and-prices)).

**An experiment says INCONCLUSIVE**
The criteria were met on average, but the difference isn't statistically clear, or there
were too few runs. Its page lists the reason. Increase `repetitions`, use a larger dataset,
or loosen `require_significance` if averages are enough for your decision.

**An evaluation or experiment stays "running"**
Check the worker: `docker compose logs -f worker`. If the worker was stopped mid-run, a new
worker picks the work up again automatically after about the lease time
(`AGENTEVAL_TASK_LEASE_SECONDS`, default 10 minutes).

**The judge's scores are all 4 and say "MOCK JUDGE"**
That evaluator uses the `mock` provider, which returns placeholder scores. Create an
`llm_judge` evaluator with a real provider.

## Starting over

```bash
docker compose down -v          # deletes all data
docker compose up -d --build
docker compose exec api python -m app.auth create "me"
docker compose exec api python -m app.demo
```
