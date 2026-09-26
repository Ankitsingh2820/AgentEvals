import { describe, expect, it } from 'vitest'
import { enabledOptions, money, ms, pct, signedPct, signedPoints } from './format'

describe('money', () => {
  it('keeps precision for sub-cent costs instead of rounding to zero', () => {
    expect(money('0.0000734')).toBe('$0.0000734')
    expect(money(0.000012345)).toBe('$0.0000123')
  })
  it('uses fixed decimals for ordinary amounts', () => {
    expect(money(0.076)).toBe('$0.076')
    expect(money(123.4)).toBe('$123')
  })
  it('handles zero, missing and other currencies', () => {
    expect(money(0)).toBe('$0')
    expect(money(null)).toBe('—')
    expect(money('')).toBe('—')
    expect(money(1.5, 'EUR')).toBe('EUR 1.500')
  })
})

describe('durations and percentages', () => {
  it('formats latency across scales', () => {
    expect(ms(1.234)).toBe('1.23 ms')
    expect(ms(250)).toBe('250 ms')
    expect(ms(8700)).toBe('8.70 s')
    expect(ms(null)).toBe('—')
  })
  it('formats fractions and signed changes with explicit direction', () => {
    expect(pct(0.923)).toBe('92.3%')
    expect(signedPct(-61.84)).toBe('−61.8%')
    expect(signedPct(4)).toBe('+4.0%')
    expect(signedPct(0)).toBe('±0.0%')
    expect(signedPoints(-1)).toBe('−1.0 pts')
  })
})

describe('enabledOptions', () => {
  it('lists only switched-on optimizations', () => {
    expect(enabledOptions({
      parallel_tool_calls: true,
      dedupe_tool_calls: false,
      max_tool_calls: 4,
      routing: { mode: 'classifier' },
      prompt_caching: null,
    })).toEqual(['parallel_tool_calls', 'max_tool_calls=4', 'routing (classifier)'])
  })
})
