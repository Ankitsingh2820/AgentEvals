# Getting started

This guide takes you from nothing to a running AgentEval with demo data, in about 10
minutes. You don't need any AI API keys for the demo.

## 1. What you need

| Tool | Why | Get it |
|---|---|---|
| **Docker Desktop** (or Docker Engine with Compose) | Runs every part of AgentEval in containers | [docker.com/get-started](https://www.docker.com/get-started/) |
| **Git** | To download the code | [git-scm.com](https://git-scm.com/downloads) |
| ~4 GB free memory, ~3 GB disk | For the containers and images | |

Start Docker Desktop and wait until it says it is running. Everything below works the same
in PowerShell (Windows), Terminal (macOS) and any Linux shell.

## 2. Download and configure

```bash
git clone https://github.com/Ankitsingh2820/AgentEvals.git agenteval
cd agenteval
cp .env.example .env
```

`.env` holds your settings and secrets. It is ignored by git, so it never gets committed.
You can leave it as it is for the demo. To use real AI models later, fill in the keys you
have:

```ini
OPENAI_API_KEY=sk-...
GROQ_API_KEY=gsk_...
ANTHROPIC_API_KEY=sk-ant-...
```

Use exactly these variable names; AgentEval won't find a key stored under any other name.

## 3. Start AgentEval

```bash
docker compose up -d --build
```

The first start builds the images and takes a few minutes. It starts five services:

| Service | What it does |
|---|---|
| `db` | PostgreSQL: stores agents, runs, results |
| `redis` | Queue for background work, shared rate limits |
| `api` | The backend (FastAPI). Applies database migrations and loads model prices on start |
| `worker` | Runs evaluations and experiments in the background (Celery) |
| `web` | The dashboard (React), served by nginx |

Check they are up with `docker compose ps`: all five should say `Up`.

## 4. Create your sign-in key

Every page and API call needs a key. Create one:

```bash
docker compose exec api python -m app.auth create "me"
```

It prints something like:

```text
id:  9d29819a-...
key: ae_73rkPT1...
Store it now; it cannot be shown again.
```

Copy the `ae_...` key somewhere safe. Only a hash of it is stored, so it can't be shown
again. If you lose it, create another.

## 5. Load the demo data

```bash
docker compose exec api python -m app.demo
```

This creates two example agents, runs them, and runs one evaluation and one experiment,
all on the built-in **mock** model (free, offline, no keys). Running it again does nothing.

## 6. Open the dashboard

Go to **http://localhost:3000**, paste your key and click **Sign in**.

A short tour:

| Page | What to look at |
|---|---|
| **Overview** | Run counts, cost, latency and quality, with trend charts. Any chart can switch to a table. |
| **Agents** → **Create agent** | Make your own agent: pick a provider and model, write the prompt, tick tools. Then use **Run this agent** on its page. |
| **Agents** → *Company Research Agent (demo)* | Its two versions, recent runs, and its evaluation. Click a run ID to see every step the agent took: model calls, tool calls, tokens, cost, timing. |
| **Agents** → *Long-prompt Agent (demo)* | Click **Analyze runs**: it recommends prompt caching, with the evidence. **Apply as candidate…** creates a new version and an experiment to test it. |
| **Experiments** → *Cheaper model for the research agent* | The verdict (PASS / FAIL / INCONCLUSIVE), the baseline vs candidate table with confidence intervals, what changed, and per-case results. |
| **Evaluations** | Quality score, pass rate, and every evaluator's verdict with its reason. |

The mock model's scores and costs are placeholders that show the pipeline working. To
evaluate a real model, continue with the [user guide](user-guide.md).

## 7. Stop, restart, reset

```bash
docker compose down        # stop (your data is kept)
docker compose up -d       # start again
docker compose down -v     # stop AND delete all data (fresh start)
docker compose logs -f api worker   # watch what the backend is doing
```

## Ports

The defaults are 3000 (dashboard), 8000 (API) and 5433 (database). If one is already in
use on your machine, change it in `.env`, then run `docker compose up -d` again:

```ini
AGENTEVAL_WEB_PORT=3080
AGENTEVAL_API_PORT=8080
AGENTEVAL_DB_PORT=5434
```

## Optional: tracing with Jaeger

```bash
# in .env
AGENTEVAL_OTEL_EXPORTER_ENDPOINT=http://jaeger:4318
# then
docker compose --profile observability up -d
```

Traces of every run (model calls, tool calls, tokens, cost) appear at
http://localhost:16686.

## Development without Docker

For working on the code: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 24+,
and a PostgreSQL database.

```bash
# backend (terminal 1)
cd backend
uv sync
export AGENTEVAL_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5432/agenteval
uv run alembic upgrade head
uv run python -m app.auth create "me"
uv run python -m app.demo
uv run uvicorn app.main:app --reload        # http://localhost:8000

# dashboard (terminal 2)
cd frontend
npm install
npm run dev                                  # http://localhost:5173
```

Without Docker, evaluations run inside the API process (`AGENTEVAL_TASK_BACKEND=inline`);
no Redis or worker needed. On Windows PowerShell use `$env:AGENTEVAL_DATABASE_URL="..."`
instead of `export`.

Next: [user guide](user-guide.md) · [troubleshooting](troubleshooting.md)
