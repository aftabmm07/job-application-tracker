import { formatDate } from '../applicationDates.js'
import ApplicationActions from './ApplicationActions.jsx'

function ApplicationsTable({ applications, onView, onEdit, onDelete }) {
  return (
    <>
    <div className="table-scroll" tabIndex={0} role="region" aria-label="Job applications table">
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
                <ApplicationActions application={application} onView={onView} onEdit={onEdit} onDelete={onDelete} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    <ul className="application-card-list" aria-label="Job applications">
      {applications.map((application) => (
        <li className="application-card" key={application.id}>
          <article aria-labelledby={`application-card-title-${application.id}`}>
            <div className="application-card-heading">
              <div className="application-card-identity">
                <p className="application-card-company">{application.company_name}</p>
                <h3 id={`application-card-title-${application.id}`}>{application.job_title}</h3>
              </div>
              <span className={`status-badge status-${application.status}`}>{application.status}</span>
            </div>
            <dl className="application-card-meta">
              <div><dt>Location</dt><dd>{application.location || 'Not provided'}</dd></div>
              <div><dt>Application date</dt><dd>{formatDate(application.application_date)}</dd></div>
            </dl>
            <ApplicationActions application={application} onView={onView} onEdit={onEdit} onDelete={onDelete} />
          </article>
        </li>
      ))}
    </ul>
    </>
  )
}

export default ApplicationsTable
