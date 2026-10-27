import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import AppLayout from './components/AppLayout.jsx'
import ApplicationsPage from './pages/ApplicationsPage.jsx'
import NotFoundPage from './pages/NotFoundPage.jsx'
import LoginPage from './pages/LoginPage.jsx'
import RegisterPage from './pages/RegisterPage.jsx'
import VerifyEmailPage from './pages/VerifyEmailPage.jsx'
import ProfilePage from './pages/ProfilePage.jsx'
import { getCurrentUser, logout } from './auth/authClient.js'
import './App.css'

// Charts are needed only on /analytics; keep Recharts out of the initial route.
const AnalyticsPage = lazy(() => import('./pages/AnalyticsPage.jsx'))

function App() {
  const location = useLocation()
  const verifyingEmail = location.pathname === '/verify-email'
  const sessionVersion = useRef(0)
  const [renderedVersion, setRenderedVersion] = useState(0)
  const [applications, setApplications] = useState([])
  const [applicationsOwnerId, setApplicationsOwnerId] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [user, setUser] = useState(null)
  const [authState, setAuthState] = useState('checking')
  const [requestNumber, setRequestNumber] = useState(0)
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  function clearPrivateData() {
    setUser(null)
    setApplications([])
    setApplicationsOwnerId(null)
    setSearchQuery('')
    setStatusFilter('')
    setError('')
    setLoading(true)
  }

  function endSession(expectedVersion) {
    if (sessionVersion.current !== expectedVersion) return
    sessionVersion.current += 1
    setRenderedVersion(sessionVersion.current)
    clearPrivateData()
    setAuthState('anonymous')
  }

  async function checkSession() {
    const version = ++sessionVersion.current
    setRenderedVersion(version)
    clearPrivateData()
    setAuthState('checking')
    try {
      const currentUser = await getCurrentUser()
      if (sessionVersion.current === version) {
        setUser(currentUser)
        setAuthState('authenticated')
      }
    } catch (failure) {
      if (sessionVersion.current === version) {
        setAuthState(failure.status === 401 ? 'anonymous' : 'unavailable')
      }
    }
  }

  useEffect(() => {
    // The public verification page manages its own CSRF flow. No session check
    // runs there, including for a malformed verification link.
    let active = true
    if (!verifyingEmail) {
      Promise.resolve().then(() => { if (active) checkSession() })
    }
    return () => {
      active = false
      sessionVersion.current += 1
    }
  }, [verifyingEmail])

  useEffect(() => {
    if (authState !== 'authenticated' || !user) return
    const controller = new AbortController()
    const version = sessionVersion.current

    async function loadApplications() {
      setLoading(true)
      setError('')
      try {
        const response = await fetch('/applications', { signal: controller.signal })
        if (controller.signal.aborted || sessionVersion.current !== version) return
        if (response.status === 401) {
          endSession(version)
          return
        }
        if (!response.ok) throw new Error(`The server returned HTTP ${response.status}.`)
        const data = await response.json()
        if (!Array.isArray(data)) throw new Error('The server returned an unexpected response.')
        if (!controller.signal.aborted && sessionVersion.current === version) {
          setApplications(data)
          setApplicationsOwnerId(user.id)
        }
      } catch (failure) {
        if (!controller.signal.aborted && sessionVersion.current === version) {
          setError(failure.message || 'The server could not be reached.')
        }
      } finally {
        if (!controller.signal.aborted && sessionVersion.current === version) setLoading(false)
      }
    }

    loadApplications()
    return () => controller.abort()
  }, [requestNumber, authState, user])

  async function handleLogout() {
    const version = ++sessionVersion.current
    setRenderedVersion(version)
    clearPrivateData()
    setAuthState('checking')
    try {
      await logout()
      if (sessionVersion.current === version) setAuthState('anonymous')
    } catch {
      // Logout may have committed before a network failure. Check the session
      // again instead of restoring old private data or assuming it ended.
      if (sessionVersion.current === version) setAuthState('unavailable')
    }
  }

  function handleLogin(authenticatedUser) {
    sessionVersion.current += 1
    setRenderedVersion(sessionVersion.current)
    clearPrivateData()
    setUser(authenticatedUser)
    setAuthState('authenticated')
  }

  function handleLoginRecovery() {
    // The login POST succeeded, but its CSRF refresh did not. Confirm the
    // rotated cookie with /auth/me before showing any account-specific data.
    checkSession()
  }

  // These callbacks capture the rendering session. A form from a previous
  // account cannot clear or refresh the next account when its request resolves.
  function handleUnauthorized() { endSession(renderedVersion) }
  function refreshApplications() {
    if (sessionVersion.current === renderedVersion) {
      setRequestNumber((number) => number + 1)
    }
  }

  const visibleApplications = user && applicationsOwnerId === user.id ? applications : []
  const dataProps = {
    applications: visibleApplications,
    loading: loading || (Boolean(user) && applicationsOwnerId !== user.id && !error),
    error,
    refreshApplications,
  }

  return (
    <Routes>
      <Route path="/login" element={<LoginPage onLogin={handleLogin} onLoginRecovery={handleLoginRecovery} />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/verify-email" element={<VerifyEmailPage key={location.key} />} />
      <Route element={
        authState === 'checking' ? (
          <p role="status">Checking your session...</p>
        ) : authState === 'unavailable' ? (
          <main className="state-panel error-panel" role="alert">
            <h1>Unable to check your session</h1>
            <p>Your applications are hidden until your session can be checked.</p>
            <button type="button" onClick={checkSession}>Try again</button>
          </main>
        ) : authState === 'authenticated' && user ? (
          <AppLayout key={user.id} onLogout={handleLogout} />
        ) : (
          <Navigate to="/login" replace />
        )
      }>
        <Route index element={<Navigate to="/applications" replace />} />
        <Route path="applications" element={
          <ApplicationsPage {...dataProps} onUnauthorized={handleUnauthorized}
            searchQuery={searchQuery} setSearchQuery={setSearchQuery}
            statusFilter={statusFilter} setStatusFilter={setStatusFilter} />
        } />
        <Route path="analytics" element={
          <Suspense fallback={<section className="state-panel state-panel-spacious" role="status">
            <h2>Loading analytics…</h2>
          </section>}>
            <AnalyticsPage {...dataProps} />
          </Suspense>
        } />
        <Route path="profile" element={<ProfilePage key={`${user?.id}:${user?.email}:${renderedVersion}`}
          account={user} onUnauthorized={handleUnauthorized} />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

export default App
