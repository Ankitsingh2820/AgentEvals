# User guide

How to evaluate and optimize your own agents with AgentEval. Start with
[getting started](getting-started.md) if AgentEval isn't running yet.

## Concepts

| Term | Meaning |
|---|---|
| **Agent** | An AI assistant: a model, a system prompt and a set of tools. |
| **Version** | A frozen configuration of an agent. Changing anything creates a new version; runs always record the version they used. |
| **Run** | One execution of an agent on one input, with a full **trace**: every model call and tool call, with tokens, cost and timing. |
| **Dataset** | A fixed list of test cases (inputs, optionally expected answers). |
| **Evaluator** | A scoring rule: a deterministic check (e.g. "mentions the industry") or an **LLM judge** (a model scoring relevance, correctness, ...). |
| **Evaluation** | An agent version run on every case of a dataset, scored by evaluators. |
| **Experiment** | Two versions (baseline vs candidate) on the same dataset, compared on quality, cost, latency and reliability, with a **verdict**: PASS, FAIL or INCONCLUSIVE. |

## Where to do things

- **Dashboard** (http://localhost:3000): view everything; analyze an agent, apply a
  recommendation, run and reproduce experiments.
- **API docs** (http://localhost:8000/docs): create agents, datasets, evaluators,
  evaluations and experiments. Every request below can be sent from this page.

### Using the API docs page

1. Open http://localhost:8000/docs.
2. Click **Authorize** (top right). In the **HTTPBearer** box paste your key (just the
   `ae_...` value, without "Bearer") and click **Authorize**, then **Close**.
3. For any endpoint: click it, click **Try it out**, paste the JSON body, click
   **Execute**. The response appears below; copy IDs from it for later steps.

If a response says `missing or invalid API key`, repeat step 2.

The same requests work from the command line:

```bash
curl -X POST http://localhost:8000/agents \
  -H "Authorization: Bearer ae_your_key" -H "content-type: application/json" \
  -d '{ ...same JSON... }'
```

## 1. Check your AI provider key

Put your key in `.env` (see [getting started](getting-started.md#2-download-and-configure)),
restart with `docker compose up -d`, then call **GET /providers/{name}/models** with
`name` = `openai`, `groq` or `anthropic`. It lists the models your key can use, and it
costs nothing. An error here means the key is missing or wrong.

## 2. Create an agent

**POST /agents**

```json
{
  "name": "Research Agent (Groq)",
  "provider": "groq",
  "model": "openai/gpt-oss-20b",
  "system_prompt": "Use the company_lookup tool to research the company, then say what it does, its industry and size.",
  "tools": ["company_lookup"],
  "max_tokens": 2000
}
```

- `provider`: `groq`, `openai`, `anthropic`, or `mock` (free, offline, fake answers).
- `model`: any model from step 1.
- `tools`: built-in tools the agent may call. `GET /tools` lists them (`calculator`,
  `company_lookup`).

To change the agent later, send the full new configuration to
**POST /agents/{id}/versions**. The old version and its runs stay as they were.

## 3. Run it

**POST /runs** with `{"agent_id": "<id>", "input": "Acme Corp"}`

The response shows the answer (`final_output`), `status`, tokens, cost and latency. In the
dashboard, open **Agents** → your agent → the run to see the step-by-step timeline.

## 4. Create a dataset

**POST /datasets**

```json
{
  "name": "my-tests",
  "cases": [
    {"id": "acme", "input": "Acme Corp", "expected_output": "Industrial automation company",
     "labels": {"must_mention": ["Industrial Automation"]}},
    {"id": "globex", "input": "Globex Analytics"}
  ]
}
```

Or upload a JSON Lines file (one case per line) with **POST /datasets/jsonl?name=my-tests**.
An example ships in `backend/datasets/company_research.jsonl`. Posting the same name again
creates version 2; older versions never change.

## 5. Create evaluators

**POST /evaluators**. Some examples:

```json
{"name": "mentions-industry", "type": "contains", "config": {"labels_key": "must_mention"}}
```

```json
{"name": "relevance-gpt41mini", "type": "llm_judge",
 "config": {"criterion": "relevance", "provider": "openai", "model": "gpt-4.1-mini"}}
```

| Type | Checks |
|---|---|
| `exact_match` | The answer equals `expected_output` (ignoring case and spacing by default) |
| `contains` | Required words are present, listed inline (`values`) or per case (`labels_key`) |
| `regex` | A pattern must (or must not) appear, e.g. citations like `\[\d+\]` |
| `json_schema` | The answer is JSON matching a schema (required fields, item counts) |
| `tool_calls` | Required or forbidden tools, a maximum number of calls, all calls succeeded |
| `llm_judge` | A model scores one criterion from 1 to 5: `correctness`, `relevance`, `groundedness`, `completeness`, `instruction_following`, or `custom` with your own rubric |

Each verdict is `ok` (a score from 0 to 1), `skipped` (didn't apply, e.g. no expected
answer), or `error` (the evaluator itself failed; never counted as a zero). Use a judge at
least as capable as the agent it grades. The `mock` judge always answers 4.

## 6. Run an evaluation

**POST /evaluations**

```json
{"agent_id": "<agent id>", "dataset_id": "<dataset id>",
 "evaluator_ids": ["<evaluator id>", "<evaluator id>"]}
```

It runs in the background. Open **Evaluations** in the dashboard to see progress and
results: quality score, pass rate, agent cost vs judge cost, and every verdict with the
reason. Add `"repetitions": 3` to run each case several times (models vary).

## 7. Compare two versions (experiment)

Create a second version first (e.g. a different model), then **POST /experiments**:

```json
{
  "name": "Is the bigger model worth it?",
  "dataset_id": "<dataset id>",
  "baseline_agent_version_id": "<version 1 id>",
  "candidate_agent_version_id": "<version 2 id>",
  "evaluator_ids": ["<evaluator id>"],
  "repetitions": 2,
  "acceptance_criteria": {"max_quality_drop_points": 2, "min_cost_reduction_pct": 20,
                          "max_error_rate": 0.05}
}
```

Then **POST /experiments/{id}/run** (or the **Run experiment** button on its dashboard
page). Version IDs are in **GET /agents/{id}/versions**.

**Acceptance criteria** (all optional; set at least one):

| Criterion | Meaning |
|---|---|
| `min_quality` | Candidate's mean score at least this (0–1) |
| `max_quality_drop_points` | Quality may drop by at most this many points (92% → 91% is 1 point) |
| `min_pass_rate` | Share of cases where every check passed |
| `min_cost_reduction_pct` / `min_latency_reduction_pct` | Required saving, in % |
| `latency_stat` | Which latency to judge: `mean`, `median` or `p95` |
| `max_error_rate` | Share of runs allowed to fail |
| `min_pairs` | Minimum number of compared runs (default 10) |
| `require_significance` | Default `true`: savings must be statistically supported, not just better on average |

**The verdict:**

- **PASS:** every criterion is met, with enough evidence.
- **FAIL:** a criterion is missed.
- **INCONCLUSIVE:** criteria are met on average but the evidence isn't strong enough, or
  there are too few runs. Try more `repetitions` or a larger dataset.

Add `"isolate": ["system_prompt"]` to make sure only the prompt differs between the two
versions (the experiment is rejected otherwise). **POST /experiments/{id}/reproduce**
reruns the identical configuration as a new experiment.

## 8. Optimize

In the dashboard, open an agent and click **Analyze runs**. AgentEval studies its recorded
runs and recommends changes, each with the evidence and how any estimate was made:

- **Parallel tool calls:** several tools called one after another in one step.
- **Remove duplicate tool calls:** the same call repeated in a run.
- **Tool-call budget:** some runs use far more tool calls than usual.
- **Prompt caching** (Anthropic only; OpenAI and Groq cache automatically): long prompts
  sent repeatedly.
- **Model routing:** many simple requests on an expensive model.

**Apply as candidate…** creates a new version with the change, and optionally the
experiment that tests it. Nothing switches over automatically; the experiment decides.

The same options can be set directly on a version:

```json
"options": {
  "parallel_tool_calls": true,
  "dedupe_tool_calls": true,
  "max_tool_calls": 4,
  "prompt_caching": true,
  "routing": {"mode": "rules", "routes": [
    {"label": "short", "model": "openai/gpt-oss-20b", "max_input_chars": 200}]}
}
```

## 9. Costs and prices

Costs are **estimates** from a price list (USD per million tokens), not your provider's
bill. The list ships with prices for common Claude, OpenAI and Groq models, each with its
source and date (`backend/app/costs/seed_pricing.json`). Check them against the provider's
pricing page. A run on a model without a price shows its cost as `unpriced`.

To add or update a price, **POST /pricing/models**:

```json
{"provider": "groq", "model": "openai/gpt-oss-20b", "input_price_per_mtok": "0.075",
 "output_price_per_mtok": "0.30", "cache_read_price_per_mtok": "0.0375",
 "effective_date": "2026-10-01", "source": "console.groq.com/docs/models"}
```

Prices are never edited: a new entry with a later date takes over, and past runs keep the
price they were charged at.

## API reference

The full, interactive reference is http://localhost:8000/docs. Main endpoints:

| Method | Path | Purpose |
|---|---|---|
| POST / GET | `/agents`, `/agents/{id}` | Create / list / get agents |
| POST / GET | `/agents/{id}/versions` | New version / version history |
| POST / GET | `/runs`, `/runs/{id}`, `/runs/{id}/trace` | Run an agent / list / full trace |
| POST / GET | `/datasets`, `/datasets/jsonl?name=`, `/datasets/{id}` | Datasets |
| POST / GET | `/evaluators` | Evaluators |
| POST / GET | `/evaluations`, `/evaluations/{id}`, `/evaluations/{id}/results` | Evaluations and verdicts |
| POST / GET | `/experiments`, `/experiments/{id}` | Experiments with comparison and verdict |
| POST | `/experiments/{id}/run`, `/experiments/{id}/reproduce` | Run once / rerun identically |
| GET | `/experiments/{id}/cases` | Per-case comparison, regressions first |
| POST / GET | `/optimizations/analyze`, `/optimizations/{id}`, `/optimizations/{id}/apply` | Recommendations |
| POST / GET | `/pricing/models`, `/pricing/models/current`, `/pricing/tools` | Price list |
| GET | `/metrics/overview`, `/metrics/agents/{id}` | Aggregated metrics |
| GET | `/providers`, `/providers/{name}/models`, `/tools` | What's available |
| GET | `/auth/whoami`, `/health` | Key check / liveness (no key needed) |
