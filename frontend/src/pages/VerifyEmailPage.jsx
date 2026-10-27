import { useLayoutEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { verifyEmail } from '../auth/authClient.js'
import { createVerificationAttempt } from '../auth/emailVerification.js'
import ResendVerificationForm from '../components/ResendVerificationForm.jsx'

const messages = {
  loading: 'Verifying your email. Please wait…',
  success: 'Your email is verified. You can now sign in.',
  invalid: 'This verification link is invalid, expired, or already used.',
  'temporary-error': 'Verification could not be confirmed right now. Please try signing in later. If your account is still inactive, reopen the original link to try again.',
}

function VerifyEmailPage() {
  const [status, setStatus] = useState('loading')
  const attempt = useRef(null)
  const heading = useRef(null)

  useLayoutEffect(() => {
    let active = true
    // Read/clean the URL only in the committed effect, never in a render or
    // state initializer that StrictMode may discard. Keep the attempt on replay.
    if (!attempt.current) {
      attempt.current = createVerificationAttempt(window, verifyEmail)
    }
    attempt.current().then((nextStatus) => {
      if (active) setStatus(nextStatus)
    })
    document.title = 'Verify email | Job Application Tracker'
    heading.current?.focus()
    return () => { active = false }
  }, [])

  const isError = status === 'invalid' || status === 'temporary-error'

  return (
    <main className="verification-page">
      <section className="verification-card" aria-labelledby="verification-heading" aria-busy={status === 'loading'}>
        <p className="auth-eyebrow">Job Application Tracker</p>
        <h1 id="verification-heading" tabIndex={-1} ref={heading}>Verify your email</h1>
        <p className={isError ? 'auth-alert' : status === 'success' ? 'auth-success' : 'auth-intro'} role={isError ? 'alert' : 'status'}>{messages[status]}</p>
        {status === 'invalid' && <ResendVerificationForm />}
        <p className="auth-footer"><Link to="/login">Go to sign in</Link></p>
      </section>
    </main>
  )
}

export default VerifyEmailPage
