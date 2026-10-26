
import { useEffect, useRef, useState } from 'react'
import { login } from '../auth/authClient.js'
import { Link, useNavigate } from 'react-router-dom'

function LoginPage({ onLogin, onLoginRecovery }) {
  const navigate = useNavigate()
  const attempt = useRef(0)
  const pending = useRef(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => () => { attempt.current += 1 }, [])

  async function handleSubmit(event) {
    event.preventDefault()
    if (pending.current) return
    pending.current = true
    const currentAttempt = ++attempt.current
    setError('')
    setLoading(true)

    try {
      const result = await login(email, password)
      if (attempt.current !== currentAttempt) return
      if (result.csrfReady) onLogin(result.user)
      else onLoginRecovery()
      navigate('/applications', { replace: true })
    } catch (error) {
      if (attempt.current === currentAttempt) setError(
        error.status === 401 ? 'Unable to sign in. Check your email and password.'
          : 'Sign in is unavailable right now. Please try again.'
      )
    } finally {
      if (attempt.current === currentAttempt) {
        pending.current = false
        setLoading(false)
      }
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="login-heading">
        <p className="auth-eyebrow">Job Application Tracker</p>
        <h1 id="login-heading">Welcome back</h1>
        <p className="auth-intro">Sign in to keep your job search moving.</p>

        <form onSubmit={handleSubmit}>
          <div className="auth-field"><label htmlFor="login-email">Email</label>
          <input
            id="login-email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          /></div>

          <div className="auth-field"><label htmlFor="login-password">Password</label>
          <input
            id="login-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          /></div>

          {error && <p className="auth-alert" role="alert">{error}</p>}
          <p className="auth-progress" role="status">{loading ? 'Checking your account…' : ''}</p>

          <button type="submit" disabled={loading}>
            {loading ? 'Signing in...' : 'Sign in'}
          </button>
        </form>
        <p className="auth-footer">New here? <Link to="/register">Create account</Link></p>
      </section>
    </main>
  )
}

export default LoginPage
