import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, type Agent, type ModelPrice, type ProviderModels, type ToolInfo } from '../api'
import { Button, Card, ErrorBox, Page } from '../components/ui'
import { useApi } from '../useApi'

const PROVIDER_LABEL: Record<string, string> = {
  groq: 'Groq',
  openai: 'OpenAI',
  anthropic: 'Anthropic (Claude)',
  mock: 'Mock (free, offline, fake answers)',
}
const PROVIDER_ORDER = ['groq', 'openai', 'anthropic', 'mock']
const DEFAULT_MODEL: Record<string, string> = {
  groq: 'openai/gpt-oss-20b',
  openai: 'gpt-4.1-mini',
  anthropic: 'claude-haiku-4-5',
  mock: 'mock-model',
}
const MOCK_MODELS = ['mock-model', 'mock-model-small']
const DEFAULT_PROMPT =
  'Use the company_lookup tool to research the company, then say what it does, its industry and approximate size.'

const inputCls = 'w-full rounded-md border border-line bg-surface px-3 py-1.5 text-sm text-ink'

function Field({ label, htmlFor, hint, children }: {
  label: string
  htmlFor?: string
  hint?: ReactNode
  children: ReactNode
}) {
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1 block text-sm font-medium text-ink">{label}</label>
      {children}
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  )
}

export function AgentNew() {
  const navigate = useNavigate()
  const providers = useApi<string[]>('/providers')
  const tools = useApi<ToolInfo[]>('/tools')
  const prices = useApi<ModelPrice[]>('/pricing/models')

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [provider, setProvider] = useState('mock')
  const [model, setModel] = useState(DEFAULT_MODEL.mock)
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT)
  const [selectedTools, setSelectedTools] = useState<string[]>(['company_lookup'])
  const [maxTokens, setMaxTokens] = useState('2000')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Models the provider's key can use (free call). Not available for the offline mock, and
  // when a key isn't configured; typing a model name always works.
  const listed = useApi<ProviderModels>(provider === 'mock' ? null : `/providers/${provider}/models`)
  const available = listed.data?.provider === provider ? listed.data.models : []
  const listError = provider !== 'mock' && listed.error && !listed.loading ? listed.error : null

  const pricedForProvider = new Set(
    (prices.data ?? []).filter((p) => p.provider === provider).map((p) => p.model),
  )
  const suggestions = provider === 'mock'
    ? MOCK_MODELS
    : [...available.filter((m) => pricedForProvider.has(m)), ...available.filter((m) => !pricedForProvider.has(m))]
  const providerList = PROVIDER_ORDER.filter((p) => providers.data?.includes(p))
  const modelName = model.trim()
  const isPriced = pricedForProvider.has(modelName)

  function chooseProvider(p: string) {
    setProvider(p)
    setModel(DEFAULT_MODEL[p] ?? '')
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (!name.trim()) return setError('Give the agent a name.')
    if (!modelName) return setError('Choose or type a model.')
    setBusy(true)
    try {
      const agent = await api.post<Agent>('/agents', {
        name: name.trim(),
        description: description.trim() || null,
        provider,
        model: modelName,
        system_prompt: prompt,
        tools: selectedTools,
        max_tokens: Number(maxTokens) || 2000,
      })
      navigate(`/agents/${agent.id}`)
    } catch (err) {
      setError((err as Error).message)
      setBusy(false)
    }
  }

  return (
    <Page
      title="Create agent"
      subtitle="An agent is a model, a system prompt and the tools it may call. Changing it later creates a new version."
    >
      <form onSubmit={submit} className="max-w-3xl space-y-5">
        <Card>
          <div className="space-y-4">
            <Field label="Name" htmlFor="agent-name">
              <input id="agent-name" className={inputCls} value={name} maxLength={200}
                placeholder="e.g. Research Agent (Groq)" onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Description (optional)" htmlFor="agent-description">
              <input id="agent-description" className={inputCls} value={description}
                onChange={(e) => setDescription(e.target.value)} />
            </Field>
          </div>
        </Card>

        <Card title="Model">
          <div className="space-y-4">
            <Field label="Provider" htmlFor="agent-provider"
              hint={provider === 'mock'
                ? 'The mock model needs no key and gives fake answers: good for trying the platform for free.'
                : `Uses the ${PROVIDER_LABEL[provider]} key from your .env file.`}>
              <select id="agent-provider" className={inputCls} value={provider}
                onChange={(e) => chooseProvider(e.target.value)}>
                {(providerList.length ? providerList : [provider]).map((p) => (
                  <option key={p} value={p}>{PROVIDER_LABEL[p] ?? p}</option>
                ))}
              </select>
            </Field>
            <Field label="Model" htmlFor="agent-model"
              hint={
                provider === 'mock' ? null
                  : !modelName ? null
                    : isPriced
                      ? 'Price on file: costs will be estimated.'
                      : 'No price on file for this model, so its cost will show as "unpriced". Runs still work.'
              }>
              <input id="agent-model" className={inputCls} value={model} list="agent-model-options"
                autoComplete="off" onChange={(e) => setModel(e.target.value)} />
              <datalist id="agent-model-options">
                {suggestions.map((m) => <option key={m} value={m} />)}
              </datalist>
              {provider !== 'mock' && listed.loading && (
                <p className="mt-1 text-xs text-muted">Loading the models your key can use…</p>
              )}
              {listError && (
                <p className="mt-1 text-xs text-ink-2">
                  Couldn't list models ({listError}). You can still type a model name.
                </p>
              )}
              {!listError && suggestions.length > 0 && provider !== 'mock' && (
                <p className="mt-1 text-xs text-muted">
                  {suggestions.length} models available. Start typing to filter; priced models come first.
                </p>
              )}
            </Field>
            <Field label="Max output tokens" htmlFor="agent-max-tokens"
              hint="Upper limit on each reply. Lower means cheaper and shorter.">
              <input id="agent-max-tokens" type="number" min={1} max={128000} className={`${inputCls} max-w-40`}
                value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} />
            </Field>
          </div>
        </Card>

        <Card title="Instructions and tools">
          <div className="space-y-4">
            <Field label="System prompt" htmlFor="agent-prompt">
              <textarea id="agent-prompt" rows={5} className={inputCls} value={prompt}
                onChange={(e) => setPrompt(e.target.value)} />
            </Field>
            <fieldset>
              <legend className="mb-1 text-sm font-medium text-ink">Tools the agent may call</legend>
              <div className="space-y-2">
                {(tools.data ?? []).map((t) => (
                  <label key={t.name} className="flex items-start gap-2 text-sm">
                    <input type="checkbox" className="mt-1" checked={selectedTools.includes(t.name)}
                      onChange={(e) => setSelectedTools(
                        e.target.checked ? [...selectedTools, t.name] : selectedTools.filter((x) => x !== t.name),
                      )} />
                    <span>
                      <span className="font-medium text-ink">{t.name}</span>
                      <span className="block text-xs text-muted">{t.description}</span>
                    </span>
                  </label>
                ))}
                {tools.data?.length === 0 && <p className="text-xs text-muted">No tools available.</p>}
              </div>
            </fieldset>
          </div>
        </Card>

        {error && <ErrorBox message={error} />}
        <div className="flex gap-2">
          <Button type="submit" disabled={busy}>{busy ? 'Creating…' : 'Create agent'}</Button>
          <Button variant="secondary" onClick={() => navigate('/agents')} disabled={busy}>Cancel</Button>
        </div>
      </form>
    </Page>
  )
}
