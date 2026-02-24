import { useState, useCallback } from 'react'
import { getToken, setToken, clearToken } from '../utils/apiClient'

export interface AuthUser {
  username: string
  role: 'admin' | 'viewer'
}

/**
 * Декодирует payload JWT-токена без верификации подписи.
 *
 * Args:
 *   token: JWT-строка в формате header.payload.signature.
 *
 * Returns:
 *   AuthUser при успехе или null при невалидном/просроченном токене.
 */
function decodeJwtPayload(token: string): AuthUser | null {
  try {
    const parts = token.split('.')
    if (parts.length !== 3) return null
    const padded = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const json = atob(padded)
    const payload = JSON.parse(json) as { sub?: string; role?: string; exp?: number }
    if (!payload.sub || !payload.role) return null
    if (payload.exp && payload.exp * 1000 < Date.now()) return null
    return { username: payload.sub, role: payload.role as 'admin' | 'viewer' }
  } catch {
    return null
  }
}

export interface AuthState {
  user: AuthUser | null
  isAuthenticated: boolean
  login: (username: string, password: string) => Promise<string | null>
  logout: () => void
}

/**
 * Хук управления аутентификацией пользователя.
 *
 * Инициализирует состояние из localStorage, предоставляет методы
 * login и logout. При успешном входе сохраняет JWT-токен и
 * декодирует данные пользователя из его payload.
 *
 * Returns:
 *   AuthState с текущим пользователем и методами управления сессией.
 */
export function useAuth(): AuthState {
  const [user, setUser] = useState<AuthUser | null>(() => {
    const token = getToken()
    return token ? decodeJwtPayload(token) : null
  })

  const login = useCallback(
    async (username: string, password: string): Promise<string | null> => {
      try {
        const res = await fetch('/api/auth/login', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ username, password }).toString(),
        })
        if (!res.ok) {
          const data = (await res.json().catch(() => ({}))) as { detail?: string }
          return data.detail ?? 'Ошибка входа'
        }
        const data = (await res.json()) as { access_token: string }
        setToken(data.access_token)
        setUser(decodeJwtPayload(data.access_token))
        return null  // null = успех
      } catch {
        return 'Ошибка сети'
      }
    },
    []
  )

  const logout = useCallback(() => {
    clearToken()
    setUser(null)
  }, [])

  return { user, isAuthenticated: user !== null, login, logout }
}
