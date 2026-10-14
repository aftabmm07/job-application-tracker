import { useEffect, useState } from 'react'
import SummaryCards from './components/SummaryCards.jsx'
import ApplicationsTable from './components/ApplicationsTable.jsx'
import ApplicationForm from './components/ApplicationForm.jsx'
import DeleteApplicationDialog from './components/DeleteApplicationDialog.jsx'
import './App.css'

function App() {
  const [deletingApplication, setDeletingApplication] = useState(null)
  const [editingApplication, setEditingApplication] = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [successMessage, setSuccessMessage] = useState('')
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
        <button type="button" id="add-application-button" className="add-application-button" onClick={() => {
          setSuccessMessage('')
          setEditingApplication(null)
          setShowForm(true)
        }}>
          + Add Application
        </button>
      </header>

      {successMessage && <p className="success-message" role="status">{successMessage}</p>}
      {showForm && (
        <ApplicationForm
          application={editingApplication}
          onCancel={() => setShowForm(false)}
          onSaved={() => {
            setShowForm(false)
            setSuccessMessage(editingApplication ? 'Application updated successfully.' : 'Application added successfully.')
            setRequestNumber((number) => number + 1)
          }}
        />
      )}

      {deletingApplication && (
        <DeleteApplicationDialog
          application={deletingApplication}
          onCancel={() => setDeletingApplication(null)}
          onDeleted={() => {
            setDeletingApplication(null)
            setSuccessMessage('Application deleted successfully.')
            setRequestNumber((number) => number + 1)
          }}
        />
      )}

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
              <ApplicationsTable applications={applications} onEdit={(application) => {
                setSuccessMessage('')
                setEditingApplication(application)
                setShowForm(true)
              }} onDelete={(application) => {
                setSuccessMessage('')
                setDeletingApplication(application)
              }} />
            )}
          </section>
        </>
      )}
    </main>
  )
}

export default App
