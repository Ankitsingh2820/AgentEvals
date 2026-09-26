import { useEffect, useState, type ReactNode } from 'react'
import { api, ApiError, getApiKey, setApiKey, UNAUTHORIZED_EVENT } from '../api'
import { Button, ErrorBox } from './ui'

type State =
  | { kind: 'checking' }
  | { kind: 'signed-in'; name: string }
  | { kind: 'needs-key'; error?: string }
  | { kind: 'offline'; error: string }

/** Shows the app once the API accepts our key (or auth is disabled); otherwise a sign-in form. */
export function AuthGate({ children }: { children: (user: string, signOut: () => void) => ReactNode }) {
  const [state, setState] = useState<State>({ kind: 'checking' })
  const [draft, setDraft] = useState('')

  async function check(key: string | null, fromForm = false) {
    try {
      const who = await api.whoami(key)
      if (fromForm) setApiKey(key)
      setState({ kind: 'signed-in', name: who.name })
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setState({ kind: 'needs-key', error: fromForm ? 'That key was not accepted.' : undefined })
      } else {
        setState({ kind: 'offline', error: (e as Error).message })
      }
    }
  }

  useEffect(() => {
    // Initial check against the stored key (a network sync, so an effect is the right place).
    let active = true
    api.whoami(getApiKey())
      .then((who) => active && setState({ kind: 'signed-in', name: who.name }))
      .catch((e) => {
        if (!active) return
        if (e instanceof ApiError && e.status === 401) setState({ kind: 'needs-key' })
        else setState({ kind: 'offline', error: (e as Error).message })
      })
    const onUnauthorized = () => setState({ kind: 'needs-key', error: 'Your key was rejected or revoked.' })
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => {
      active = false
      window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    }
  }, [])

  function signOut() {
    setApiKey(null)
    setState({ kind: 'needs-key' })
  }

  if (state.kind === 'signed-in') return <>{children(state.name, signOut)}</>
  if (state.kind === 'checking') return <div className="p-8 text-sm text-muted">Connecting…</div>
  if (state.kind === 'offline') {
    return (
      <div className="mx-auto max-w-md p-8">
        <ErrorBox message={`Cannot reach the AgentEval API: ${state.error}`} />
      </div>
    )
  }
  return (
    <div className="mx-auto mt-24 max-w-md px-4">
      <form
        className="space-y-3 rounded-lg border border-line bg-surface p-6"
        onSubmit={(e) => {
          e.preventDefault()
          void check(draft.trim(), true)
        }}
      >
        <h1 className="text-lg font-semibold text-ink">Sign in to AgentEval</h1>
        <p className="text-sm text-ink-2">
          Paste an API key. Create one on the server with{' '}
          <code className="text-xs">python -m app.auth create "dashboard"</code>.
        </p>
        <input
          type="password"
          autoComplete="off"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="ae_…"
          aria-label="API key"
          className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm text-ink"
        />
        {state.error && <ErrorBox message={state.error} />}
        <Button type="submit" disabled={!draft.trim()}>Sign in</Button>
        <p className="text-xs text-muted">The key is kept in this browser only.</p>
      </form>
    </div>
  )
}
