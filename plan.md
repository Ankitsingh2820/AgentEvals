# AgentEval — AI Agent Evaluation & Optimization Platform

## 1. Project Overview

Build a production-oriented platform for evaluating and optimizing LLM-powered AI agents.

The system should allow a developer to run an AI agent against a task/dataset and measure:

- Output quality
- Correctness
- Relevance
- Groundedness
- Hallucination
- Tool-call correctness
- Token usage
- LLM cost
- End-to-end latency
- Individual component latency
- Number of LLM calls
- Number of tool calls
- Failure/retry counts

The platform should then allow different configurations of the same agent to be compared and identify whether an optimization reduces cost and/or latency without materially degrading output quality.

### Core principle

> Optimize AI agents based on measurable quality, cost, and latency — not assumptions.

---

# 2. Primary Goal

The final system should answer:

> "Can I produce the same or nearly the same quality of output from this AI agent while using less money and less time?"

For every experiment, the system should make the trade-off visible.

Example:

```text
                    Baseline       Optimized

Quality                92%             91%
Cost                  $0.076          $0.029
Latency               8.7 sec         4.3 sec
LLM Calls                5               3
Tool Calls               6               4

Cost Reduction:       61.8%
Latency Reduction:    50.6%
Quality Change:        -1.0%
```

The platform should never claim an optimization is successful merely because it is cheaper or faster.

An optimization is successful only when:

1. Quality remains above the configured threshold.
2. Cost improves or remains acceptable.
3. Latency improves or remains acceptable.
4. The experiment is reproducible.

---

# 3. Target Users

Primary user:

- AI Engineer
- ML Engineer
- LLM Engineer
- Developer building AI agents

Typical workflow:

```text
Build Agent
    ↓
Run Agent
    ↓
Capture Trace
    ↓
Evaluate Output
    ↓
Measure Cost + Latency
    ↓
Create Optimization
    ↓
Run Experiment
    ↓
Compare Results
    ↓
Promote Better Configuration
```

---

# 4. Core Features

## 4.1 Agent Configuration

Users should be able to define an agent configuration.

Configuration should include:

- Agent name
- Description
- Provider
- Model
- System prompt
- Temperature
- Maximum tokens
- Available tools
- RAG configuration
- Evaluation configuration
- Metadata/tags

Example:

```json
{
  "name": "Research Agent",
  "provider": "openai",
  "model": "gpt-model",
  "temperature": 0,
  "tools": [
    "web_search",
    "company_lookup"
  ]
}
```

Do not hardcode one specific provider into the architecture.

The system should support multiple providers through an abstraction layer.

---

# 5. Agent Execution Engine

Build an execution layer responsible for running an agent.

The execution engine should capture a complete trace.

Example:

```text
Agent Run
│
├── LLM Call
│   ├── model
│   ├── input tokens
│   ├── output tokens
│   ├── latency
│   └── cost
│
├── Tool Call
│   ├── tool
│   ├── input
│   ├── output
│   └── latency
│
├── LLM Call
│
├── Retrieval
│   ├── query
│   ├── documents
│   └── latency
│
└── Final Response
```

Every run must have a unique `run_id`.

---

# 6. Observability / Tracing

Create a trace system that records:

### Run-level metrics

- run_id
- agent_id
- experiment_id
- start_time
- end_time
- total_latency
- total_cost
- final_output
- success/failure
- error

### LLM-level metrics

- provider
- model
- request
- response
- input_tokens
- output_tokens
- total_tokens
- latency
- cost

### Tool-level metrics

- tool name
- arguments
- result
- latency
- success/failure

### Retrieval-level metrics

- query
- number of documents retrieved
- retrieval latency
- reranking latency
- retrieved document IDs

---

# 7. Cost Calculation Engine

Build a provider-independent cost calculation service.

The system should calculate:

```text
Input Cost
+
Output Cost
+
Tool Cost
+
Other Model Costs
=
Total Run Cost
```

For LLMs:

```text
input_cost =
input_tokens / 1,000,000 × input_price_per_1M_tokens

output_cost =
output_tokens / 1,000,000 × output_price_per_1M_tokens
```

Do not hardcode pricing throughout the application.

Create a centralized model pricing registry.

Example conceptual structure:

```text
ModelPricing
├── provider
├── model
├── input_price
├── output_price
├── effective_date
└── currency
```

The architecture should allow pricing to be updated without modifying execution logic.

Important:

- Store the pricing used for each run.
- Historical runs must not change when pricing is updated later.
- Support different pricing for different models.
- Clearly distinguish estimated cost from actual provider-billed cost.

---

# 8. Latency Measurement

Measure latency at multiple levels.

### Total latency

```text
end_time - start_time
```

### LLM latency

Measure each model request independently.

### Tool latency

Measure each tool execution.

### Retrieval latency

Measure:

- embedding generation
- vector search
- reranking
- database lookup

### Queue latency

If asynchronous execution is used, distinguish:

```text
Queue Time
Execution Time
Total Time
```

Do not only display total latency.

The platform should help answer:

> "Where is the agent spending its time?"

---

# 9. Output Evaluation

The evaluation engine is the core of the project.

Support multiple evaluation strategies.

## 9.1 Deterministic Evaluators

Where possible, use deterministic checks.

Examples:

- JSON schema validity
- Required fields
- Exact match
- Regex
- Citation presence
- Tool-call validity
- Number of expected items

---

## 9.2 LLM-as-a-Judge

Create configurable evaluators that score outputs using another LLM.

Possible dimensions:

### Correctness

Does the answer match the reference answer?

### Relevance

Does the answer directly address the task?

### Groundedness

Is the answer supported by retrieved/contextual information?

### Completeness

Does the response contain the required information?

### Instruction Following

Did the agent follow the required instructions?

The evaluator should return structured output such as:

```json
{
  "score": 0.91,
  "reason": "...",
  "passed": true
}
```

Do not treat LLM-as-a-judge as absolute truth.

Store:

- evaluator model
- evaluator prompt/version
- score
- reasoning
- timestamp

This makes evaluations auditable.

---

# 10. Evaluation Dataset

Create a dataset abstraction.

A dataset contains test cases.

Example:

```json
{
  "id": "case_001",
  "input": "Research this company...",
  "expected_output": "...",
  "metadata": {
    "category": "company_research"
  }
}
```

The platform should support:

- input-only datasets
- input + expected output datasets
- metadata
- evaluation labels

Start with JSON/JSONL support.

Later support CSV/database sources.

---

# 11. Experiment System

This is one of the most important components.

Users should be able to compare:

```text
Experiment
├── Baseline configuration
└── Candidate configuration
```

Example:

```text
Experiment: Reduce Research Agent Cost

Baseline:
GPT-X
5 tool calls
No caching

Candidate:
Smaller model
3 tool calls
Prompt caching
```

Both configurations should run against the same evaluation dataset.

This is critical.

Never compare two configurations using different test cases.

---

# 12. Experiment Metrics

For every experiment calculate:

### Quality

- Average score
- Pass rate
- Correctness
- Relevance
- Groundedness
- Completeness

### Cost

- Total cost
- Average cost/run
- Cost reduction %
- Cost distribution

### Latency

- Average latency
- Median latency
- P95 latency
- P99 latency
- Latency reduction %

### Reliability

- Success rate
- Error rate
- Retry rate
- Tool failure rate

---

# 13. Statistical Comparison

Do not rely only on averages.

For latency calculate:

```text
Mean
Median
P95
P99
```

For quality calculate:

```text
Mean score
Median score
Pass rate
Score distribution
```

For cost calculate:

```text
Total cost
Average cost/run
Minimum
Maximum
```

Where appropriate, report confidence intervals or statistical significance.

The system should avoid claiming a meaningful improvement from tiny sample sizes.

---

# 14. Optimization Engine

Create an optimization layer that can suggest potential improvements.

Initial optimization strategies:

## Model Routing

Use cheaper models for simpler tasks.

Example:

```text
Simple classification → smaller model

Complex reasoning → stronger model
```

---

## Prompt Optimization

Compare:

```text
Prompt A
vs
Prompt B
```

while keeping:

- model
- dataset
- temperature
- tools

constant.

---

## Tool Optimization

Detect unnecessary tool calls.

Example:

```text
Baseline:
Search → Search → Search → Answer

Optimized:
Search → Answer
```

---

## Parallel Tool Execution

Where tool calls are independent:

```text
Sequential:

Tool A → Tool B → Tool C

Parallel:

Tool A ─┐
Tool B ─┼→ Result
Tool C ─┘
```

Measure the latency difference.

---

## Prompt / Context Caching

Measure whether repeated context can be cached.

Compare:

```text
Without caching
vs
With caching
```

Measure:

- token savings
- cost reduction
- latency change
- quality change

---

## Retrieval Optimization

Compare:

- chunk sizes
- top-k
- reranking
- embedding models

Evaluate both retrieval quality and final answer quality.

---

# 15. Optimization Recommendation

The platform should eventually generate recommendations.

Example:

```text
Optimization Recommendation

Current configuration:

Cost: $0.072/run
Latency: 7.8 sec
Quality: 92%

Detected opportunities:

1. 3 sequential tool calls can execute in parallel.
   Estimated latency reduction: 25–40%

2. 42% of requests use the large model for simple tasks.
   Consider model routing.

3. Repeated system context detected.
   Consider prompt caching.

4. Average retrieved documents: 12
   Top-5 retrieval produces similar quality in testing.
```

Recommendations must be based on observed data.

Do not invent optimization percentages without evidence.

---

# 16. Dashboard

Build a clean engineering-focused dashboard.

## Dashboard

Display:

```text
Total Agent Runs
Average Cost
Average Latency
Average Quality
Success Rate
```

Include trend charts:

- Cost over time
- Latency over time
- Quality over time

---

## Agent Details

Show:

- Agent configuration
- Recent runs
- Average cost
- Average latency
- Quality scores

---

## Run Details

Show the complete trace:

```text
Run #123

Total Cost: $0.041
Total Latency: 4.8s
Quality: 91%

Timeline

00.0s ─ Agent Start
00.2s ─ LLM Call
01.1s ─ Tool Call
02.7s ─ Retrieval
03.4s ─ LLM Call
04.8s ─ Final Response
```

Allow users to inspect each step.

---

## Experiment Comparison

Display baseline vs candidate.

Example:

```text
Metric             Baseline      Candidate      Change

Quality              92%            91%          -1.0%
Cost                $0.076         $0.029        -61.8%
Latency              8.7s           4.3s          -50.6%
Success Rate          98%            99%          +1.0%
```

Also show whether the candidate passes configured acceptance criteria.

---

# 17. Acceptance Criteria

Allow users to configure constraints.

Example:

```text
Minimum quality: 90%
Maximum quality degradation: 2%
Minimum cost reduction: 20%
Minimum latency reduction: 15%
Maximum error rate: 3%
```

An optimization is marked:

```text
PASS
```

only if it satisfies the configured constraints.

Otherwise:

```text
FAIL
```

Example:

```text
Candidate

Cost: -52%       ✓
Latency: -37%    ✓
Quality: -1.2%   ✓
Error rate: +4%  ✗

Result: FAIL
Reason: Error rate exceeded threshold.
```

This is important because optimization must be multi-objective.

---

# 18. Architecture

Recommended initial architecture:

```text
                    ┌─────────────────────┐
                    │      Frontend       │
                    │       React         │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │     FastAPI API     │
                    └──────────┬──────────┘
                               │
             ┌─────────────────┼─────────────────┐
             ▼                 ▼                 ▼
       Agent Engine      Evaluation Engine   Experiment Engine
             │                 │                 │
             ▼                 ▼                 ▼
        LLM Providers      Evaluators       Optimizer
             │
             ▼
       Trace Collector
             │
       ┌─────┴──────┐
       ▼            ▼
   PostgreSQL     Redis
       │
       ▼
   Analytics
```

---

# 19. Recommended Technology Stack

## Backend

- Python
- FastAPI
- Pydantic
- SQLAlchemy
- PostgreSQL

## Agent Layer

- LangGraph
- LangChain where useful

Do not introduce frameworks simply for the sake of using them.

---

## Evaluation

- DeepEval
- Custom deterministic evaluators
- LLM-as-a-judge

The architecture should allow additional evaluation frameworks later.

---

## Observability

Use:

- OpenTelemetry where appropriate
- Structured logging
- Trace IDs
- Metrics

---

## Async Processing

Use:

- Celery
- Redis

for long-running evaluation experiments.

---

## Frontend

- React
- TypeScript
- Tailwind CSS
- Recharts or equivalent charting library

---

## Deployment

Use:

- Docker
- Docker Compose for local development

Structure the application so that it can later be deployed to AWS or another cloud platform.

---

# 20. Database Design

Create models for at least:

```text
users
agents
agent_versions
datasets
dataset_cases
runs
run_steps
llm_calls
tool_calls
evaluations
evaluation_results
experiments
experiment_runs
model_pricing
optimization_recommendations
```

Maintain relationships between these entities.

Every run must be traceable back to:

```text
Agent
→ Agent Version
→ Experiment
→ Dataset
→ Run
→ Steps
→ Evaluations
```

---

# 21. Versioning

Version:

- Agent configurations
- Prompts
- Evaluation criteria
- Evaluator prompts
- Model configuration
- Pricing information

An experiment must always record exactly which versions were used.

This ensures reproducibility.

---

# 22. API Design

Initial API groups:

```text
/agents
/datasets
/runs
/evaluations
/experiments
/metrics
/pricing
/optimizations
```

Examples:

```text
POST /agents
GET /agents
GET /agents/{id}

POST /datasets
GET /datasets

POST /runs
GET /runs/{id}
GET /runs/{id}/trace

POST /evaluations
GET /evaluations/{id}

POST /experiments
GET /experiments/{id}
POST /experiments/{id}/run

GET /metrics/agents/{id}
GET /optimizations/{id}
```

Keep the API modular.

---

# 23. Security

Implement:

- API authentication
- Input validation
- Rate limiting
- Secret management
- No API keys stored in plaintext
- Tenant isolation if multi-user support is implemented
- Logging without leaking secrets or sensitive prompts

---

# 24. Testing Strategy

Testing must be treated as a first-class part of the project.

## Unit tests

Test:

- Cost calculation
- Token calculations
- Latency calculations
- Evaluation scoring
- Pricing lookup
- Acceptance criteria
- Percentage calculations

---

## Integration tests

Test:

```text
API
→ Agent
→ LLM
→ Trace
→ Evaluation
→ Database
```

Use mocked LLM providers for deterministic tests.

---

## Evaluation tests

Create a small fixed evaluation dataset.

The dataset should be used as a regression suite.

Every significant agent change should be evaluated against it.

---

# 25. CI/CD

Set up CI to run:

```text
Lint
↓
Unit Tests
↓
Integration Tests
↓
Evaluation Tests
↓
Build Docker Image
```

The project should demonstrate that AI systems can be tested like software systems.

---

# 26. Important Engineering Principles

Follow these principles throughout development:

### 1. Reproducibility

Every experiment must be reproducible.

### 2. Observability

Every agent execution should be traceable.

### 3. Provider independence

Do not tightly couple the system to one LLM provider.

### 4. Evaluation before optimization

Never optimize blindly.

### 5. Quality before cost

Do not sacrifice significant quality simply to reduce cost.

### 6. Measure before claiming

Every optimization claim must come from recorded experiment data.

### 7. Separate concerns

Keep:

```text
Agent execution
Evaluation
Tracing
Cost calculation
Experimentation
Optimization
```

as separate components.

---

# 27. MVP Scope

Do NOT attempt to build everything at once.

### Phase 1 — Foundation

Build:

- FastAPI backend
- PostgreSQL
- Agent configuration
- One LLM provider
- Agent execution
- Basic tracing

---

### Phase 2 — Metrics

Add:

- Token tracking
- Cost calculation
- Latency measurement
- LLM call tracking
- Tool call tracking

---

### Phase 3 — Evaluation

Add:

- Evaluation datasets
- Deterministic evaluators
- LLM-as-a-judge
- Quality scores

---

### Phase 4 — Experiments

Add:

- Baseline configuration
- Candidate configuration
- Dataset-based comparison
- Cost comparison
- Latency comparison
- Quality comparison

---

### Phase 5 — Optimization

Add:

- Model routing
- Prompt comparison
- Tool optimization
- Parallel tool execution
- Caching experiments

---

### Phase 6 — Dashboard

Add:

- Agent dashboard
- Run trace
- Metrics
- Experiment comparison
- Optimization recommendations

---

### Phase 7 — Production Hardening

Add:

- Authentication
- Rate limiting
- Docker
- CI/CD
- Logging
- OpenTelemetry
- Error handling
- Documentation

---

# 28. Example Demo

The final project should have a strong demonstration.

Create an example agent:

> **Company Research Agent**

Input:

```text
Research Acme Corp and determine:
1. What the company does
2. Industry
3. Approximate company size
4. Whether it matches our ICP
5. Reason for the decision
```

### Baseline

```text
Model: Large model
Tool calls: 6
Latency: 9.2 sec
Cost: $0.081
Quality: 93%
```

### Optimization

Apply:

- Smaller model for classification
- Parallel independent tool calls
- Prompt caching
- Reduced unnecessary retrieval
- Better tool routing

### Optimized

```text
Model: Mixed routing
Tool calls: 4
Latency: 4.8 sec
Cost: $0.031
Quality: 92%
```

Then let the dashboard show:

```text
Cost:       -61.7%
Latency:    -47.8%
Quality:     -1.0%

Optimization Status: PASS
```

This should be the project's primary demo.

---

# 29. Resume Goal

The project should ultimately allow a resume bullet similar to:

> Built an AI agent evaluation and optimization platform that traces LLM/tool calls and measures quality, cost, and latency across experiments; implemented model routing, caching and parallel tool execution to reduce inference cost and response latency while maintaining evaluation quality.

Do NOT invent metrics.

Only include percentages after they are actually measured from experiments.

---

# 30. Interview Goal

The project should be designed so that an interviewer can ask:

### Architecture

- How does your tracing system work?
- Why did you use FastAPI?
- Why PostgreSQL?
- How did you model agent runs?

### LLM

- How do you calculate token cost?
- How do you handle different model pricing?
- How do you compare models?

### Evaluation

- How does LLM-as-a-judge work?
- How do you reduce evaluator bias?
- How do you evaluate hallucination?
- How do you measure groundedness?

### Agents

- How do you trace tool calls?
- How do you detect unnecessary tool calls?
- How do you optimize agent workflows?

### Performance

- How do you measure latency?
- Why P95 instead of only average latency?
- How does parallel execution reduce latency?

### Optimization

- How do you decide whether an optimization is safe?
- How do you trade off quality vs cost?
- How do you prevent cheaper models from degrading output quality?

### Production

- How do you handle retries?
- How do you handle provider failures?
- How do you manage API keys?
- How do you test nondeterministic AI systems?

The implementation should be strong enough that you can answer these questions from actual experience with the project.

---

# 31. Development Rules for Claude Code

1. Do not build the entire project in one step.
2. Follow the phases sequentially.
3. Before implementing a phase, inspect the existing repository.
4. Do not rewrite working components unnecessarily.
5. Keep architecture modular.
6. Write tests alongside important functionality.
7. Do not hardcode API keys.
8. Use environment variables for secrets.
9. Do not hardcode model pricing throughout the codebase.
10. Add proper logging and error handling.
11. Keep database migrations version-controlled.
12. Document important architectural decisions.
13. Do not add unnecessary dependencies.
14. Prefer simple implementations before introducing abstractions.
15. Do not claim performance improvements without running experiments.
16. Record experiment results so they can be reproduced.
17. Every major feature should have tests.
18. Keep the project runnable after every phase.

---

# 32. Definition of Done

The project is considered complete when a developer can:

1. Create an agent.
2. Configure its model and tools.
3. Create an evaluation dataset.
4. Run the agent against the dataset.
5. Capture the complete execution trace.
6. Calculate token usage.
7. Calculate estimated LLM cost.
8. Measure total and component latency.
9. Evaluate output quality.
10. Create a baseline.
11. Create an optimized configuration.
12. Run both configurations against the same dataset.
13. Compare quality, cost, latency and reliability.
14. Determine whether the optimization passes configured thresholds.
15. View all results through a dashboard.
16. Reproduce the experiment later.
17. Run automated tests and CI successfully.
18. Run the complete system locally through Docker.

---

# 33. Final Product Positioning

The project should be positioned as:

> **An evaluation and optimization platform for production AI agents.**

Not:

> "A dashboard for tracking LLM costs."

The central engineering problem is:

```text
How do we make AI agents
        ↓
CHEAPER
        +
FASTER
        +
WITHOUT SIGNIFICANTLY
REDUCING QUALITY?
```

That is the problem the entire architecture should revolve around.