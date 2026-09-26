import { useNavigate } from 'react-router-dom'
import type { Agent } from '../api'
import { A, Button, Card, ErrorBox, Loading, Page, Table } from '../components/ui'
import { dateTime, enabledOptions } from '../format'
import { useApi } from '../useApi'

export function Agents() {
  const { data, error } = useApi<Agent[]>('/agents')
  const navigate = useNavigate()
  return (
    <Page
      title="Agents"
      subtitle="Each configuration change creates a new immutable version."
      actions={<Button onClick={() => navigate('/agents/new')}>Create agent</Button>}
    >
      {error && <ErrorBox message={error} />}
      {!data && !error && <Loading />}
      {data && (
        <Card>
          <Table
            rows={data}
            rowKey={(a) => a.id}
            empty="No agents yet. Click Create agent to make your first one."
            columns={[
              { header: 'Agent', cell: (a) => <A to={`/agents/${a.id}`}>{a.name}</A> },
              { header: 'Version', align: 'right', cell: (a) => `v${a.latest_version.version}` },
              { header: 'Model', cell: (a) => <code className="text-xs">{a.latest_version.provider}/{a.latest_version.model}</code> },
              { header: 'Tools', cell: (a) => a.latest_version.tools.join(', ') || '—' },
              { header: 'Optimizations', cell: (a) => enabledOptions(a.latest_version.options).join(', ') || '—' },
              { header: 'Created', cell: (a) => dateTime(a.created_at) },
            ]}
          />
        </Card>
      )}
    </Page>
  )
}
