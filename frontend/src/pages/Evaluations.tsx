import { useParams } from 'react-router-dom'
import type { Evaluation, EvaluationResult } from '../api'
import { ChartCard, Columns, type Series } from '../components/charts'
import {
  A, Badge, Card, ErrorBox, Loading, Page, StatTile, Table, TileRow,
} from '../components/ui'
import { dateTime, money, ms, num, pct, shortId } from '../format'
import { useApi } from '../useApi'

export function Evaluations() {
  const { data, error } = useApi<Evaluation[]>('/evaluations')
  return (
    <Page title="Evaluations" subtitle="An agent version run over a dataset and scored by evaluators.">
      {error && <ErrorBox message={error} />}
      {!data && !error && <Loading />}
      {data && (
        <Card>
          <Table
            rows={data}
            rowKey={(e) => e.id}
            empty="No evaluations yet."
            columns={[
              { header: 'Evaluation', cell: (e) => <A to={`/evaluations/${e.id}`}>{shortId(e.id)}</A> },
              { header: 'Status', cell: (e) => <Badge status={e.status} /> },
              { header: 'Agent', cell: (e) => <A to={`/agents/${e.agent_id}`}>{shortId(e.agent_id)}</A> },
              { header: 'Experiment', cell: (e) => e.experiment_id
                ? <A to={`/experiments/${e.experiment_id}`}>{e.arm}</A> : '—' },
              { header: 'Runs', align: 'right', cell: (e) => num(e.summary?.runs ?? e.summary?.cases) },
              { header: 'Quality', align: 'right', cell: (e) => pct(e.summary?.quality.score.mean) },
              { header: 'Pass rate', align: 'right', cell: (e) => pct(e.summary?.quality.pass_rate) },
              { header: 'Created', cell: (e) => dateTime(e.created_at) },
            ]}
          />
        </Card>
      )}
    </Page>
  )
}

interface Bucket { bucket: string; runs: number }

export function EvaluationDetail() {
  const { id } = useParams()
  const ev = useApi<Evaluation>(`/evaluations/${id}`, {
    pollMs: 2000,
    shouldPoll: (e) => e.status === 'pending' || e.status === 'running',
  })
  const results = useApi<EvaluationResult[]>(ev.data?.status === 'completed' ? `/evaluations/${id}/results` : null)
  if (ev.error) return <Page title="Evaluation"><ErrorBox message={ev.error} /></Page>
  if (!ev.data) return <Page title="Evaluation"><Loading /></Page>
  const e = ev.data
  const s = e.summary
  // Summaries saved before repetitions existed counted runs as "cases".
  const legacy = s as unknown as { quality: { cases_scored?: number; cases_unscored?: number } } | null
  const runsScored = s?.quality.runs_scored ?? legacy?.quality.cases_scored
  const runsUnscored = s?.quality.runs_unscored ?? legacy?.quality.cases_unscored
  const runCount = s?.runs ?? s?.cases

  const buckets: Bucket[] = s ? Object.entries(s.quality.score_distribution).map(([bucket, runs]) => ({ bucket, runs })) : []
  const bucketSeries: Series<Bucket> = { key: 'runs', label: 'Runs', color: 'var(--series-1)', format: (v) => num(v) }

  return (
    <Page
      title={<span className="flex items-center gap-2">Evaluation {shortId(e.id)} <Badge status={e.status} /></span>}
      subtitle={<>
        <A to={`/agents/${e.agent_id}`}>Agent</A> · {e.repetitions} repetition(s)
        {e.experiment_id && <> · <A to={`/experiments/${e.experiment_id}`}>experiment</A> ({e.arm})</>}
      </>}
    >
      {e.error && <ErrorBox message={e.error} />}
      {s && (
        <>
          <TileRow>
            <StatTile label="Quality (mean score)" value={pct(s.quality.score.mean)}
              sub={`${num(runsScored)} runs scored · ${num(runsUnscored)} unscored`} />
            <StatTile label="Pass rate" value={pct(s.quality.pass_rate)} sub="all scored verdicts passed" />
            <StatTile label="Run success rate" value={pct(s.success_rate)} sub={`${num(runCount)} runs over ${num(s.cases)} cases`} />
            <StatTile label="Agent cost" value={money(s.estimated_total_cost)}
              sub={s.runs_without_cost ? `${num(s.runs_without_cost)} runs not fully priced` : 'total, all runs'} />
            <StatTile label="Judge cost" value={money(s.judge_estimated_cost)}
              sub={`${num(s.judge_calls)} judge calls · latency p50 ${ms(s.latency_ms.median)}`} />
          </TileRow>

          <div className="grid gap-5 lg:grid-cols-[2fr_3fr]">
            <ChartCard title="Score distribution" subtitle="Runs by mean evaluator score"
              rows={buckets} xKey="bucket" xLabel="Score" xFormat={(v) => v} series={[bucketSeries]}>
              <Columns rows={buckets} xKey="bucket" xFormat={(v) => v} series={bucketSeries} yFormat={(v) => num(v)} height={200} />
            </ChartCard>
            <Card title="Evaluators">
              <Table
                rows={Object.entries(s.evaluators)}
                rowKey={([evId]) => evId}
                columns={[
                  { header: 'Evaluator', cell: ([, v]) => <>{v.name} <span className="text-muted">v{v.version} · {v.type}</span></> },
                  { header: 'Mean', align: 'right', cell: ([, v]) => pct(v.score.mean) },
                  { header: 'Pass rate', align: 'right', cell: ([, v]) => pct(v.pass_rate) },
                  { header: 'Scored', align: 'right', cell: ([, v]) => num(v.scored) },
                  { header: 'Skipped', align: 'right', cell: ([, v]) => num(v.skipped) },
                  { header: 'Errors', align: 'right', cell: ([, v]) => num(v.errors) },
                ]}
              />
            </Card>
          </div>
        </>
      )}

      {results.data && (
        <Card title="Verdicts" subtitle="Every evaluator's verdict on every run, with its reason">
          <Table
            rows={results.data}
            rowKey={(r) => r.id}
            columns={[
              { header: 'Case', cell: (r) => r.case_key },
              { header: 'Evaluator', cell: (r) => r.evaluator_name },
              { header: 'Verdict', cell: (r) => <Badge status={r.status === 'ok' ? (r.passed ? 'PASS' : 'FAIL') : r.status} /> },
              { header: 'Score', align: 'right', cell: (r) => pct(r.score, 0) },
              { header: 'Reason', cell: (r) => <span className="line-clamp-3 text-ink-2">{r.reason}</span> },
              { header: 'Run', cell: (r) => <A to={`/runs/${r.run_id}`}>{shortId(r.run_id)}</A> },
            ]}
          />
        </Card>
      )}
    </Page>
  )
}
