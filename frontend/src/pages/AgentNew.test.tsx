import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AgentNew } from './AgentNew'

interface Call { method: string; path: string; body?: Record<string, unknown> }

/** A fake API: records calls and answers the endpoints the form uses. */
function mockApi(opts: { models?: string[] | 'missing-key'; createStatus?: number } = {}) {
  const calls: Call[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    const path = url.replace(/^\/api/, '')
    const method = init?.method ?? 'GET'
    const body = init?.body ? JSON.parse(String(init.body)) : undefined
    calls.push({ method, path, body })
    const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status })
    if (path === '/providers') return json(['anthropic', 'groq', 'mock', 'openai'])
    if (path === '/tools') {
      return json([
        { name: 'calculator', description: 'Evaluate arithmetic' },
        { name: 'company_lookup', description: 'Look up a company' },
      ])
    }
    if (path === '/pricing/models') {
      return json([
        { provider: 'groq', model: 'openai/gpt-oss-20b' },
        { provider: 'groq', model: 'openai/gpt-oss-120b' },
      ])
    }
    if (path === '/providers/groq/models') {
      if (opts.models === 'missing-key') {
        return json({ detail: 'GROQ_API_KEY is not set: add it to .env and restart' }, 502)
      }
      return json({ provider: 'groq', models: opts.models ?? ['allam-2-7b', 'openai/gpt-oss-120b', 'openai/gpt-oss-20b'] })
    }
    if (path === '/agents' && method === 'POST') {
      if (opts.createStatus === 422) return json({ detail: 'an agent with this name already exists' }, 409)
      return json({ id: 'new-agent-id' }, 201)
    }
    return json({ detail: 'not found' }, 404)
  }))
  return calls
}

function app() {
  return render(
    <MemoryRouter initialEntries={['/agents/new']}>
      <Routes>
        <Route path="/agents/new" element={<AgentNew />} />
        <Route path="/agents/:id" element={<p>agent page</p>} />
        <Route path="/agents" element={<p>agents list</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

const optionValues = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('datalist option')).map((o) => (o as HTMLOptionElement).value)

beforeEach(() => vi.stubGlobal('scrollTo', () => {}))
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('AgentNew form', () => {
  it('creates a mock agent with the fields it shows and opens the new agent', async () => {
    const calls = mockApi()
    app()
    fireEvent.change(await screen.findByLabelText('Name', { exact: true }), { target: { value: '  My agent  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create agent' }))

    expect(await screen.findByText('agent page')).toBeTruthy()
    const post = calls.find((c) => c.method === 'POST')!
    expect(post.path).toBe('/agents')
    expect(post.body).toMatchObject({
      name: 'My agent', // trimmed
      description: null,
      provider: 'mock',
      model: 'mock-model',
      tools: ['company_lookup'],
      max_tokens: 2000,
    })
  })

  it('lists the tools from the API and lets you change the selection', async () => {
    const calls = mockApi()
    app()
    const calc = await screen.findByLabelText(/calculator/)
    expect((calc as HTMLInputElement).checked).toBe(false)
    fireEvent.click(calc)
    fireEvent.click(screen.getByLabelText(/company_lookup/)) // untick the default
    fireEvent.change(screen.getByLabelText('Name', { exact: true }), { target: { value: 'a' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create agent' }))
    await screen.findByText('agent page')
    expect(calls.find((c) => c.method === 'POST')!.body!.tools).toEqual(['calculator'])
  })

  it('offers the models a real key can use, priced ones first, and says whether a price exists', async () => {
    mockApi()
    const { container } = app()
    fireEvent.change(await screen.findByLabelText('Provider'), { target: { value: 'groq' } })

    await waitFor(() => expect(optionValues(container).length).toBe(3))
    expect(optionValues(container)).toEqual(['openai/gpt-oss-120b', 'openai/gpt-oss-20b', 'allam-2-7b'])
    expect((screen.getByLabelText('Model') as HTMLInputElement).value).toBe('openai/gpt-oss-20b')
    expect(screen.getByText('Price on file: costs will be estimated.')).toBeTruthy()

    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'allam-2-7b' } })
    expect(screen.getByText(/No price on file for this model/)).toBeTruthy()
  })

  it('still lets you type a model when the key is not configured', async () => {
    const calls = mockApi({ models: 'missing-key' })
    app()
    fireEvent.change(await screen.findByLabelText('Provider'), { target: { value: 'groq' } })
    expect(await screen.findByText(/Couldn't list models/)).toBeTruthy()
    expect(screen.getByText(/GROQ_API_KEY is not set/)).toBeTruthy()

    fireEvent.change(screen.getByLabelText('Name', { exact: true }), { target: { value: 'typed model' } })
    fireEvent.change(screen.getByLabelText('Model'), { target: { value: 'llama-x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create agent' }))
    await screen.findByText('agent page')
    expect(calls.find((c) => c.method === 'POST')!.body).toMatchObject({ provider: 'groq', model: 'llama-x' })
  })

  it('validates before sending and shows server errors without leaving the form', async () => {
    const calls = mockApi({ createStatus: 422 })
    app()
    fireEvent.click(await screen.findByRole('button', { name: 'Create agent' }))
    expect((await screen.findByRole('alert')).textContent).toContain('Give the agent a name.')
    expect(calls.some((c) => c.method === 'POST')).toBe(false)

    fireEvent.change(screen.getByLabelText('Name', { exact: true }), { target: { value: 'dup' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create agent' }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('already exists'))
    expect(screen.queryByText('agent page')).toBeNull() // stayed on the form
    expect((screen.getByRole('button', { name: 'Create agent' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('cancel returns to the agents list', async () => {
    mockApi()
    app()
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
    expect(await screen.findByText('agents list')).toBeTruthy()
  })
})
