import { useEffect, useRef, useState } from 'react'
import { applicationStatuses } from '../applicationStatuses.js'
import { getCsrfToken } from '../auth/authClient.js'

const fieldLabels = {
  company_name: 'Company name',
  job_title: 'Job title',
  location: 'Location',
  status: 'Status',
  application_date: 'Application date',
  job_description: 'Job description',
}

function ApplicationForm({ application = null, onCancel, onSaved, onUnauthorized }) {
  const isEditing = application !== null
  const dialogRef = useRef(null)
  const errorRef = useRef(null)
  const pendingRef = useRef(false)
  const savedRef = useRef(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const previousFocus = document.activeElement
    const dialog = dialogRef.current
    dialog.showModal()
    dialog.querySelector('input').focus()
    return () => {
      dialog.close()
      if (!savedRef.current && previousFocus?.isConnected) previousFocus.focus()
      else document.getElementById('add-application-button')?.focus()
    }
  }, [])

  useEffect(() => {
    if (error) errorRef.current?.focus()
  }, [error])

  async function handleSubmit(event) {
    event.preventDefault()
    if (pendingRef.current) return
    setError('')

    const form = event.currentTarget
    const values = new FormData(form)
    const companyName = values.get('company_name').trim()
    const jobTitle = values.get('job_title').trim()
    if (!companyName || !jobTitle) {
      setError('Company name and job title cannot be empty or contain only spaces.')
      return
    }

    // These optional fields are nullable in both create and update schemas.
    // PATCH accepts all six fields; explicit null clears an optional value.
    const payload = {
      company_name: companyName,
      job_title: jobTitle,
      location: values.get('location').trim() || null,
      status: values.get('status'),
      application_date: values.get('application_date') || null,
      job_description: values.get('job_description').trim() || null,
    }

    pendingRef.current = true
    setPending(true)
    try {
       const csrfToken = await getCsrfToken()

      const response = await fetch(isEditing ? `/applications/${application.id}` : '/applications', {
        method: isEditing ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken, },
        body: JSON.stringify(payload),
      })

      if (response.status === 401) {
        onUnauthorized()
        return
      }

      if (!response.ok) {
        // FastAPI validation errors use a detail array; other errors may use text.
        const body = await response.json().catch(() => null)
        let message = `Unable to save the application (HTTP ${response.status}).`
        if (Array.isArray(body?.detail)) {
          message = body.detail.map((issue) => {
            const field = issue.loc?.[issue.loc.length - 1]
            return `${fieldLabels[field] || 'Application'}: ${issue.msg || 'Invalid value'}`
          }).join(' ')
        } else if (typeof body?.detail === 'string') {
          message = body.detail
        }
        throw new Error(message)
      }

      savedRef.current = true
      onSaved()
    } catch (error) {
      setError(error.message || 'Unable to reach the server. Please try again.')
    } finally {
      pendingRef.current = false
      setPending(false)
    }
  }

  return (
    <dialog className="application-dialog" ref={dialogRef} aria-labelledby="application-form-heading"
      aria-describedby="form-instructions" onCancel={(event) => {
        event.preventDefault()
        if (!pendingRef.current) onCancel()
      }}>
      <h2 id="application-form-heading">{isEditing ? 'Edit application' : 'Add new application'}</h2>
      <p id="form-instructions">Fields marked * are required.</p>
      <form onSubmit={handleSubmit} aria-busy={pending}>
        {error && <p className="form-error" role="alert" tabIndex={-1} ref={errorRef}>{error}</p>}
        <fieldset disabled={pending} className="application-fields">
          <legend className="visually-hidden">Application details</legend>
          <div className="form-field">
            <label htmlFor="company-name">Company name *</label>
            <input id="company-name" name="company_name" required maxLength={150} autoComplete="organization" defaultValue={application?.company_name ?? ''} />
          </div>
          <div className="form-field">
            <label htmlFor="job-title">Job title *</label>
            <input id="job-title" name="job_title" required maxLength={200} defaultValue={application?.job_title ?? ''} />
          </div>
          <div className="form-field">
            <label htmlFor="location">Location</label>
            <input id="location" name="location" maxLength={150} defaultValue={application?.location ?? ''} />
          </div>
          <div className="form-field">
            <label htmlFor="application-status">Status</label>
            <select id="application-status" name="status" defaultValue={application?.status ?? 'saved'}>
              {applicationStatuses.map((status) => (
                <option key={status} value={status}>{status[0].toUpperCase() + status.slice(1)}</option>
              ))}
            </select>
          </div>
          <div className="form-field">
            <label htmlFor="application-date">Application date</label>
            <input id="application-date" name="application_date" type="date" defaultValue={application?.application_date ?? ''} />
          </div>
          <div className="form-field form-field-wide">
            <label htmlFor="job-description">Job description</label>
            <textarea id="job-description" name="job_description" rows={5} defaultValue={application?.job_description ?? ''} />
          </div>
        </fieldset>
        <p className="saving-message" role="status">{pending ? 'Saving application…' : ''}</p>
        <div className="form-actions">
          <button type="button" className="secondary-button" disabled={pending} onClick={onCancel}>Cancel</button>
          <button type="submit" disabled={pending}>{pending ? 'Saving…' : isEditing ? 'Save changes' : 'Save application'}</button>
        </div>
      </form>
    </dialog>
  )
}

export default ApplicationForm
