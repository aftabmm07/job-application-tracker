import { useEffect, useState } from 'react'
import SummaryCards from './components/SummaryCards.jsx'
import ApplicationsTable from './components/ApplicationsTable.jsx'
import './App.css'

function App() {
  const [applications, setApplications] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [requestNumber, setRequestNumber] = useState(0)

  useEffect(() => {
    const controller = new AbortController()

    async function loadApplications() {
      setLoading(true)
      setError('')

      try {
        const response = await fetch('/applications', { signal: controller.signal })
        if (!response.ok) {
          throw new Error(`The server returned HTTP ${response.status}.`)
        }

        const data = await response.json()
        if (!Array.isArray(data)) {
          throw new Error('The server returned an unexpected response.')
        }

        if (!controller.signal.aborted) setApplications(data)
      } catch (error) {
        if (!controller.signal.aborted) {
          setError(error.message || 'The server could not be reached.')
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }

    loadApplications()
    return () => controller.abort()
  }, [requestNumber])

  return (
    <main className="dashboard">
      <header className="dashboard-header">
        <p className="eyebrow">Your career, organized</p>
        <h1>Job Application Tracker</h1>
        <p>Keep an overview of your applications and your next opportunities.</p>
      </header>

      {loading ? (
        <section className="state-panel" role="status">
          <h2>Loading applications…</h2>
          <p>Getting the latest information about your job search.</p>
        </section>
      ) : error ? (
        <section className="state-panel error-panel" role="alert">
          <h2>Unable to load applications</h2>
          <p>{error}</p>
          <p>Please check your connection and try again.</p>
          <button onClick={() => setRequestNumber((number) => number + 1)}>
            Try again
          </button>
        </section>
      ) : (
        <>
          <SummaryCards applications={applications} />
          <section className="applications-panel" aria-labelledby="applications-heading">
            <div className="panel-header">
              <h2 id="applications-heading">Applications</h2>
              <p>{applications.length} {applications.length === 1 ? 'application' : 'applications'}</p>
            </div>
            {applications.length === 0 ? (
              <div className="empty-state" role="status">
                <h3>No applications yet</h3>
                <p>Your saved applications will appear here when available.</p>
              </div>
            ) : (
              <ApplicationsTable applications={applications} />
            )}
          </section>
        </>
      )}
    </main>
  )
}

export default App
