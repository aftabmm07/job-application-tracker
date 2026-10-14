function formatDate(date) {
  if (!date) return 'Not provided'

  // Backend dates are YYYY-MM-DD. Use local calendar parts to avoid timezone shifts.
  const [year, month, day] = date.split('-').map(Number)
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(
    new Date(year, month - 1, day),
  )
}

function ApplicationsTable({ applications, onEdit, onDelete }) {
  return (
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Job applications; scroll horizontally on smaller screens">
      <table>
        <caption className="visually-hidden">Job applications, newest records first</caption>
        <thead>
          <tr>
            <th scope="col">Company</th>
            <th scope="col">Job title</th>
            <th scope="col">Location</th>
            <th scope="col">Status</th>
            <th scope="col">Application date</th>
            <th scope="col">Actions</th>
          </tr>
        </thead>
        <tbody>
          {applications.map((application) => (
            <tr key={application.id}>
              <th scope="row">{application.company_name}</th>
              <td>{application.job_title}</td>
              <td>{application.location || 'Not provided'}</td>
              <td>
                <span className={`status-badge status-${application.status}`}>
                  {application.status}
                </span>
              </td>
              <td>{formatDate(application.application_date)}</td>
              <td>
                <div className="row-actions">
                  <button type="button" className="secondary-button"
                    aria-label={`Edit ${application.job_title} at ${application.company_name}`}
                    onClick={() => onEdit(application)}>
                    Edit
                  </button>
                  <button type="button" className="danger-button"
                    aria-label={`Delete ${application.job_title} at ${application.company_name}`}
                    onClick={() => onDelete(application)}>
                    Delete
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default ApplicationsTable
