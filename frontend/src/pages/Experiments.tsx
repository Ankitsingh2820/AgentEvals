import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api, type Experiment, type ExperimentCase } from '../api'
import {
  A, Badge, Button, Card, ErrorBox, Json, Loading, Page, Table,
} from '../components/ui'
import { dateTime, money, ms, num, pct, shortId, signedPct, signedPoints } from '../format'
import { useApi } from '../useApi'

export function Experiments() {
  const { data, error } = useApi<Experiment[]>('/experiments')
  return (
    <Page title="Experiments" subtitle="Baseline vs candidate on the same dataset, judged against acceptance criteria.">
      {error && <ErrorBox message={error} />}
      {!data && !error && <Loading />}
      {data && (
        <Card>
          <Table
            rows={data}
            rowKey={(e) => e.id}
            empty="No experiments yet."
            columns={[
              { header: 'Experiment', cell: (e) => <A to={`/experiments/${e.id}`}>{e.name}</A> },
              { header: 'Verdict', cell: (e) => <Badge status={e.verdict ?? e.status} /> },
              { header: 'Changed', cell: (e) => e.config_snapshot.config_diff?.join(', ') || '—' },
              { header: 'Pairs', align: 'right', cell: (e) => num(e.comparison?.pairs) },
              { header: 'Cost', align: 'right', cell: (e) => signedPct(e.comparison?.metrics.cost_per_run.change_pct) },
              { header: 'Latency', align: 'right', cell: (e) => signedPct(e.comparison?.metrics.latency_ms.change_pct.mean) },
              { header: 'Quality', align: 'right', cell: (e) => signedPoints(e.comparison?.metrics.quality.change_points) },
              { header: 'Created', cell: (e) => dateTime(e.created_at) },
            ]}
          />
        </Card>
      )}
    </Page>
  )
}

const VERDICT_TEXT = {
  PASS: 'The candidate meets every acceptance criterion with enough evidence.',
  FAIL: 'The candidate misses at least one acceptance criterion.',
  INCONCLUSIVE: 'Criteria are met on average, but the evidence is not strong enough to claim it.',
}

function ci(range: number[] | null | undefined, fmt: (v: number) => string) {
  return range ? `${fmt(range[0])} … ${fmt(range[1])}` : '—'
}

interface Row {
  metric: string
  baseline: string
  candidate: string
  change: string
  ci: string
  n?: number
}

function comparisonRows(e: Experiment): Row[] {
  const m = e.comparison!.metrics
  const L = m.latency_ms
  const r = m.reliability
  const pctOrDash = (v: number | null | undefined) => pct(v)
  return [
    { metric: 'Quality (mean score)', baseline: pct(m.quality.baseline), candidate: pct(m.quality.candidate),
      change: signedPoints(m.quality.change_points), ci: ci(m.quality.change_points_ci, (v) => signedPoints(v)), n: m.quality.n },
    { metric: 'Pass rate', baseline: pct(m.pass_rate.baseline), candidate: pct(m.pass_rate.candidate),
      change: signedPoints(m.pass_rate.change_points), ci: '—', n: m.pass_rate.n },
    { metric: 'Cost per run', baseline: money(m.cost_per_run.baseline), candidate: money(m.cost_per_run.candidate),
      change: signedPct(m.cost_per_run.change_pct), ci: ci(m.cost_per_run.change_pct_ci, (v) => signedPct(v)), n: m.cost_per_run.n },
    { metric: 'Latency mean', baseline: ms(L.baseline.mean), candidate: ms(L.candidate.mean),
      change: signedPct(L.change_pct.mean), ci: ci(L.mean_change_pct_ci, (v) => signedPct(v)), n: L.n },
    { metric: 'Latency median', baseline: ms(L.baseline.median), candidate: ms(L.candidate.median), change: signedPct(L.change_pct.median), ci: '—' },
    { metric: 'Latency p95', baseline: ms(L.baseline.p95), candidate: ms(L.candidate.p95), change: signedPct(L.change_pct.p95), ci: '—' },
    { metric: 'Latency p99', baseline: ms(L.baseline.p99), candidate: ms(L.candidate.p99), change: signedPct(L.change_pct.p99), ci: '—' },
    { metric: 'Success rate', baseline: pctOrDash(r.baseline_success_rate), candidate: pctOrDash(r.candidate_success_rate),
      change: r.baseline_success_rate != null && r.candidate_success_rate != null
        ? signedPoints((r.candidate_success_rate - r.baseline_success_rate) * 100) : '—', ci: '—' },
    { metric: 'Tool failure rate', baseline: pctOrDash(r.baseline_tool_failure_rate), candidate: pctOrDash(r.candidate_tool_failure_rate),
      change: r.baseline_tool_failure_rate != null && r.candidate_tool_failure_rate != null
        ? signedPoints((r.candidate_tool_failure_rate - r.baseline_tool_failure_rate) * 100) : '—', ci: '—' },
    { metric: 'LLM calls per run', baseline: num(m.llm_calls_per_run.baseline, 2), candidate: num(m.llm_calls_per_run.candidate, 2), change: signedPct(m.llm_calls_per_run.change_pct), ci: '—' },
    { metric: 'Tool calls per run', baseline: num(m.tool_calls_per_run.baseline, 2), candidate: num(m.tool_calls_per_run.candidate, 2), change: signedPct(m.tool_calls_per_run.change_pct), ci: '—' },
    { metric: 'Tokens per run', baseline: num(m.tokens_per_run.baseline), candidate: num(m.tokens_per_run.candidate), change: signedPct(m.tokens_per_run.change_pct), ci: '—' },
  ]
}

const CONFIG_FIELDS = ['provider', 'model', 'system_prompt', 'temperature', 'max_tokens', 'tools', 'rag_config', 'options']

const CHECK_ICON = { PASS: '✓', FAIL: '✕', INCONCLUSIVE: '?' }

export function ExperimentDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const exp = useApi<Experiment>(`/experiments/${id}`, {
    pollMs: 2000,
    shouldPoll: (e) => e.status === 'queued' || e.status === 'running',
  })
  const done = exp.data?.status === 'completed'
  const cases = useApi<ExperimentCase[]>(done ? `/experiments/${id}/cases` : null)
  const [error, setError] = useState<string | null>(null)

  async function act(path: string, then: (e: Experiment) => void) {
    setError(null)
    try {
      then(await api.post<Experiment>(path))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  if (exp.error) return <Page title="Experiment"><ErrorBox message={exp.error} /></Page>
  if (!exp.data) return <Page title="Experiment"><Loading /></Page>
  const e = exp.data
  const snap = e.config_snapshot
  const c = e.comparison
  // Experiments created before config_diff was recorded: derive it from the snapshots.
  const diff = snap.config_diff ?? CONFIG_FIELDS.filter(
    (f) => JSON.stringify(snap.baseline[f] ?? null) !== JSON.stringify(snap.candidate[f] ?? null))

  return (
    <Page
      title={e.name}
      subtitle={<>
        {snap.dataset.name} v{snap.dataset.version} · {snap.dataset.cases} cases × {e.repetitions} repetitions ·{' '}
        {snap.evaluators.map((x) => `${x.name} v${x.version}`).join(', ')}
        {e.reproduction_of && <> · reproduction of <A to={`/experiments/${e.reproduction_of}`}>{shortId(e.reproduction_of)}</A></>}
      </>}
      actions={<>
        {e.status === 'created' && <Button onClick={() => act(`/experiments/${e.id}/run`, () => exp.reload())}>Run experiment</Button>}
        {e.status !== 'created' && <Button variant="secondary"
          onClick={() => act(`/experiments/${e.id}/reproduce`, (n) => navigate(`/experiments/${n.id}`))}>Reproduce</Button>}
      </>}
    >
      {error && <ErrorBox message={error} />}
      {e.error && <ErrorBox message={e.error} />}

      <Card>
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-lg"><Badge status={e.verdict ?? e.status} /></span>
          <span className="text-sm text-ink-2">
            {e.verdict ? VERDICT_TEXT[e.verdict]
              : e.status === 'created' ? 'Not run yet.' : `Status: ${e.status}…`}
          </span>
        </div>
        {c && (
          <ul className="mt-3 space-y-1 text-sm">
            {c.checks.map((ch) => (
              <li key={ch.name} className="flex flex-wrap gap-x-2">
                <span aria-hidden className={ch.status === 'PASS' ? 'text-good-text' : ch.status === 'FAIL' ? 'text-critical-text' : 'text-ink'}>
                  {CHECK_ICON[ch.status]}
                </span>
                <span className="font-medium text-ink">{ch.name}</span>
                <span className="text-muted">({ch.rule})</span>
                <span className="text-ink-2">— {ch.status.toLowerCase()}: {ch.reason}</span>
              </li>
            ))}
            {c.pairs < Number(e.acceptance_criteria.min_pairs ?? 0) && (
              <li className="text-ink-2">? only {c.pairs} paired runs; {String(e.acceptance_criteria.min_pairs)} required</li>
            )}
          </ul>
        )}
      </Card>

      {c && (
        <Card title="Baseline vs candidate" subtitle={`${c.pairs} paired runs · 95% bootstrap CI of the paired difference`}>
          <Table
            rows={comparisonRows(e)}
            rowKey={(r) => r.metric}
            columns={[
              { header: 'Metric', cell: (r) => r.metric },
              { header: 'Baseline', align: 'right', cell: (r) => r.baseline },
              { header: 'Candidate', align: 'right', cell: (r) => r.candidate },
              { header: 'Change', align: 'right', cell: (r) => <span className="font-medium">{r.change}</span> },
              { header: '95% CI', align: 'right', cell: (r) => <span className="text-ink-2">{r.ci}</span> },
              { header: 'n', align: 'right', cell: (r) => (r.n == null ? '' : num(r.n)) },
            ]}
          />
        </Card>
      )}

      <Card title="What changed" subtitle="Configuration fields that differ between the arms">
        <Table
          rows={diff}
          rowKey={(f) => f}
          empty="Identical configurations (A/A test)."
          columns={[
            { header: 'Field', cell: (f) => <code className="text-xs">{f}</code> },
            { header: 'Baseline', cell: (f) => <code className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(snap.baseline[f])}</code> },
            { header: 'Candidate', cell: (f) => <code className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(snap.candidate[f])}</code> },
          ]}
        />
        <div className="mt-2 flex gap-4">
          <Json value={snap} label="Full config snapshot" />
          <Json value={e.acceptance_criteria} label="Acceptance criteria" />
        </div>
      </Card>

      {done && (
        <Card title="Per case" subtitle="Largest quality regressions first">
          <Table
            rows={cases.data ?? []}
            rowKey={(r) => `${r.case_key}-${r.repetition}`}
            columns={[
              { header: 'Case', cell: (r) => `${r.case_key} #${r.repetition + 1}` },
              { header: 'Quality Δ', align: 'right', cell: (r) => signedPoints(r.quality_delta == null ? null : r.quality_delta * 100) },
              { header: 'Baseline', cell: (r) => r.baseline && <Side s={r.baseline} /> },
              { header: 'Candidate', cell: (r) => r.candidate && <Side s={r.candidate} /> },
            ]}
          />
        </Card>
      )}
    </Page>
  )
}

function Side({ s }: { s: NonNullable<ExperimentCase['baseline']> }) {
  return (
    <span className="num text-xs text-ink-2">
      <A to={`/runs/${s.run_id}`}>{shortId(s.run_id)}</A> · {pct(s.quality)} · {money(s.estimated_cost)} · {ms(s.latency_ms)}
      {s.status !== 'succeeded' && <> · <Badge status={s.status} /></>}
    </span>
  )
}
