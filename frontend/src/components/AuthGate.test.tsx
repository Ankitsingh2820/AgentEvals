import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getApiKey, setApiKey } from '../api'
import { AuthGate } from './AuthGate'

const GOOD = 'ae_good-key-0123456789'

function mockApi() {
  const calls: { url: string; auth: string | null }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const headers = (init?.headers ?? {}) as Record<string, string>
    const auth = headers.authorization ?? null
    calls.push({ url, auth })
    if (auth === `Bearer ${GOOD}`) {
      return new Response(JSON.stringify({ id: 'k1', name: 'dashboard' }), { status: 200 })
    }
    return new Response(JSON.stringify({ detail: 'missing or invalid API key' }), { status: 401 })
  }))
  return calls
}

function app() {
  return render(<AuthGate>{(user) => <p>signed in as {user}</p>}</AuthGate>)
}

beforeEach(() => setApiKey(null))
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('AuthGate', () => {
  it('asks for a key when the API rejects the request', async () => {
    mockApi()
    app()
    expect(await screen.findByText('Sign in to AgentEval')).toBeTruthy()
  })

  it('signs in with a valid key, stores it and sends it as a bearer token', async () => {
    const calls = mockApi()
    app()
    fireEvent.change(await screen.findByLabelText('API key'), { target: { value: ` ${GOOD} ` } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('signed in as dashboard')).toBeTruthy()
    expect(getApiKey()).toBe(GOOD) // trimmed
    expect(calls.at(-1)).toEqual({ url: '/api/auth/whoami', auth: `Bearer ${GOOD}` })
  })

  it('rejects a wrong key without storing it', async () => {
    mockApi()
    app()
    fireEvent.change(await screen.findByLabelText('API key'), { target: { value: 'ae_wrong' } })
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByText('That key was not accepted.')).toBeTruthy()
    expect(getApiKey()).toBeNull()
  })

  it('uses a stored key on load', async () => {
    mockApi()
    setApiKey(GOOD)
    app()
    await waitFor(() => expect(screen.getByText('signed in as dashboard')).toBeTruthy())
  })

  it('reports an unreachable API instead of asking for a key', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch') }))
    app()
    expect(await screen.findByText(/Cannot reach the AgentEval API/)).toBeTruthy()
  })
})
