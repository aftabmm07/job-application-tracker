function ApplicationActions({ application, onView, onEdit, onDelete }) {
  const name = `${application.job_title} at ${application.company_name}`

  return (
    <div className="row-actions">
      <button type="button" className="secondary-button"
        aria-label={`View details for ${name}`} onClick={() => onView(application)}>
        Details
      </button>
      <button type="button" className="secondary-button"
        aria-label={`Edit ${name}`} onClick={() => onEdit(application)}>
        Edit
      </button>
      <button type="button" className="danger-button"
        aria-label={`Delete ${name}`} onClick={() => onDelete(application)}>
        Delete
      </button>
    </div>
  )
}

export default ApplicationActions
