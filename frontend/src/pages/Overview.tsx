import { useState } from 'react'
import type { Agent, Experiment, Kpis, Overview as OverviewData, TrendPoint } from '../api'
import { ChartCard, Columns, TrendLines, type Series } from '../components/charts'
import {
  A, Badge, Card, Delta, ErrorBox, Loading, Page, Select, StatTile, Table, TileRow,
} from '../components/ui'
import { dateTime, money, ms, num, pct, shortDate, signedPct, signedPoints } from '../format'
import { useApi } from '../useApi'

function relChange(cur: number | null, prev: number | null): number | null {
  if (cur == null || prev == null || prev === 0) return null
  return ((cur - prev) / prev) * 100
}

function KpiTiles({ k, prev, days }: { k: Kpis; prev: Kpis; days: number }) {
  const vs = `vs previous ${days}d`
  const successPts = k.success_rate != null && prev.success_rate != null
    ? (k.success_rate - prev.success_rate) * 100 : null
  const qualityPts = k.avg_quality != null && prev.avg_quality != null
    ? (k.avg_quality - prev.avg_quality) * 100 : null
  const cost = relChange(k.avg_cost, prev.avg_cost)
  const lat = relChange(k.p50_latency_ms, prev.p50_latency_ms)
  return (
    <TileRow>
      <StatTile label="Agent runs" value={num(k.runs)}
        delta={<Delta value={prev.runs ? k.runs - prev.runs : null}
          text={`${k.runs - prev.runs >= 0 ? '+' : ''}${num(k.runs - prev.runs)}`}
          goodWhen="neutral" suffix={vs} />} />
      <StatTile label="Success rate" value={pct(k.success_rate)}
        delta={<Delta value={successPts} text={signedPoints(successPts)} goodWhen="up" suffix={vs} />} />
      <StatTile label="Avg cost per run" value={money(k.avg_cost)}
        sub={`${num(k.runs_with_cost)} of ${num(k.runs)} runs fully priced`}
        delta={<Delta value={cost} text={signedPct(cost)} goodWhen="down" suffix={vs} />} />
      <StatTile label="Latency (median)" value={ms(k.p50_latency_ms)}
        sub={`p95 ${ms(k.p95_latency_ms)}`}
        delta={<Delta value={lat} text={signedPct(lat)} goodWhen="down" suffix={vs} />} />
      <StatTile label="Avg quality" value={pct(k.avg_quality)}
        sub={`${num(k.runs_with_quality)} evaluated runs`}
        delta={<Delta value={qualityPts} text={signedPoints(qualityPts)} goodWhen="up" suffix={vs} />} />
    </TileRow>
  )
}

const one = (key: keyof TrendPoint & string, label: string, format: (v: number | null) => string): Series<TrendPoint> =>
  ({ key, label, color: 'var(--series-1)', format })

export function Overview() {
  const [days, setDays] = useState('30')
  const [agentId, setAgentId] = useState('')
  const agents = useApi<Agent[]>('/agents')
  const path = `/metrics/overview?days=${days}${agentId ? `&agent_id=${agentId}` : ''}`
  const { data, error, loading } = useApi<OverviewData>(path)
  const experiments = useApi<Experiment[]>('/experiments')

  const trend = data?.trend ?? []
  const common = { rows: trend, xKey: 'date' as const, xFormat: shortDate, xLabel: 'Date' }
  const latency: Series<TrendPoint>[] = [
    { key: 'p50_latency_ms', label: 'Median', color: 'var(--series-1)', format: ms },
    { key: 'p95_latency_ms', label: 'p95', color: 'var(--series-2)', format: ms },
  ]
  const runsS = one('runs', 'Runs', (v) => num(v))
  const costS = one('avg_cost', 'Avg cost per run', money)
  const qualityS = one('avg_quality', 'Avg quality', (v) => pct(v))

  return (
    <Page title="Overview" subtitle="Quality, cost and latency across all recorded agent runs.">
      <div className="flex flex-wrap items-center gap-4">
        <Select label="Range" value={days} onChange={setDays} options={[
          { value: '7', label: 'Last 7 days' },
          { value: '30', label: 'Last 30 days' },
          { value: '90', label: 'Last 90 days' },
        ]} />
        <Select label="Agent" value={agentId} onChange={setAgentId} options={[
          { value: '', label: 'All agents' },
          ...(agents.data ?? []).map((a) => ({ value: a.id, label: a.name })),
        ]} />
      </div>
      {error && <ErrorBox message={error} />}
      {!data && !error && <Loading />}
      {data && (
        <div className={`space-y-5 transition-opacity ${loading ? 'opacity-60' : ''}`}>
          <KpiTiles k={data.kpis} prev={data.previous} days={data.days} />
          <div className="grid gap-5 lg:grid-cols-2">
            <ChartCard title="Runs per day" {...common} series={[runsS]}>
              <Columns {...common} series={runsS} yFormat={(v) => num(v)} />
            </ChartCard>
            <ChartCard title="Cost per run" subtitle="Mean estimated cost of fully priced runs"
              {...common} series={[costS]}>
              <TrendLines {...common} series={[costS]} yFormat={(v) => money(v)} />
            </ChartCard>
            <ChartCard title="Latency" subtitle="Succeeded runs, end to end" {...common} series={latency}>
              <TrendLines {...common} series={latency} yFormat={ms} />
            </ChartCard>
            <ChartCard title="Quality" subtitle="Mean evaluator score of evaluated runs"
              {...common} series={[qualityS]}>
              <TrendLines {...common} series={[qualityS]} yFormat={(v) => pct(v, 0)} />
            </ChartCard>
          </div>
        </div>
      )}
      <Card title="Recent experiments">
        <Table
          rows={(experiments.data ?? []).slice(0, 8)}
          rowKey={(e) => e.id}
          empty="No experiments yet."
          columns={[
            { header: 'Experiment', cell: (e) => <A to={`/experiments/${e.id}`}>{e.name}</A> },
            { header: 'Verdict', cell: (e) => <Badge status={e.verdict ?? e.status} /> },
            { header: 'Cost', align: 'right', cell: (e) => signedPct(e.comparison?.metrics.cost_per_run.change_pct) },
            { header: 'Latency', align: 'right', cell: (e) => signedPct(e.comparison?.metrics.latency_ms.change_pct.mean) },
            { header: 'Quality', align: 'right', cell: (e) => signedPoints(e.comparison?.metrics.quality.change_points) },
            { header: 'Created', cell: (e) => dateTime(e.created_at) },
          ]}
        />
      </Card>
    </Page>
  )
}
