// Typed client for the AgentEval API. Money arrives as decimal strings (exact); the
// dashboard parses them only for display.

export type Money = string | null

export interface AgentVersion {
  id: string
  version: number
  provider: string
  model: string
  system_prompt: string
  temperature: number | null
  max_tokens: number
  tools: string[]
  options: Record<string, unknown>
  metadata: Record<string, unknown>
  created_at: string
}

export interface Agent {
  id: string
  name: string
  description: string | null
  tags: string[]
  created_at: string
  latest_version: AgentVersion
}

export interface Run {
  id: string
  agent_id: string
  agent_version_id: string
  experiment_id: string | null
  evaluation_id: string | null
  repetition: number
  status: 'running' | 'succeeded' | 'failed'
  input: string
  final_output: string | null
  error: string | null
  started_at: string
  total_latency_ms: number | null
  llm_call_count: number
  tool_call_count: number
  tool_failure_count: number
  tool_calls_skipped: number
  route: string | null
  routed_model: string | null
  input_tokens: number
  output_tokens: number
  cache_creation_input_tokens: number
  cache_read_input_tokens: number
  llm_latency_ms: number
  tool_latency_ms: number
  estimated_total_cost: Money
  currency: string | null
  cost_status: string | null
}

export interface LLMCall {
  provider: string
  model: string
  purpose: string
  response_model: string | null
  stop_reason: string | null
  input_tokens: number
  output_tokens: number
  cache_creation_input_tokens: number
  cache_read_input_tokens: number
  total_tokens: number
  pricing_snapshot: Record<string, unknown> | null
  input_cost: Money
  output_cost: Money
  cache_write_cost: Money
  cache_read_cost: Money
  estimated_cost: Money
  request: Record<string, unknown>
  response: Record<string, unknown> | null
}

export interface ToolCall {
  tool_name: string
  arguments: Record<string, unknown>
  result: string | null
  success: boolean
  skip_reason: string | null
  estimated_cost: Money
}

export interface Step {
  sequence: number
  turn: number
  type: 'llm_call' | 'tool_call'
  status: string
  start_offset_ms: number
  latency_ms: number
  error: string | null
  llm_call: LLMCall | null
  tool_call: ToolCall | null
}

export interface Trace {
  run: Run
  agent_version: AgentVersion
  latency: { total_ms: number | null; llm_ms: number; tool_ms: number; other_ms: number | null }
  steps: Step[]
}

export interface Summary {
  count: number
  mean: number | null
  median: number | null
  p95: number | null
  p99: number | null
  min: number | null
  max: number | null
}

export interface AgentMetrics {
  runs: number
  succeeded: number
  success_rate: number | null
  latency_ms: Summary
  estimated_cost: Summary
  estimated_total_spend: string
  runs_without_cost: number
  llm_calls: Summary
  tool_calls: Summary
  tool_failure_rate: number | null
}

export interface Kpis {
  runs: number
  success_rate: number | null
  avg_cost: number | null
  runs_with_cost: number
  avg_latency_ms: number | null
  p50_latency_ms: number | null
  p95_latency_ms: number | null
  avg_quality: number | null
  runs_with_quality: number
}

export interface TrendPoint {
  date: string
  runs: number
  success_rate: number | null
  avg_cost: number | null
  p50_latency_ms: number | null
  p95_latency_ms: number | null
  avg_quality: number | null
}

export interface Overview {
  days: number
  kpis: Kpis
  previous: Kpis
  trend: TrendPoint[]
}

export interface EvaluatorSummary {
  name: string
  version: number
  type: string
  scored: number
  skipped: number
  errors: number
  score: Summary
  pass_rate: number | null
  score_distribution: Record<string, number>
}

export interface EvaluationSummary {
  cases: number
  runs: number
  success_rate: number | null
  quality: {
    runs_scored: number
    runs_unscored: number
    score: Summary
    pass_rate: number | null
    score_distribution: Record<string, number>
  }
  evaluators: Record<string, EvaluatorSummary>
  latency_ms: Summary
  estimated_total_cost: string | null
  runs_without_cost: number
  judge_calls: number
  judge_estimated_cost: string | null
}

export interface Evaluation {
  id: string
  agent_id: string
  agent_version_id: string
  dataset_id: string
  evaluator_ids: string[]
  repetitions: number
  experiment_id: string | null
  arm: string | null
  status: string
  error: string | null
  summary: EvaluationSummary | null
  created_at: string
}

export interface EvaluationResult {
  id: string
  run_id: string
  case_key: string
  evaluator_name: string
  status: 'ok' | 'skipped' | 'error'
  score: number | null
  passed: boolean | null
  reason: string | null
  judge_model: string | null
  judge_prompt_version: string | null
}

export interface Check {
  name: string
  rule: string
  observed: number | null
  status: 'PASS' | 'FAIL' | 'INCONCLUSIVE'
  reason: string
}

export interface Comparison {
  pairs: number
  verdict: 'PASS' | 'FAIL' | 'INCONCLUSIVE'
  reasons: string[]
  checks: Check[]
  metrics: {
    quality: { n: number; baseline: number | null; candidate: number | null; change_points: number | null; change_points_ci: number[] | null }
    pass_rate: { n: number; baseline: number | null; candidate: number | null; change_points: number | null }
    cost_per_run: { n: number; baseline: number | null; candidate: number | null; change_pct: number | null; change_pct_ci: number[] | null }
    latency_ms: {
      n: number
      baseline: Record<'mean' | 'median' | 'p95' | 'p99', number | null>
      candidate: Record<'mean' | 'median' | 'p95' | 'p99', number | null>
      change_pct: Record<'mean' | 'median' | 'p95' | 'p99', number | null>
      mean_change_pct_ci: number[] | null
    }
    reliability: Record<string, number | null>
    llm_calls_per_run: { baseline: number | null; candidate: number | null; change_pct: number | null }
    tool_calls_per_run: { baseline: number | null; candidate: number | null; change_pct: number | null }
    tokens_per_run: { baseline: number | null; candidate: number | null; change_pct: number | null }
  }
}

export interface Experiment {
  id: string
  name: string
  description: string | null
  dataset_id: string
  baseline_agent_version_id: string
  candidate_agent_version_id: string
  evaluator_ids: string[]
  repetitions: number
  acceptance_criteria: Record<string, unknown>
  config_snapshot: {
    baseline: Record<string, unknown>
    candidate: Record<string, unknown>
    config_diff?: string[]
    dataset: { name: string; version: number; cases: number }
    evaluators: { id: string; name: string; version: number; type: string }[]
  }
  reproduction_of: string | null
  status: string
  error: string | null
  baseline_evaluation_id: string | null
  candidate_evaluation_id: string | null
  verdict: 'PASS' | 'FAIL' | 'INCONCLUSIVE' | null
  comparison: Comparison | null
  created_at: string
}

export interface ExperimentSide {
  run_id: string
  status: string
  quality: number | null
  passed: boolean | null
  estimated_cost: Money
  latency_ms: number | null
  tool_calls: number
}

export interface ExperimentCase {
  case_key: string | null
  repetition: number
  baseline: ExperimentSide | null
  candidate: ExperimentSide | null
  quality_delta: number | null
}

export interface Recommendation {
  type: string
  title: string
  evidence: Record<string, unknown>
  estimated_impact: Record<string, unknown>
  basis: string
  caveats: string[]
  suggested_options: Record<string, unknown>
  confidence: 'low' | 'normal'
  next_step: string
}

export interface OptimizationReport {
  id: string
  agent_id: string
  agent_version_id: string | null
  runs_analyzed: number
  recommendations: Recommendation[]
  created_at: string
}

export interface Dataset {
  id: string
  name: string
  version: number
  case_count: number
}

export interface Evaluator {
  id: string
  name: string
  version: number
  type: string
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// The API key lives in this browser's localStorage (per-viewer convenience). Storage can
// be unavailable (private mode, blocked site data), so every access is guarded.
const KEY_STORAGE = 'agenteval-api-key'
export const UNAUTHORIZED_EVENT = 'agenteval:unauthorized'

export function getApiKey(): string | null {
  try {
    return localStorage.getItem(KEY_STORAGE)
  } catch {
    return null
  }
}

export function setApiKey(key: string | null): void {
  try {
    if (key) localStorage.setItem(KEY_STORAGE, key)
    else localStorage.removeItem(KEY_STORAGE)
  } catch {
    /* storage unavailable: the key only lasts for this page load */
  }
}

async function request<T>(method: string, path: string, body?: unknown, key = getApiKey()): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['content-type'] = 'application/json'
  if (key) headers.authorization = `Bearer ${key}`
  const res = await fetch(`/api${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const data = await res.json()
      detail = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    } catch {
      /* non-JSON error body */
    }
    if (res.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT))
    if (res.status === 429) detail = `${detail} (retry in ${res.headers.get('retry-after') ?? '?'}s)`
    throw new ApiError(res.status, detail)
  }
  return res.json() as Promise<T>
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body ?? {}),
  /** Validate a key without storing it. */
  whoami: (key: string | null) => request<{ id: string; name: string }>('GET', '/auth/whoami', undefined, key),
}
