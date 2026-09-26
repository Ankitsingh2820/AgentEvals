import { useEffect, useState, type ReactNode } from 'react'
import { NavLink } from 'react-router-dom'

const NAV = [
  { to: '/', label: 'Overview', end: true },
  { to: '/agents', label: 'Agents' },
  { to: '/evaluations', label: 'Evaluations' },
  { to: '/experiments', label: 'Experiments' },
]

type Theme = 'system' | 'light' | 'dark'

function readTheme(): Theme {
  try {
    const t = localStorage.getItem('agenteval-theme')
    return t === 'light' || t === 'dark' ? t : 'system'
  } catch {
    return 'system'
  }
}

export function Layout({ children, user, onSignOut }: {
  children: ReactNode
  user: string
  onSignOut: () => void
}) {
  const [theme, setTheme] = useState<Theme>(readTheme)

  useEffect(() => {
    const root = document.documentElement
    if (theme === 'system') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
    try {
      localStorage.setItem('agenteval-theme', theme)
    } catch {
      /* storage unavailable: theme still applies for this visit */
    }
  }, [theme])

  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
          <span className="font-semibold text-ink">AgentEval</span>
          <nav className="flex flex-wrap gap-1 text-sm">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end}
                className={({ isActive }) =>
                  `rounded-md px-2.5 py-1 ${isActive ? 'bg-surface-2 font-medium text-ink' : 'text-ink-2 hover:text-ink'}`}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <span className="ml-auto text-xs text-ink-2">
            {user === 'anonymous' ? 'auth disabled' : <>key <span className="text-ink">{user}</span></>}
            {user !== 'anonymous' && (
              <button onClick={onSignOut} className="ml-2 text-series-1 hover:underline">Sign out</button>
            )}
          </span>
          <label className="flex items-center gap-2 text-xs text-ink-2">
            Theme
            <select value={theme} onChange={(e) => setTheme(e.target.value as Theme)}
              className="rounded border border-line bg-surface px-1.5 py-0.5 text-ink">
              <option value="system">System</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
          </label>
        </div>
      </header>
      <main>{children}</main>
    </div>
  )
}
