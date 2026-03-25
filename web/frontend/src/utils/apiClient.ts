/**
 * Fetch-обёртка с JWT-аутентификацией.
 * Автоматически добавляет Authorization: Bearer к каждому запросу.
 * При 401 очищает токен и перенаправляет на главную страницу.
 */

const TOKEN_KEY = 'sphere_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

function authHeaders(extra?: Record<string, string>): Record<string, string> {
  const token = getToken()
  const headers: Record<string, string> = { ...extra }
  if (token) headers['Authorization'] = `Bearer ${token}`
  return headers
}

async function request(url: string, init: RequestInit): Promise<Response> {
  const res = await fetch(url, init)
  if (res.status === 401) {
    clearToken()
    window.dispatchEvent(new Event('auth:logout'))
  }
  return res
}

export const apiClient = {
  get: (url: string) =>
    request(url, { headers: authHeaders() }),

  post: (url: string, body?: unknown) =>
    request(url, {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    }),

  put: (url: string, body?: unknown) =>
    request(url, {
      method: 'PUT',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    }),

  delete: (url: string) =>
    request(url, { method: 'DELETE', headers: authHeaders() }),
}
