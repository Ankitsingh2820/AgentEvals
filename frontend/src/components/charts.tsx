// Charts follow the dataviz method: one y-axis per chart, 2px lines, <=24px bars with
// 4px rounded data-ends, hairline solid grid, crosshair tooltip listing every series,
// legend for 2+ series, and a table view as the accessible twin of every chart.
import { useState, type ReactNode } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Card, Table } from './ui'

export interface Series<T> {
  key: keyof T & string
  label: string
  color: string // a CSS var() role
  format: (v: number | null) => string
}

/** Card with title and a Chart/Table toggle. */
export function ChartCard<T>({ title, subtitle, rows, xLabel, xKey, xFormat, series, children }: {
  title: string
  subtitle?: ReactNode
  rows: T[]
  xLabel: string
  xKey: keyof T & string
  xFormat: (v: string) => string
  series: Series<T>[]
  children: ReactNode
}) {
  const [view, setView] = useState<'chart' | 'table'>('chart')
  return (
    <Card
      title={title}
      subtitle={subtitle}
      actions={
        <div className="flex rounded-md border border-line text-xs" role="group" aria-label="View">
          {(['chart', 'table'] as const).map((v) => (
            <button key={v} onClick={() => setView(v)} aria-pressed={view === v}
              className={`px-2 py-1 capitalize ${view === v ? 'bg-surface-2 font-medium text-ink' : 'text-ink-2'}`}>
              {v}
            </button>
          ))}
        </div>
      }
    >
      {series.length > 1 && view === 'chart' && <Legend series={series} />}
      {view === 'chart' ? children : (
        <div className="max-h-64 overflow-y-auto">
          <Table
            rows={rows}
            rowKey={(r) => String(r[xKey])}
            columns={[
              { header: xLabel, cell: (r) => xFormat(String(r[xKey])) },
              ...series.map((s) => ({
                header: s.label,
                align: 'right' as const,
                cell: (r: T) => s.format(r[s.key] as number | null),
              })),
            ]}
          />
        </div>
      )}
    </Card>
  )
}

function Legend<T>({ series }: { series: Series<T>[] }) {
  return (
    <div className="mb-2 flex gap-4 text-xs text-ink-2">
      {series.map((s) => (
        <span key={s.key} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-0.5 w-4 rounded" style={{ background: s.color }} aria-hidden />
          {s.label}
        </span>
      ))}
    </div>
  )
}

interface TooltipLikeProps {
  active?: boolean
  label?: string | number
  payload?: ReadonlyArray<{ dataKey?: unknown; value?: unknown }>
}

function ChartTooltip<T>({ active, label, payload, series, xFormat }: TooltipLikeProps & {
  series: Series<T>[]
  xFormat: (v: string) => string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-sm">
      <div className="mb-1 text-ink-2">{xFormat(String(label))}</div>
      {series.map((s) => {
        const p = payload.find((x) => x.dataKey === s.key)
        const v = p?.value as number | null | undefined
        return (
          <div key={s.key} className="flex items-center gap-2">
            <span className="inline-block h-0.5 w-3 rounded" style={{ background: s.color }} aria-hidden />
            <span className="num font-semibold text-ink">{s.format(v ?? null)}</span>
            <span className="text-ink-2">{s.label}</span>
          </div>
        )
      })}
    </div>
  )
}

const axis = { stroke: 'var(--axis)', tick: { fill: 'var(--muted)', fontSize: 11 }, tickLine: false }

export function TrendLines<T>({ rows, xKey, xFormat, series, yFormat, height = 220 }: {
  rows: T[]
  xKey: keyof T & string
  xFormat: (v: string) => string
  series: Series<T>[]
  yFormat: (v: number) => string
  height?: number
}) {
  // Show markers when data is sparse, so isolated points (gaps around them) stay visible.
  const sparse = rows.filter((r) => series.some((s) => r[s.key] != null)).length < 12
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows as Record<string, unknown>[]} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey={xKey as string} tickFormatter={(v) => xFormat(String(v))} {...axis} minTickGap={24} />
        <YAxis tickFormatter={yFormat} {...axis} axisLine={false} width={64} />
        <Tooltip
          cursor={{ stroke: 'var(--axis)', strokeWidth: 1 }}
          content={(p: TooltipLikeProps) => <ChartTooltip {...p} series={series} xFormat={xFormat} />}
        />
        {series.map((s) => (
          <Line
            key={s.key}
            dataKey={s.key as string}
            name={s.label}
            stroke={s.color}
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            dot={sparse ? { r: 4, fill: s.color, stroke: 'var(--surface)', strokeWidth: 2 } : false}
            activeDot={{ r: 5, fill: s.color, stroke: 'var(--surface)', strokeWidth: 2 }}
            connectNulls={false}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  )
}

export function Columns<T>({ rows, xKey, xFormat, series, yFormat, height = 220 }: {
  rows: T[]
  xKey: keyof T & string
  xFormat: (v: string) => string
  series: Series<T>
  yFormat: (v: number) => string
  height?: number
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={rows as Record<string, unknown>[]} margin={{ top: 8, right: 12, bottom: 0, left: 0 }} barCategoryGap={2}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey={xKey as string} tickFormatter={(v) => xFormat(String(v))} {...axis} minTickGap={24} />
        <YAxis tickFormatter={yFormat} {...axis} axisLine={false} width={64} allowDecimals={false} />
        <Tooltip
          cursor={{ fill: 'var(--surface-2)' }}
          content={(p: TooltipLikeProps) => <ChartTooltip {...p} series={[series]} xFormat={xFormat} />}
        />
        <Bar dataKey={series.key as string} fill={series.color} maxBarSize={24} radius={[4, 4, 0, 0]}
          isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  )
}
