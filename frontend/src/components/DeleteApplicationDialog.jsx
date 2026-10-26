import { useEffect, useRef, useState } from 'react'
import { getCsrfToken } from '../auth/authClient.js'

function DeleteApplicationDialog({ application, onCancel, onDeleted, onUnauthorized }) {
  const dialogRef = useRef(null)
  const cancelRef = useRef(null)
  const errorRef = useRef(null)
  const pendingRef = useRef(false)
  const deletedRef = useRef(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    const previousFocus = document.activeElement
    const dialog = dialogRef.current
    dialog.showModal()
    cancelRef.current.focus()
    return () => {
      dialog.close()
      if (!deletedRef.current && previousFocus?.isConnected) previousFocus.focus()
      else document.getElementById('add-application-button')?.focus()
    }
  }, [])

  useEffect(() => {
    if (error) errorRef.current?.focus()
  }, [error])

  async function handleDelete() {
    if (pendingRef.current) return
    pendingRef.current = true
    setPending(true)
    setError('')

    try {
      const csrfToken = await getCsrfToken()

      const response = await fetch(`/applications/${application.id}`, { method: 'DELETE',
        headers: { 'Content-Type': 'application/json','X-CSRF-Token': csrfToken },
        body: JSON.stringify({}),
       })
      if (response.status === 401) {
        onUnauthorized()
        return
      }
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        let message = `Unable to delete the application (HTTP ${response.status}). Please try again.`
        if (response.status === 404) {
          message = 'This application no longer exists. Cancel and reload the dashboard to see the latest list.'
        } else if (response.status === 503) {
          message = 'The database could not complete the deletion. Please try again later.'
        } else if (typeof body?.detail === 'string') {
          message = body.detail
        }
        setError(message)
        return
      }

      // The backend returns 204 No Content: do not try to parse a JSON body.
      deletedRef.current = true
      onDeleted()
    } catch {
      setError('Unable to reach the server. Check your connection and try again.')
    } finally {
      pendingRef.current = false
      setPending(false)
    }
  }

  return (
    <dialog className="application-dialog delete-dialog" ref={dialogRef}
      aria-labelledby="delete-application-heading" aria-describedby="delete-application-description"
      onCancel={(event) => {
        event.preventDefault()
        if (!pendingRef.current) onCancel()
      }}>
      <h2 id="delete-application-heading">Delete application?</h2>
      <p id="delete-application-description">
        Delete <strong>{application.job_title}</strong> at <strong>{application.company_name}</strong>?
        {' '}This permanently removes the application.
      </p>
      <div aria-busy={pending}>
        {error && <p className="form-error" role="alert" tabIndex={-1} ref={errorRef}>{error}</p>}
        <p className="saving-message" role="status">{pending ? 'Deleting application…' : ''}</p>
        <div className="form-actions">
          <button type="button" className="secondary-button" ref={cancelRef} disabled={pending}
            onClick={onCancel}>Cancel</button>
          <button type="button" className="danger-button" disabled={pending} onClick={handleDelete}>
            {pending ? 'Deleting…' : 'Delete Application'}
          </button>
        </div>
      </div>
    </dialog>
  )
}

export default DeleteApplicationDialog
