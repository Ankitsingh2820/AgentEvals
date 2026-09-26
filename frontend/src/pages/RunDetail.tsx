import { useState } from 'react'
import { useParams } from 'react-router-dom'
import type { Step, Trace } from '../api'
import {
  A, Badge, Card, ErrorBox, Json, KeyValues, Loading, Page, StatTile, TileRow,
} from '../components/ui'
import { dateTime, money, ms, num, shortId } from '../format'
import { useApi } from '../useApi'

function stepLabel(s: Step): string {
  if (s.llm_call) return s.llm_call.purpose === 'routing' ? `route · ${s.llm_call.model}` : `LLM · ${s.llm_call.model}`
  return `tool · ${s.tool_call?.tool_name}`
}

function stepColor(s: Step): string {
  if (s.tool_call?.skip_reason) return 'var(--skipped)'
  return s.type === 'llm_call' ? 'var(--series-1)' : 'var(--series-2)'
}

/** Gantt-style timeline: each step is a bar from its start offset, on the run's clock. */
function Timeline({ trace, selected, onSelect }: {
  trace: Trace
  selected: number
  onSelect: (seq: number) => void
}) {
  const total = Math.max(trace.latency.total_ms ?? 0,
    ...trace.steps.map((s) => s.start_offset_ms + s.latency_ms), 0.001)
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-4 text-xs text-ink-2">
        {[['var(--series-1)', 'LLM call'], ['var(--series-2)', 'Tool call'], ['var(--skipped)', 'Skipped (dedupe / budget)']].map(([c, l]) => (
          <span key={l} className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2.5 w-2.5 rounded-sm" style={{ background: c }} aria-hidden />{l}
          </span>
        ))}
      </div>
      <div className="space-y-0.5" role="list">
        {trace.steps.map((s) => {
          const left = (s.start_offset_ms / total) * 100
          const width = Math.max((s.latency_ms / total) * 100, 0.4)
          const active = s.sequence === selected
          return (
            <button
              key={s.sequence}
              role="listitem"
              onClick={() => onSelect(s.sequence)}
              aria-pressed={active}
              title={`${stepLabel(s)} · starts ${ms(s.start_offset_ms)} · ${ms(s.latency_ms)}`}
              className={`grid w-full grid-cols-[3rem_minmax(8rem,14rem)_1fr_5rem] items-center gap-2 rounded px-1 py-1 text-left text-xs ${active ? 'bg-surface-2' : 'hover:bg-surface-2'}`}
            >
              <span className="num text-muted">{ms(s.start_offset_ms)}</span>
              <span className="truncate text-ink">
                <span className="text-muted">t{s.turn} </span>{stepLabel(s)}
                {s.status === 'failed' && <span className="text-critical-text"> ✕</span>}
              </span>
              <span className="relative h-3">
                <span className="absolute inset-y-0 rounded-sm" style={{ left: `${left}%`, width: `${width}%`, background: stepColor(s) }} />
              </span>
              <span className="num text-right text-ink-2">{ms(s.latency_ms)}</span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function StepDetail({ step, currency }: { step: Step; currency: string }) {
  const l = step.llm_call
  const t = step.tool_call
  return (
    <div className="space-y-3">
      <KeyValues items={[
        ['Step', `#${step.sequence} · turn ${step.turn} · ${step.type}`],
        ['Status', <Badge key="s" status={step.status} />],
        ['Starts at', ms(step.start_offset_ms)],
        ['Latency', ms(step.latency_ms)],
        ...(step.error ? [['Error', <span key="e" className="text-critical-text">{step.error}</span>] as [string, React.ReactNode]] : []),
      ]} />
      {l && (
        <>
          <KeyValues items={[
            ['Model', `${l.provider}/${l.model}${l.response_model && l.response_model !== l.model ? ` (served by ${l.response_model})` : ''}`],
            ['Purpose', l.purpose],
            ['Stop reason', l.stop_reason ?? '—'],
            ['Tokens', `${num(l.input_tokens)} in · ${num(l.output_tokens)} out · ${num(l.cache_creation_input_tokens)} cache write · ${num(l.cache_read_input_tokens)} cache read`],
            ['Estimated cost', l.estimated_cost == null ? 'not priced'
              : `${money(l.estimated_cost, currency)} (in ${money(l.input_cost, currency)} · out ${money(l.output_cost, currency)} · cache ${money(Number(l.cache_write_cost) + Number(l.cache_read_cost), currency)})`],
          ]} />
          {l.pricing_snapshot && <Json value={l.pricing_snapshot} label="Prices used" />}
          <Json value={l.request} label="Request" />
          <Json value={l.response} label="Response" />
        </>
      )}
      {t && (
        <>
          <KeyValues items={[
            ['Tool', t.tool_name],
            ['Executed', t.skip_reason ? `no (${t.skip_reason})` : 'yes'],
            ['Arguments', <code key="a" className="text-xs">{JSON.stringify(t.arguments)}</code>],
            ['Cost', t.estimated_cost == null ? 'free / unpriced' : money(t.estimated_cost, currency)],
          ]} />
          <div>
            <div className="mb-1 text-xs text-ink-2">Result</div>
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded bg-surface-2 p-2 text-xs">{t.result ?? '—'}</pre>
          </div>
        </>
      )}
    </div>
  )
}

export function RunDetail() {
  const { id } = useParams()
  const { data, error } = useApi<Trace>(`/runs/${id}/trace`)
  const [selected, setSelected] = useState(1)
  if (error) return <Page title="Run"><ErrorBox message={error} /></Page>
  if (!data) return <Page title="Run"><Loading /></Page>
  const r = data.run
  const v = data.agent_version
  const step = data.steps.find((s) => s.sequence === selected) ?? data.steps[0]
  const currency = r.currency ?? 'USD'

  return (
    <Page
      title={<span className="flex items-center gap-2">Run {shortId(r.id)} <Badge status={r.status} /></span>}
      subtitle={<>
        <A to={`/agents/${r.agent_id}`}>Agent</A> v{v.version} · {v.provider}/{r.routed_model ?? v.model}
        {r.route && <> · route <code className="text-xs">{r.route}</code></>}
        {r.evaluation_id && <> · <A to={`/evaluations/${r.evaluation_id}`}>evaluation</A></>}
        {r.experiment_id && <> · <A to={`/experiments/${r.experiment_id}`}>experiment</A></>}
        {' · '}{dateTime(r.started_at)}
      </>}
    >
      <TileRow>
        <StatTile label="Total latency" value={ms(data.latency.total_ms)}
          sub={`LLM ${ms(data.latency.llm_ms)} · tools ${ms(data.latency.tool_ms)} · other ${ms(data.latency.other_ms)}`} />
        <StatTile label="Estimated cost" value={money(r.estimated_total_cost, currency)}
          sub={r.cost_status === 'complete' ? 'all calls priced' : `cost ${r.cost_status ?? 'not computed'}`} />
        <StatTile label="LLM calls" value={num(r.llm_call_count)} />
        <StatTile label="Tool calls" value={num(r.tool_call_count)}
          sub={`${num(r.tool_failure_count)} failed · ${num(r.tool_calls_skipped)} skipped`} />
        <StatTile label="Tokens" value={num(r.input_tokens + r.output_tokens + r.cache_creation_input_tokens + r.cache_read_input_tokens)}
          sub={`${num(r.input_tokens)} in · ${num(r.output_tokens)} out · ${num(r.cache_read_input_tokens)} cached`} />
      </TileRow>

      {r.error && <ErrorBox message={r.error} />}

      <div className="grid gap-5 lg:grid-cols-[3fr_2fr]">
        <Card title="Timeline" subtitle="Where the run spent its time. Select a step to inspect it.">
          {data.steps.length ? <Timeline trace={data} selected={step.sequence} onSelect={setSelected} />
            : <p className="text-sm text-muted">No steps recorded.</p>}
        </Card>
        <Card title="Step detail">{step ? <StepDetail step={step} currency={currency} /> : null}</Card>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Input"><pre className="whitespace-pre-wrap text-sm">{r.input}</pre></Card>
        <Card title="Final output"><pre className="whitespace-pre-wrap text-sm">{r.final_output ?? '—'}</pre></Card>
      </div>
    </Page>
  )
}
