import { defineConfig, devices } from '@playwright/test'

const PORT = Number(process.env.PW_PORT ?? '8767')
const BASE_URL = `http://127.0.0.1:${PORT}`

// В окружении репо часто заданы HTTP_PROXY/HTTPS_PROXY; для localhost это ломает WS/healthchecks.
for (const key of ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']) {
  if (process.env[key]) delete process.env[key]
}
process.env.NO_PROXY = process.env.NO_PROXY || '127.0.0.1,localhost'

export default defineConfig({
  testDir: './playwright',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    ...devices['Desktop Chrome'],
  },
  webServer: {
    command: `cd ../.. && ./web/start_e2e.sh ${PORT}`,
    url: `${BASE_URL}/`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      NO_PROXY: '127.0.0.1,localhost',
      HTTP_PROXY: '',
      HTTPS_PROXY: '',
      ALL_PROXY: '',
      http_proxy: '',
      https_proxy: '',
      all_proxy: '',
      PW_JWT_SECRET: process.env.PW_JWT_SECRET ?? 'playwright-secret-0123456789abcdef0123456789abcdef',
      PW_ADMIN_USER: process.env.PW_ADMIN_USER ?? 'pw_admin',
      PW_ADMIN_PASS: process.env.PW_ADMIN_PASS ?? 'pw_password',
      PW_USERS_DB: process.env.PW_USERS_DB ?? '',
    },
  },
})

