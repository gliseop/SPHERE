import { test, expect, type APIRequestContext } from '@playwright/test'

const ADMIN_USER = process.env.PW_ADMIN_USER ?? 'pw_admin'
const ADMIN_PASS = process.env.PW_ADMIN_PASS ?? 'pw_password'

async function apiLogin(request: APIRequestContext): Promise<string> {
  const res = await request.post('/api/auth/login', {
    form: { username: ADMIN_USER, password: ADMIN_PASS },
  })
  expect(res.ok()).toBeTruthy()
  const json = (await res.json()) as { access_token: string }
  expect(json.access_token).toBeTruthy()
  return json.access_token
}

async function openAuthedApp(page, request: APIRequestContext): Promise<string> {
  const token = await apiLogin(request)
  await page.addInitScript((t) => localStorage.setItem('magistry_token', t), token)
  await page.goto('/')
  await expect(page.locator('.hud-header')).toBeVisible()
  return token
}

test('login UI: success + error', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'MAGISTRY' })).toBeVisible()

  // wrong password
  await page.locator('input[autocomplete="username"]').fill(ADMIN_USER)
  await page.locator('input[autocomplete="current-password"]').fill('wrong_password')
  await page.getByRole('button', { name: 'Войти' }).click()
  await expect(page.getByText('Неверный логин или пароль')).toBeVisible()

  // success
  await page.locator('input[autocomplete="current-password"]').fill(ADMIN_PASS)
  await page.getByRole('button', { name: 'Войти' }).click()
  await expect(page.locator('.hud-header')).toBeVisible()
  await expect(page.locator('.hud-header-stats')).toContainText(ADMIN_USER)
})

test('header metrics only on monitor', async ({ page, request }) => {
  await openAuthedApp(page, request)

  const headerStats = page.locator('.hud-header-stats')
  await expect(headerStats.getByText('Событий')).toBeVisible()

  await page.getByRole('button', { name: 'Сценарии' }).click()
  await expect(headerStats.getByText('Событий')).toHaveCount(0)

  await page.getByRole('button', { name: 'Монитор' }).click()
  await expect(headerStats.getByText('Событий')).toBeVisible()
})

test('delete run via UI (runner not selectable)', async ({ page, request }) => {
  const token = await openAuthedApp(page, request)

  await page.getByRole('button', { name: /^Прогоны/ }).click()

  const launchPanel = page.locator('.runs-launch-panel')
  await expect(launchPanel).toBeVisible()
  await expect(launchPanel.getByText('Runner')).toHaveCount(0)
  await expect(launchPanel.getByText('Mock')).toHaveCount(0)

  // Arrange: create a tiny mock run (fast, deterministic) via API.
  const launch = await request.post('/api/runs/launch', {
    headers: { Authorization: `Bearer ${token}` },
    data: { scenario: 'S1', governance: 'G1', seed: '', runner: 'mock', rounds: 1 },
  })
  expect(launch.ok()).toBeTruthy()
  const { run_name } = (await launch.json()) as { run_name: string }
  expect(run_name).toMatch(/^S1_G1_seed\d+_mock$/)

  // Wait until the run appears in the table (poll refreshRuns).
  await expect(page.locator('td.runs-name-cell', { hasText: run_name })).toBeVisible({ timeout: 30_000 })

  // Delete it via UI
  page.once('dialog', (d) => d.accept())
  const row = page.locator('tbody tr', { hasText: run_name })
  await row.locator('button[title="Удалить"]').click()

  await expect(page.locator('tbody tr', { hasText: run_name })).toHaveCount(0, { timeout: 30_000 })
})

test('playback websocket closes and UI returns to idle', async ({ page, request }) => {
  const token = await apiLogin(request)

  // Arrange: create a tiny run first.
  const launch = await request.post('/api/runs/launch', {
    headers: { Authorization: `Bearer ${token}` },
    data: { scenario: 'S1', governance: 'G1', seed: 314159, runner: 'mock', rounds: 1 },
  })
  expect(launch.ok()).toBeTruthy()
  const { run_name } = (await launch.json()) as { run_name: string }

  // Open app with token
  await page.addInitScript((t) => localStorage.setItem('magistry_token', t), token)
  await page.goto('/')
  await expect(page.locator('.hud-header')).toBeVisible()

  // Start playback from Runs tab (stable: full run name is shown).
  await page.getByRole('button', { name: /^Прогоны/ }).click()
  const row = page.locator('tbody tr', { hasText: run_name })
  await expect(row).toBeVisible({ timeout: 30_000 })
  await row.locator('button[title="Воспроизвести"]').click()

  // While playback is active, mode indicator should be visible.
  await expect(page.locator('.mode-indicator')).toBeVisible({ timeout: 10_000 })
  // After server sends done+close, hook should return to idle and hide indicator.
  await expect(page.locator('.mode-indicator')).toHaveCount(0, { timeout: 30_000 })

  // Cleanup
  const del = await request.delete(`/api/runs/${run_name}`, { headers: { Authorization: `Bearer ${token}` } })
  expect(del.status()).toBe(204)
})
