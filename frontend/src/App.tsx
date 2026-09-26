import { lazy, Suspense } from 'react'
import { Route, Routes } from 'react-router-dom'
import { AuthGate } from './components/AuthGate'
import { Layout } from './components/Layout'
import { Loading, Page } from './components/ui'

// Pages load on demand, so the charting library is fetched only by pages that chart.
const Overview = lazy(() => import('./pages/Overview').then((m) => ({ default: m.Overview })))
const Agents = lazy(() => import('./pages/Agents').then((m) => ({ default: m.Agents })))
const AgentNew = lazy(() => import('./pages/AgentNew').then((m) => ({ default: m.AgentNew })))
const AgentDetail = lazy(() => import('./pages/AgentDetail').then((m) => ({ default: m.AgentDetail })))
const RunDetail = lazy(() => import('./pages/RunDetail').then((m) => ({ default: m.RunDetail })))
const Evaluations = lazy(() => import('./pages/Evaluations').then((m) => ({ default: m.Evaluations })))
const EvaluationDetail = lazy(() => import('./pages/Evaluations').then((m) => ({ default: m.EvaluationDetail })))
const Experiments = lazy(() => import('./pages/Experiments').then((m) => ({ default: m.Experiments })))
const ExperimentDetail = lazy(() => import('./pages/Experiments').then((m) => ({ default: m.ExperimentDetail })))

export default function App() {
  return (
    <AuthGate>
      {(user, signOut) => (
        <Layout user={user} onSignOut={signOut}>
          <Suspense fallback={<Loading />}>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/agents" element={<Agents />} />
              <Route path="/agents/new" element={<AgentNew />} />
              <Route path="/agents/:id" element={<AgentDetail />} />
              <Route path="/runs/:id" element={<RunDetail />} />
              <Route path="/evaluations" element={<Evaluations />} />
              <Route path="/evaluations/:id" element={<EvaluationDetail />} />
              <Route path="/experiments" element={<Experiments />} />
              <Route path="/experiments/:id" element={<ExperimentDetail />} />
              <Route path="*" element={<Page title="Not found">This page does not exist.</Page>} />
            </Routes>
          </Suspense>
        </Layout>
      )}
    </AuthGate>
  )
}
