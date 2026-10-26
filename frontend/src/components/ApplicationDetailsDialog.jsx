import { useEffect, useRef } from 'react'
import { formatDate } from '../applicationDates.js'

function ApplicationDetailsDialog({ application, onClose }) {
  const dialogRef = useRef(null)
  const closeRef = useRef(null)

  useEffect(() => {
    const previousFocus = document.activeElement
    const dialog = dialogRef.current
    dialog.showModal()
    closeRef.current.focus()
    return () => {
      dialog.close()
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  }, [])

  const createdAt = application.created_at ? new Date(application.created_at) : null
  const createdAtLabel = createdAt && !Number.isNaN(createdAt.getTime())
    ? new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(createdAt)
    : 'Not provided'

  return (
    <dialog className="application-dialog details-dialog" ref={dialogRef}
      aria-labelledby="application-details-heading"
      onCancel={(event) => { event.preventDefault(); onClose() }}>
      <h2 id="application-details-heading">Application details</h2>
      <dl className="details-grid">
        <div><dt>Company</dt><dd>{application.company_name}</dd></div>
        <div><dt>Job title</dt><dd>{application.job_title}</dd></div>
        <div><dt>Status</dt><dd><span className={`status-badge status-${application.status}`}>{application.status}</span></dd></div>
        <div><dt>Location</dt><dd>{application.location || 'Not provided'}</dd></div>
        <div><dt>Application date</dt><dd>{formatDate(application.application_date)}</dd></div>
        <div><dt>Added to tracker</dt><dd>{createdAtLabel}</dd></div>
        <div className="details-description">
          <dt>Job description</dt>
          <dd>{application.job_description || 'Not provided'}</dd>
        </div>
      </dl>
      <div className="form-actions">
        <button type="button" className="secondary-button" ref={closeRef} onClick={onClose}>Close details</button>
      </div>
    </dialog>
  )
}

export default ApplicationDetailsDialog
