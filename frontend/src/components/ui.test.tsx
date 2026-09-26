import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { Badge, Delta } from './ui'

afterEach(cleanup)

describe('Badge', () => {
  it('never relies on color alone: icon and label are always rendered', () => {
    for (const [status, icon] of [['PASS', '✓'], ['FAIL', '✕'], ['INCONCLUSIVE', '?']]) {
      const { container } = render(<Badge status={status} />)
      expect(container.textContent).toBe(`${icon}${status}`)
      cleanup()
    }
  })
  it('renders a dash for a missing status', () => {
    render(<Badge status={null} />)
    expect(screen.getByText('—')).toBeTruthy()
  })
})

describe('Delta', () => {
  it('marks a cost decrease as good, with an arrow', () => {
    const { container } = render(<Delta value={-12} text="−12%" goodWhen="down" />)
    const span = container.firstElementChild!
    expect(span.textContent).toContain('↓')
    expect(span.className).toContain('text-good-text')
  })
  it('marks a quality decrease as bad', () => {
    const { container } = render(<Delta value={-2} text="−2 pts" goodWhen="up" />)
    expect(container.firstElementChild!.className).toContain('text-critical-text')
  })
  it('stays neutral when direction carries no judgement or there is no baseline', () => {
    const neutral = render(<Delta value={5} text="+5" goodWhen="neutral" />)
    expect(neutral.container.firstElementChild!.className).toContain('text-ink-2')
    cleanup()
    render(<Delta value={null} text="" goodWhen="up" />)
    expect(screen.getByText('no comparison')).toBeTruthy()
  })
})
