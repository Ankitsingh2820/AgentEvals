import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

export function Page({ title, subtitle, actions, children }: {
  title: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-ink">{title}</h1>
          {subtitle && <div className="mt-1 text-sm text-ink-2">{subtitle}</div>}
        </div>
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </div>
      <div className="space-y-5">{children}</div>
    </div>
  )
}

export function Card({ title, subtitle, actions, children, className = '' }: {
  title?: ReactNode
  subtitle?: ReactNode
  actions?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={`rounded-lg border border-line bg-surface p-4 ${className}`}>
      {(title || actions) && (
        <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
          <div>
            {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  )
}

/** Stat tile: label, value, optional sub-line and delta (figure contract). */
export function StatTile({ label, value, sub, delta }: {
  label: string
  value: ReactNode
  sub?: ReactNode
  delta?: ReactNode
}) {
  return (
    <div className="rounded-lg border border-line bg-surface p-4">
      <div className="text-xs text-ink-2">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-ink">{value}</div>
      {delta && <div className="mt-1 text-xs">{delta}</div>}
      {sub && <div className="mt-1 text-xs text-muted">{sub}</div>}
    </div>
  )
}

export function TileRow({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">{children}</div>
}

/**
 * Change vs a reference, with direction shown by sign + arrow (never color alone).
 * `goodWhen` says whether an increase or a decrease is the good direction.
 */
export function Delta({ value, text, goodWhen, suffix }: {
  value: number | null | undefined
  text: string
  goodWhen: 'up' | 'down' | 'neutral'
  suffix?: string
}) {
  if (value == null) return <span className="text-muted">no comparison</span>
  const good = value === 0 || goodWhen === 'neutral' ? null : (value > 0) === (goodWhen === 'up')
  const cls = good == null ? 'text-ink-2' : good ? 'text-good-text' : 'text-critical-text'
  const arrow = value > 0 ? '↑' : value < 0 ? '↓' : '→'
  return (
    <span className={cls}>
      {arrow} {text}
      {suffix && <span className="text-muted"> {suffix}</span>}
    </span>
  )
}

const STATUS: Record<string, { icon: string; cls: string; label?: string }> = {
  PASS: { icon: '✓', cls: 'bg-good/15 text-good-text' },
  FAIL: { icon: '✕', cls: 'bg-critical/15 text-critical-text' },
  INCONCLUSIVE: { icon: '?', cls: 'bg-warning/20 text-ink' },
  succeeded: { icon: '✓', cls: 'bg-good/15 text-good-text' },
  completed: { icon: '✓', cls: 'bg-good/15 text-good-text' },
  ok: { icon: '✓', cls: 'bg-good/15 text-good-text' },
  failed: { icon: '✕', cls: 'bg-critical/15 text-critical-text' },
  error: { icon: '!', cls: 'bg-critical/15 text-critical-text' },
  running: { icon: '◌', cls: 'bg-surface-2 text-ink-2' },
  queued: { icon: '◌', cls: 'bg-surface-2 text-ink-2' },
  pending: { icon: '◌', cls: 'bg-surface-2 text-ink-2' },
  created: { icon: '○', cls: 'bg-surface-2 text-ink-2' },
  skipped: { icon: '–', cls: 'bg-surface-2 text-ink-2' },
}

/** Status/verdict badge: status color + icon + label, so meaning never rides on color alone. */
export function Badge({ status }: { status: string | null | undefined }) {
  if (!status) return <span className="text-muted">—</span>
  const s = STATUS[status] ?? { icon: '•', cls: 'bg-surface-2 text-ink-2' }
  return (
    <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium ${s.cls}`}>
      <span aria-hidden>{s.icon}</span>
      {status}
    </span>
  )
}

export function Button({ children, onClick, disabled, variant = 'primary', type = 'button' }: {
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  variant?: 'primary' | 'secondary'
  type?: 'button' | 'submit'
}) {
  const cls = variant === 'primary'
    ? 'bg-series-1 text-white hover:opacity-90'
    : 'border border-line bg-surface text-ink hover:bg-surface-2'
  return (
    <button type={type} onClick={onClick} disabled={disabled}
      className={`rounded-md px-3 py-1.5 text-sm font-medium disabled:opacity-50 ${cls}`}>
      {children}
    </button>
  )
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div role="alert" className="rounded-md border border-line bg-critical/10 p-3 text-sm text-critical-text">
      <span aria-hidden>! </span>{message}
    </div>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-8 text-center text-sm text-muted">{children}</div>
}

export function Loading() {
  return <div className="py-8 text-center text-sm text-muted">Loading…</div>
}

export interface Column<T> {
  header: string
  cell: (row: T) => ReactNode
  align?: 'left' | 'right'
}

export function Table<T>({ rows, columns, rowKey, empty = 'Nothing yet.' }: {
  rows: T[]
  columns: Column<T>[]
  rowKey: (row: T, i: number) => string
  empty?: ReactNode
}) {
  if (rows.length === 0) return <Empty>{empty}</Empty>
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs text-ink-2">
            {columns.map((c) => (
              <th key={c.header} className={`px-2 py-2 font-medium ${c.align === 'right' ? 'text-right' : ''}`}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={rowKey(row, i)} className="border-b border-line last:border-0">
              {columns.map((c) => (
                <td key={c.header} className={`px-2 py-2 align-top ${c.align === 'right' ? 'num text-right' : ''}`}>
                  {c.cell(row)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function A({ to, children }: { to: string; children: ReactNode }) {
  return <Link to={to} className="text-series-1 hover:underline">{children}</Link>
}

export function Json({ value, label = 'JSON' }: { value: unknown; label?: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div>
      <button className="text-xs text-series-1 hover:underline" onClick={() => setOpen(!open)}>
        {open ? '▾' : '▸'} {label}
      </button>
      {open && (
        <pre className="mt-1 max-h-96 overflow-auto rounded bg-surface-2 p-2 text-xs text-ink">
          {JSON.stringify(value, null, 2)}
        </pre>
      )}
    </div>
  )
}

export function KeyValues({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
      {items.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-2">{k}</dt>
          <dd className="min-w-0 break-words text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

export function Select({ label, value, onChange, options }: {
  label: string
  value: string
  onChange: (v: string) => void
  options: { value: string; label: string }[]
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-ink-2">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-line bg-surface px-2 py-1 text-ink">
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  )
}
