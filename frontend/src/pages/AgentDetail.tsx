import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  api, type Agent, type AgentMetrics, type AgentVersion, type Dataset, type Evaluation,
  type Evaluator, type Experiment, type OptimizationReport, type Recommendation, type Run,
} from '../api'
import {
  A, Badge, Button, Card, ErrorBox, Json, KeyValues, Loading, Page, StatTile, Table, TileRow,
} from '../components/ui'
import { dateTime, enabledOptions, money, ms, num, pct, shortId } from '../format'
import { useApi } from '../useApi'

export function AgentDetail() {
  const { id } = useParams()
  const agent = useApi<Agent>(`/agents/${id}`)
  const versions = useApi<AgentVersion[]>(`/agents/${id}/versions`)
  const metrics = useApi<AgentMetrics>(`/metrics/agents/${id}`)
  const runs = useApi<Run[]>(`/runs?agent_id=${id}&limit=15`)
  const evals = useApi<Evaluation[]>(`/evaluations?agent_id=${id}`)

  if (agent.error) return <Page title="Agent"><ErrorBox message={agent.error} /></Page>
  if (!agent.data) return <Page title="Agent"><Loading /></Page>
  const a = agent.data
  const m = metrics.data

  return (
    <Page title={a.name} subtitle={a.description ?? `Latest version v${a.latest_version.version}`}>
      {m && (
        <TileRow>
          <StatTile label="Runs" value={num(m.runs)} sub={`${num(m.succeeded)} succeeded`} />
          <StatTile label="Success rate" value={pct(m.success_rate)} />
          <StatTile label="Latency (median)" value={ms(m.latency_ms.median)} sub={`p95 ${ms(m.latency_ms.p95)}`} />
          <StatTile label="Avg cost per run" value={money(m.estimated_cost.mean)}
            sub={m.runs_without_cost ? `${num(m.runs_without_cost)} runs not fully priced` : 'all runs priced'} />
          <StatTile label="Tool failure rate" value={pct(m.tool_failure_rate)}
            sub={`${num(m.tool_calls.mean, 1)} tool calls / run`} />
        </TileRow>
      )}

      <Card title="Versions" subtitle="Immutable: every run records the version it used.">
        <Table
          rows={[...(versions.data ?? [])].reverse()}
          rowKey={(v) => v.id}
          columns={[
            { header: 'Version', cell: (v) => `v${v.version}` },
            { header: 'Model', cell: (v) => <code className="text-xs">{v.provider}/{v.model}</code> },
            { header: 'Tools', cell: (v) => v.tools.join(', ') || '—' },
            { header: 'Optimizations', cell: (v) => enabledOptions(v.options).join(', ') || '—' },
            { header: 'System prompt', cell: (v) => <span className="line-clamp-2 text-ink-2">{v.system_prompt || '—'}</span> },
            { header: 'Created', cell: (v) => dateTime(v.created_at) },
            { header: 'Config', cell: (v) => <Json value={v} label="view" /> },
          ]}
        />
      </Card>

      <Optimizations agent={a} />

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Recent runs">
          <Table
            rows={runs.data ?? []}
            rowKey={(r) => r.id}
            columns={[
              { header: 'Run', cell: (r) => <A to={`/runs/${r.id}`}>{shortId(r.id)}</A> },
              { header: 'Status', cell: (r) => <Badge status={r.status} /> },
              { header: 'Latency', align: 'right', cell: (r) => ms(r.total_latency_ms) },
              { header: 'Cost', align: 'right', cell: (r) => money(r.estimated_total_cost) },
              { header: 'Started', cell: (r) => dateTime(r.started_at) },
            ]}
          />
        </Card>
        <Card title="Evaluations">
          <Table
            rows={evals.data ?? []}
            rowKey={(e) => e.id}
            empty="Not evaluated yet."
            columns={[
              { header: 'Evaluation', cell: (e) => <A to={`/evaluations/${e.id}`}>{shortId(e.id)}</A> },
              { header: 'Status', cell: (e) => <Badge status={e.status} /> },
              { header: 'Arm', cell: (e) => e.arm ?? '—' },
              { header: 'Quality', align: 'right', cell: (e) => pct(e.summary?.quality.score.mean) },
              { header: 'Pass rate', align: 'right', cell: (e) => pct(e.summary?.quality.pass_rate) },
            ]}
          />
        </Card>
      </div>
    </Page>
  )
}

function Optimizations({ agent }: { agent: Agent }) {
  const reports = useApi<OptimizationReport[]>(`/optimizations?agent_id=${agent.id}`)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const latest = reports.data?.[0]

  async function analyze() {
    setBusy(true)
    setError(null)
    try {
      await api.post('/optimizations/analyze', { agent_id: agent.id })
      reports.reload()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card
      title="Optimization recommendations"
      subtitle={latest
        ? `From ${num(latest.runs_analyzed)} runs of the analyzed version · ${dateTime(latest.created_at)}`
        : 'Analyze recorded runs for evidence-based optimization opportunities.'}
      actions={<Button onClick={analyze} disabled={busy}>{busy ? 'Analyzing…' : 'Analyze runs'}</Button>}
    >
      {error && <ErrorBox message={error} />}
      {latest && latest.recommendations.length === 0 && (
        <p className="text-sm text-ink-2">No opportunities found in the recorded runs.</p>
      )}
      <div className="space-y-3">
        {latest?.recommendations.map((r) => <RecommendationCard key={r.type} rec={r} report={latest} />)}
      </div>
    </Card>
  )
}

function RecommendationCard({ rec, report }: { rec: Recommendation; report: OptimizationReport }) {
  const [open, setOpen] = useState(false)
  const applicable = Object.keys(rec.suggested_options).length > 0
  return (
    <div className="rounded-md border border-line p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="font-medium text-ink">{rec.title}</div>
          <div className="mt-0.5 text-xs text-muted">
            {rec.type} · confidence {rec.confidence}
            {rec.confidence === 'low' && ' (fewer than 20 runs)'}
          </div>
        </div>
        {applicable && <Button variant="secondary" onClick={() => setOpen(!open)}>
          {open ? 'Cancel' : 'Apply as candidate…'}</Button>}
      </div>
      <div className="mt-3 grid gap-4 md:grid-cols-2">
        <div>
          <div className="mb-1 text-xs font-medium text-ink-2">Evidence</div>
          <KeyValues items={Object.entries(rec.evidence).map(([k, v]) => [k.replaceAll('_', ' '), String(v)])} />
        </div>
        <div>
          <div className="mb-1 text-xs font-medium text-ink-2">Estimated impact</div>
          {Object.keys(rec.estimated_impact).length
            ? <KeyValues items={Object.entries(rec.estimated_impact).map(([k, v]) => [k.replaceAll('_', ' '), String(v)])} />
            : <span className="text-sm text-muted">No estimate</span>}
        </div>
      </div>
      <p className="mt-3 text-xs text-ink-2"><span className="font-medium">How estimated: </span>{rec.basis}</p>
      {rec.caveats.length > 0 && (
        <ul className="mt-1 list-disc pl-5 text-xs text-ink-2">
          {rec.caveats.map((c) => <li key={c}>{c}</li>)}
        </ul>
      )}
      {open && <ApplyForm rec={rec} report={report} />}
    </div>
  )
}

function ApplyForm({ rec, report }: { rec: Recommendation; report: OptimizationReport }) {
  const navigate = useNavigate()
  const datasets = useApi<Dataset[]>('/datasets')
  const evaluators = useApi<Evaluator[]>('/evaluators')
  const [datasetId, setDatasetId] = useState('')
  const [evalIds, setEvalIds] = useState<string[]>([])
  const [criteria, setCriteria] = useState({
    max_quality_drop_points: '2', min_cost_reduction_pct: '', min_latency_reduction_pct: '',
    max_error_rate: '0.05', repetitions: '2',
  })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit() {
    setBusy(true)
    setError(null)
    const ac: Record<string, number> = {}
    for (const k of ['max_quality_drop_points', 'min_cost_reduction_pct', 'min_latency_reduction_pct', 'max_error_rate'] as const) {
      if (criteria[k] !== '') ac[k] = Number(criteria[k])
    }
    try {
      const out = await api.post<{ agent_version: AgentVersion; experiment: Experiment | null }>(
        `/optimizations/${report.id}/apply`,
        {
          type: rec.type,
          experiment: datasetId ? {
            dataset_id: datasetId, evaluator_ids: evalIds,
            repetitions: Number(criteria.repetitions), acceptance_criteria: ac,
          } : null,
        },
      )
      if (out.experiment) navigate(`/experiments/${out.experiment.id}`)
      else navigate(0)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const input = 'w-24 rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink'
  return (
    <div className="mt-3 space-y-3 rounded-md bg-surface-2 p-3 text-sm">
      <p className="text-ink-2">
        Creates a new agent version with <code className="text-xs">{JSON.stringify(rec.suggested_options)}</code>.
        Choose a dataset to also create an experiment against the analyzed version. Nothing is promoted until the experiment passes.
      </p>
      <label className="flex items-center gap-2">
        Dataset
        <select value={datasetId} onChange={(e) => setDatasetId(e.target.value)}
          className="rounded-md border border-line bg-surface px-2 py-1 text-ink">
          <option value="">No experiment (version only)</option>
          {datasets.data?.map((d) => <option key={d.id} value={d.id}>{d.name} v{d.version} ({d.case_count} cases)</option>)}
        </select>
      </label>
      {datasetId && (
        <>
          <fieldset>
            <legend className="mb-1">Evaluators</legend>
            <div className="flex flex-wrap gap-3">
              {evaluators.data?.map((ev) => (
                <label key={ev.id} className="flex items-center gap-1.5">
                  <input type="checkbox" checked={evalIds.includes(ev.id)}
                    onChange={(e) => setEvalIds(e.target.checked ? [...evalIds, ev.id] : evalIds.filter((x) => x !== ev.id))} />
                  {ev.name} v{ev.version} <span className="text-muted">({ev.type})</span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="flex flex-wrap gap-4">
            {([
              ['max_quality_drop_points', 'Max quality drop (pts)'],
              ['min_cost_reduction_pct', 'Min cost reduction %'],
              ['min_latency_reduction_pct', 'Min latency reduction %'],
              ['max_error_rate', 'Max error rate (0–1)'],
              ['repetitions', 'Repetitions'],
            ] as const).map(([k, label]) => (
              <label key={k} className="flex flex-col gap-1 text-xs text-ink-2">
                {label}
                <input className={input} value={criteria[k]} inputMode="decimal"
                  onChange={(e) => setCriteria({ ...criteria, [k]: e.target.value })} />
              </label>
            ))}
          </div>
        </>
      )}
      {error && <ErrorBox message={error} />}
      <Button onClick={submit} disabled={busy || (!!datasetId && evalIds.length === 0)}>
        {datasetId ? 'Create version + experiment' : 'Create version'}
      </Button>
    </div>
  )
}
