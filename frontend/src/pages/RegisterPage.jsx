
import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { register } from '../auth/authClient.js'
import ResendVerificationForm from '../components/ResendVerificationForm.jsx'

function RegisterPage() {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [loading, setLoading] = useState(false)
  const [passwordError, setPasswordError] = useState('')
  const pending = useRef(false)

  async function handleSubmit(event) {
    event.preventDefault()
    if (pending.current) return
    setError('')
    setSuccess('')
    const length = Array.from(password).length
    let validUnicode = true
    try { encodeURIComponent(password) } catch { validUnicode = false }
    if (!validUnicode || length < 15 || length > 128) {
      setPasswordError('Use 15–128 valid Unicode characters. Spaces are allowed.')
      document.getElementById('register-password')?.focus()
      return
    }
    setPasswordError('')
    pending.current = true
    setLoading(true)

    try {
      const result = await register(email, password)
      setSuccess(
        result.message ||
          'Registration received. Account activation is required before sign-in.'
      )
      setPassword('')
    } catch {
      setError('Unable to complete registration right now. Please check your details and try again.')
    } finally {
      pending.current = false
      setLoading(false)
    }
  }

  return (
    <main className="auth-page">
      <section className="auth-card" aria-labelledby="register-heading">
        <p className="auth-eyebrow">Job Application Tracker</p>
        <h1 id="register-heading">Create your account</h1>
        <p className="auth-intro">Start tracking your job applications in one place.</p>

        <form onSubmit={handleSubmit}>
          <div className="auth-field"><label htmlFor="register-email">Email</label>
          <input
            id="register-email"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          /></div>

          <div className="auth-field"><label htmlFor="register-password">Password</label>
          <input
            id="register-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => { setPassword(event.target.value); setPasswordError('') }}
            aria-invalid={Boolean(passwordError)}
            aria-describedby={passwordError ? 'register-password-hint register-password-error' : 'register-password-hint'}
            required
          />
          <p className="auth-hint" id="register-password-hint">Use 15–128 Unicode characters. Spaces are allowed; no special character or number is required.</p>
          {passwordError && <p className="auth-field-error" id="register-password-error" role="alert">{passwordError}</p>}</div>

          {error && <p className="auth-alert" role="alert">{error}</p>}
          {success && <p className="auth-success" role="status">{success}</p>}
          <p className="auth-progress" role="status">{loading ? 'Submitting your request…' : ''}</p>

          <button type="submit" disabled={loading}>
            {loading ? 'Creating account...' : 'Create account'}
          </button>
        </form>

        {success && <ResendVerificationForm initialEmail={email.trim()} />}

        <p className="auth-footer">
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </section>
    </main>
  )
}

export default RegisterPage
