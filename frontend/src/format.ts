const dash = '—'

export function num(v: number | null | undefined, digits = 0): string {
  if (v == null || Number.isNaN(v)) return dash
  return v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits })
}

export function compact(v: number | null | undefined): string {
  if (v == null) return dash
  return Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(v)
}

/** Costs can be fractions of a cent: keep 3 significant digits instead of rounding to 0. */
export function money(v: number | string | null | undefined, currency = 'USD'): string {
  if (v == null || v === '') return dash
  const n = typeof v === 'string' ? Number(v) : v
  if (Number.isNaN(n)) return dash
  const symbol = currency === 'USD' ? '$' : `${currency} `
  if (n === 0) return `${symbol}0`
  if (Math.abs(n) >= 0.01) return `${symbol}${n.toFixed(Math.abs(n) >= 100 ? 0 : 3)}`
  return `${symbol}${Number(n.toPrecision(3))}`
}

export function ms(v: number | null | undefined): string {
  if (v == null) return dash
  if (v >= 1000) return `${(v / 1000).toFixed(v >= 10000 ? 1 : 2)} s`
  if (v >= 10) return `${v.toFixed(0)} ms`
  return `${v.toFixed(2)} ms`
}

export function pct(v: number | null | undefined, digits = 1): string {
  return v == null ? dash : `${(v * 100).toFixed(digits)}%`
}

/** A change already expressed in % (e.g. -61.8). */
export function signedPct(v: number | null | undefined, digits = 1): string {
  if (v == null) return dash
  return `${v > 0 ? '+' : v < 0 ? '−' : '±'}${Math.abs(v).toFixed(digits)}%`
}

export function signedPoints(v: number | null | undefined, digits = 1): string {
  if (v == null) return dash
  return `${v > 0 ? '+' : v < 0 ? '−' : '±'}${Math.abs(v).toFixed(digits)} pts`
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return dash
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function shortDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function shortId(id: string | null | undefined): string {
  return id ? id.slice(0, 8) : dash
}

/** Human-readable list of the optimization options switched on for a version. */
export function enabledOptions(options: Record<string, unknown>): string[] {
  return Object.entries(options)
    .filter(([, v]) => v !== null && v !== false && v !== undefined)
    .map(([k, v]) => (k === 'routing' ? `routing (${(v as { mode: string }).mode})` : k === 'max_tool_calls' ? `max_tool_calls=${v}` : k))
}
