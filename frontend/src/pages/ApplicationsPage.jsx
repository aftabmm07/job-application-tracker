import { useState } from 'react'
import SummaryCards from '../components/SummaryCards.jsx'
import ApplicationsTable from '../components/ApplicationsTable.jsx'
import ApplicationForm from '../components/ApplicationForm.jsx'
import DeleteApplicationDialog from '../components/DeleteApplicationDialog.jsx'
import ApplicationDetailsDialog from '../components/ApplicationDetailsDialog.jsx'
import { applicationStatuses } from '../applicationStatuses.js'
import ApplicationDataState from '../components/ApplicationDataState.jsx'

function ApplicationsPage({ applications, loading, error, refreshApplications,
  onUnauthorized, searchQuery, setSearchQuery, statusFilter, setStatusFilter }) {
  const [deletingApplication, setDeletingApplication] = useState(null)
  const [viewingApplication, setViewingApplication] = useState(null)
  const [editingApplication, setEditingApplication] = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [successMessage, setSuccessMessage] = useState('')
  // Keep the full response for global summaries; derive table rows without API calls.
  const normalizedQuery = searchQuery.trim().toLowerCase()
  const filteredApplications = applications.filter((application) => {
    const matchesSearch = application.company_name.toLowerCase().includes(normalizedQuery)
      || application.job_title.toLowerCase().includes(normalizedQuery)
    const matchesStatus = statusFilter === '' || application.status === statusFilter
    return matchesSearch && matchesStatus
  })

  function clearFilters() {
    setSearchQuery('')
    setStatusFilter('')
  }

  function openAddForm() {
    setSuccessMessage('')
    setEditingApplication(null)
    setShowForm(true)
  }

  return (
    <>
      <header className="dashboard-header dashboard-header-actions">
        <div>
          <p className="eyebrow">Your career, organized</p>
          <h1>Applications</h1>
          <p>Keep an overview of your applications and your next opportunities.</p>
        </div>
        <button type="button" id="add-application-button" className="add-application-button" onClick={openAddForm}>
          + Add application
        </button>
      </header>

      {successMessage && <p className="success-message" role="status">{successMessage}</p>}
      {showForm && (
        <ApplicationForm
          application={editingApplication}
          onUnauthorized={onUnauthorized}
          onCancel={() => setShowForm(false)}
          onSaved={() => {
            setShowForm(false)
            setSuccessMessage(editingApplication ? 'Application updated successfully.' : 'Application added successfully.')
            refreshApplications()
          }}
        />
      )}

      {deletingApplication && (
        <DeleteApplicationDialog
          application={deletingApplication}
          onUnauthorized={onUnauthorized}
          onCancel={() => setDeletingApplication(null)}
          onDeleted={() => {
            setDeletingApplication(null)
            setSuccessMessage('Application deleted successfully.')
            refreshApplications()
          }}
        />
      )}

      {viewingApplication && (
        <ApplicationDetailsDialog application={viewingApplication}
          onClose={() => setViewingApplication(null)} />
      )}

      <ApplicationDataState loading={loading} error={error} onRetry={refreshApplications}>
        <p className="summary-note">Summary counts include all applications, regardless of filters.</p>
        <SummaryCards applications={applications} />
        <section className="applications-panel" aria-labelledby="applications-heading">
          <div className="panel-header">
            <h2 id="applications-heading">Applications</h2>
            <p role="status" aria-atomic="true">
              {filteredApplications.length} of {applications.length} {applications.length === 1 ? 'application' : 'applications'}
            </p>
          </div>
          <div className="application-filters" role="search" aria-label="Filter applications">
            <div className="form-field">
              <label htmlFor="application-search">Search company or job title</label>
              <input id="application-search" type="search" value={searchQuery}
                placeholder="Company name or job title"
                onChange={(event) => setSearchQuery(event.target.value)} />
            </div>
            <div className="form-field">
              <label htmlFor="status-filter">Filter by status</label>
              <select id="status-filter" value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value)}>
                <option value="">All statuses</option>
                {applicationStatuses.map((status) => (
                  <option key={status} value={status}>{status[0].toUpperCase() + status.slice(1)}</option>
                ))}
              </select>
            </div>
            <button type="button" className="secondary-button" onClick={clearFilters}
              disabled={!searchQuery && !statusFilter}>Clear filters</button>
          </div>
          {applications.length === 0 ? (
            <div className="empty-state" role="status">
              <h3>No applications yet</h3>
              <p>Add your first application to start tracking your search.</p>
              <button type="button" onClick={openAddForm}>Add your first application</button>
            </div>
          ) : filteredApplications.length === 0 ? (
            <div className="empty-state">
              <h3>No matching applications</h3>
              <p>Try a different company, job title, or status, or clear the filters.</p>
            </div>
          ) : (
            <ApplicationsTable applications={filteredApplications} onView={setViewingApplication} onEdit={(application) => {
              setSuccessMessage('')
              setEditingApplication(application)
              setShowForm(true)
            }} onDelete={(application) => {
              setSuccessMessage('')
              setDeletingApplication(application)
            }} />
          )}
        </section>
      </ApplicationDataState>
    </>
  )
}

export default ApplicationsPage
