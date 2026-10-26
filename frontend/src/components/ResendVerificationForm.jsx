import { useRef, useState } from 'react'
import { resendVerification } from '../auth/authClient.js'

const acknowledgement = 'If this address needs verification, instructions will be sent when available.'

function ResendVerificationForm({ initialEmail = '' }) {
  const [email, setEmail] = useState(initialEmail)
  const [status, setStatus] = useState('idle')
  const pending = useRef(false)
  const input = useRef(null)

  async function handleSubmit(event) {
    event.preventDefault()
    if (pending.current) return

    const normalizedInput = email.trim()
    if (!normalizedInput || !input.current.checkValidity()) {
      setStatus('invalid')
      input.current.focus()
      return
    }

    pending.current = true
    setStatus('sending')
    try {
      await resendVerification(normalizedInput)
      setStatus('success')
    } catch {
      // Do not display server error bodies or suggest whether an account exists.
      setStatus('error')
    } finally {
      pending.current = false
    }
  }

  return (
    <form className="resend-form" onSubmit={handleSubmit} noValidate>
      <h2>Need a new verification link?</h2>
      <p>Enter your email address to request another link.</p>
      <label htmlFor="resend-email">Email address</label>
      <input
        ref={input}
        id="resend-email"
        type="email"
        autoComplete="email"
        required
        maxLength={320}
        value={email}
        aria-invalid={status === 'invalid'}
        aria-describedby={status === 'invalid' ? 'resend-error' : undefined}
        onChange={(event) => { setEmail(event.target.value); setStatus('idle') }}
      />
      {status === 'invalid' && <p id="resend-error" className="auth-field-error" role="alert">Enter a valid email address.</p>}
      {status === 'error' && <p className="auth-alert" role="alert">We could not process your request right now. Please try again.</p>}
      {status === 'success' && <p className="auth-success" role="status">{acknowledgement}</p>}
      <p className="auth-progress" role="status">{status === 'sending' ? 'Requesting a new link…' : ''}</p>
      <button type="submit" disabled={status === 'sending'}>
        {status === 'sending' ? 'Requesting link...' : 'Request verification link'}
      </button>
    </form>
  )
}

export default ResendVerificationForm
