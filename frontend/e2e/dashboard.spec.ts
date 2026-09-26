import { expect, test, type APIRequestContext } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// The whole optimization loop, clicked through the real UI:
// sign in → overview → agent → analyze → apply (+ experiment) → run → verdict →
// inspect a run → reproduce → sign out. Data is created through the API first, using
// the offline mock provider, so the test is deterministic and costs nothing.

const KEY = process.env.E2E_API_KEY ?? ''
const here = dirname(fileURLToPath(import.meta.url))
const DATASET = readFileSync(resolve(here, '../../backend/datasets/company_research.jsonl'))
const stamp = Date.now().toString(36)
const names = {
  agent: `E2E caching agent ${stamp}`,
  dataset: `e2e-company-research-${stamp}`,
  evaluator: `e2e-mentions-${stamp}`,
}

test.skip(!KEY, 'set E2E_API_KEY to run end-to-end tests')
test.describe.configure({ mode: 'serial' })

async function api(request: APIRequestContext, method: 'get' | 'post', path: string, data?: unknown,
  contentType = 'application/json') {
  const res = await request.fetch(`/api${path}`, {
    method,
    headers: { authorization: `Bearer ${KEY}`, 'content-type': contentType },
    data: data as never,
  })
  expect(res.ok(), `${method.toUpperCase()} ${path}: ${res.status()} ${await res.text()}`).toBeTruthy()
  return res.json()
}

test.beforeAll(async ({ request }) => {
  // A long system prompt makes each call's prefix cacheable, so the analyzer has real
  // evidence for a prompt-caching recommendation.
  const agent = await api(request, 'post', '/agents', {
    name: names.agent,
    provider: 'mock',
    model: 'mock-model',
    system_prompt: 'You research companies carefully. '.repeat(400),
    tools: ['company_lookup'],
  })
  for (const company of ['Acme Corp', 'Globex Analytics', 'Initech Health']) {
    await api(request, 'post', '/runs', { agent_id: agent.id, input: company })
  }
  await api(request, 'post', `/datasets/jsonl?name=${names.dataset}`, DATASET, 'application/x-ndjson')
  await api(request, 'post', '/evaluators', {
    name: names.evaluator, type: 'contains', config: { labels_key: 'must_mention' },
  })
})

test('optimization loop through the dashboard', async ({ page }) => {
  // Sign in
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Sign in to AgentEval' })).toBeVisible()
  await page.getByLabel('API key').fill('ae_definitely-wrong-key-000000')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByText('That key was not accepted.')).toBeVisible()
  await page.getByLabel('API key').fill(KEY)
  await page.getByRole('button', { name: 'Sign in' }).click()

  // Overview: KPI tiles and a chart's table view
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
  await expect(page.getByText('Agent runs', { exact: true })).toBeVisible() // the KPI tile
  const runsCard = page.locator('section', { hasText: 'Runs per day' })
  await runsCard.getByRole('button', { name: 'table' }).click()
  await expect(runsCard.getByRole('columnheader', { name: 'Date' })).toBeVisible()

  // Agent → analyze runs → recommendation with evidence
  await page.getByRole('link', { name: 'Agents' }).click()
  await page.getByRole('link', { name: names.agent }).click()
  await expect(page.getByRole('heading', { name: names.agent })).toBeVisible()
  await page.getByRole('button', { name: 'Analyze runs' }).click()
  const rec = page.locator('div.rounded-md', { hasText: 'Enable prompt caching for multi-turn runs' })
  await expect(rec).toBeVisible()
  await expect(rec.getByText('How estimated:')).toBeVisible()

  // Apply it as a candidate version, with an experiment against the analyzed version
  await rec.getByRole('button', { name: 'Apply as candidate…' }).click()
  await rec.getByLabel('Dataset').selectOption({ label: `${names.dataset} v1 (5 cases)` })
  await rec.getByLabel(new RegExp(names.evaluator)).check()
  await rec.getByLabel('Repetitions').fill('2')
  await rec.getByRole('button', { name: 'Create version + experiment' }).click()

  // Experiment page: only the options changed; run it and wait for the verdict
  await expect(page.getByRole('heading', { name: /Enable prompt caching/ })).toBeVisible()
  await expect(page.locator('section', { hasText: 'What changed' }).getByText('options', { exact: true }))
    .toBeVisible()
  await page.getByRole('button', { name: 'Run experiment' }).click()
  await expect(page.getByText('The candidate meets every acceptance criterion with enough evidence.'))
    .toBeVisible({ timeout: 60_000 })
  const comparison = page.locator('section', { hasText: 'Baseline vs candidate' })
  await expect(comparison.getByText('10 paired runs', { exact: false })).toBeVisible()
  await expect(comparison.getByRole('cell', { name: 'Tokens per run' })).toBeVisible()
  const experimentUrl = page.url()

  // Inspect one of the experiment's runs: timeline and a tool step
  await page.locator('section', { hasText: 'Per case' }).getByRole('link').first().click()
  await expect(page.getByRole('heading', { name: /^Run / })).toBeVisible()
  await page.getByRole('listitem').filter({ hasText: 'tool · company_lookup' }).first().click()
  const detail = page.locator('section', { hasText: 'Step detail' })
  await expect(detail.getByText('company_lookup', { exact: true })).toBeVisible()
  await expect(detail.getByText('yes', { exact: true })).toBeVisible()

  // Reproduce: a new experiment with the identical pinned configuration
  await page.goto(experimentUrl)
  await page.getByRole('button', { name: 'Reproduce' }).click()
  await expect(page.getByText('reproduction of')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Run experiment' })).toBeVisible()

  // Sign out
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page.getByRole('heading', { name: 'Sign in to AgentEval' })).toBeVisible()
})
