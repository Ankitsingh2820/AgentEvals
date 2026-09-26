import type { Agent } from '../api'
import { A, Card, ErrorBox, Loading, Page, Table } from '../components/ui'
import { dateTime, enabledOptions } from '../format'
import { useApi } from '../useApi'

export function Agents() {
  const { data, error } = useApi<Agent[]>('/agents')
  return (
    <Page title="Agents" subtitle="Each configuration change creates a new immutable version.">
      {error && <ErrorBox message={error} />}
      {!data && !error && <Loading />}
      {data && (
        <Card>
          <Table
            rows={data}
            rowKey={(a) => a.id}
            empty="No agents yet. Create one with POST /agents."
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
