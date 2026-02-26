import { useState } from 'react'
import { APP_NAME } from '../constants'

interface Props {
  onLogin: (username: string, password: string) => Promise<string | null>
}

export function LoginPage({ onLogin }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    const err = await onLogin(username, password)
    setLoading(false)
    if (err) setError(err)
  }

  return (
    <div className="login-page">
      <div className="login-card hud-panel">
        <div className="corner tl accent" />
        <div className="corner tr" />
        <div className="corner bl" />
        <div className="corner br accent" />
        <h1 className="login-title">{APP_NAME}</h1>
        <form onSubmit={handleSubmit} className="login-form">
          <div className="form-field">
            <label>Имя пользователя</label>
            <input
              className="hud-input"
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
              required
            />
          </div>
          <div className="form-field">
            <label>Пароль</label>
            <input
              className="hud-input"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </div>
          {error && (
            <div className="badge danger" style={{ textAlign: 'center', padding: '0.5rem' }}>
              {error}
            </div>
          )}
          <button
            className="btn-clipped primary full-width"
            type="submit"
            disabled={loading || !username || !password}
          >
            {loading ? 'Вход...' : 'Войти'}
          </button>
        </form>
      </div>
    </div>
  )
}
