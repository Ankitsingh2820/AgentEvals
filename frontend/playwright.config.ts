import { defineConfig } from '@playwright/test'

// End-to-end tests drive the real dashboard against a running stack
// (`docker compose up`). Required env:
//   E2E_API_KEY    an AgentEval API key for that stack
// Optional:
//   E2E_BASE_URL   dashboard URL (default http://localhost:3000)
//   PW_CHANNEL     use an installed browser, e.g. "chrome" (no browser download needed)
export default defineConfig({
  testDir: './e2e',
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:3000',
    channel: process.env.PW_CHANNEL || undefined,
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
})
