import { useEffect, useRef, useState } from 'react'
import { getProfile, updateProfile } from '../auth/authClient.js'

const fields = [
  { name: 'full_name', label: 'Full name', limit: 120, autoComplete: 'name' },
  { name: 'professional_headline', label: 'Professional headline', limit: 160 },
  { name: 'location', label: 'Location', limit: 120, autoComplete: 'address-level2' },
  { name: 'linkedin_url', label: 'LinkedIn URL', limit: 500, type: 'url' },
  { name: 'github_url', label: 'GitHub URL', limit: 500, type: 'url' },
  { name: 'portfolio_url', label: 'Portfolio URL', limit: 500, type: 'url' },
]
const linkFields = fields.slice(3)

function safeHttpsUrl(value) {
  if (typeof value !== 'string' || !value || value.length > 500
    || Array.from(value).some((character) => /\s/u.test(character)
      || character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127)) return null
  try {
    const url = new URL(value)
    const host = url.hostname.toLowerCase()
    if (url.protocol !== 'https:' || url.username || url.password || !host.includes('.')
      || host.endsWith('.local') || host.endsWith('.internal')
      || /^\d+\.\d+\.\d+\.\d+$/u.test(host) || host.includes(':')) return null
    return url.href
  } catch {
    return null
  }
}

function initials(profile, email) {
  const words = (profile.full_name || '').trim().split(/\s+/u).filter(Boolean)
  const letters = words.length
    ? [words[0][0], words.length > 1 ? words.at(-1)[0] : words[0][1] || '']
    : Array.from(email.split('@')[0]).slice(0, 2)
  return letters.join('').toLocaleUpperCase() || 'JT'
}

function validateDraft(draft) {
  const values = {}
  const errors = {}
  for (const field of fields) {
    const value = draft[field.name].trim()
    if (value.length > field.limit) {
      errors[field.name] = `Use ${field.limit} characters or fewer.`
    } else if (field.type === 'url' && value && !safeHttpsUrl(value)) {
      errors[field.name] = 'Enter a public HTTPS URL without sign-in details.'
    }
    values[field.name] = value || null
  }
  return { values, errors }
}

function ProfilePage({ account, onUnauthorized }) {
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [retryNumber, setRetryNumber] = useState(0)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState({})
  const [fieldErrors, setFieldErrors] = useState({})
  const [saveError, setSaveError] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const savePending = useRef(false)
  const firstInput = useRef(null)
  const requestController = useRef(null)
  const onUnauthorizedRef = useRef(onUnauthorized)

  useEffect(() => {
    onUnauthorizedRef.current = onUnauthorized
  }, [onUnauthorized])

  useEffect(() => {
    const controller = new AbortController()
    requestController.current = controller
    let active = true
    getProfile({ signal: controller.signal }).then((data) => {
      if (!active || controller.signal.aborted) return
      if (!data || data.email !== account.email) {
        setLoadError('Unable to confirm this profile. Please try again.')
        return
      }
      setProfile(data)
    }).catch((error) => {
      if (!active || controller.signal.aborted) return
      if (error.status === 401) onUnauthorizedRef.current()
      else setLoadError('Unable to load your profile. Please try again.')
    }).finally(() => {
      if (active && !controller.signal.aborted) setLoading(false)
    })
    return () => {
      active = false
      controller.abort()
      if (requestController.current === controller) requestController.current = null
    }
  }, [account.id, account.email, retryNumber])

  useEffect(() => {
    if (editing) firstInput.current?.focus()
  }, [editing])

  function beginEditing() {
    setDraft(Object.fromEntries(fields.map(({ name }) => [name, profile[name] ?? ''])))
    setFieldErrors({})
    setSaveError('')
    setSaved(false)
    setEditing(true)
  }

  function cancelEditing() {
    if (savePending.current) return
    setEditing(false)
    setFieldErrors({})
    setSaveError('')
  }

  async function saveProfile(event) {
    event.preventDefault()
    if (savePending.current) return
    const { values, errors } = validateDraft(draft)
    setFieldErrors(errors)
    setSaveError('')
    if (Object.keys(errors).length) return
    const changes = Object.fromEntries(
      Object.entries(values).filter(([name, value]) => value !== profile[name]))
    if (!Object.keys(changes).length) {
      setEditing(false)
      return
    }
    savePending.current = true
    setSaving(true)
    const controller = requestController.current
    try {
      const updated = await updateProfile(changes, { signal: controller?.signal })
      if (controller?.signal.aborted) return
      if (!updated || updated.email !== account.email) {
        setSaveError('Unable to confirm the saved profile. Please reload and try again.')
        return
      }
      setProfile(updated)
      setEditing(false)
      setSaved(true)
    } catch (error) {
      if (controller?.signal.aborted) return
      if (error.status === 401) onUnauthorizedRef.current()
      else if (error.status === 422) setSaveError('Please check the profile fields and try again.')
      else setSaveError('Unable to save your profile right now. Please try again.')
    } finally {
      savePending.current = false
      if (!controller?.signal.aborted) setSaving(false)
    }
  }

  if (loading) return <section className="state-panel" role="status"><h1>Loading your profile…</h1></section>
  if (loadError || !profile) {
    return <section className="state-panel error-panel" role="alert">
      <h1>Unable to load your profile</h1>
      <p>{loadError || 'Please try again.'}</p>
      <button type="button" onClick={() => {
        setProfile(null)
        setLoading(true)
        setLoadError('')
        setSaved(false)
        setRetryNumber((number) => number + 1)
      }}>Try again</button>
    </section>
  }

  const empty = fields.every(({ name }) => !profile[name])
  return <section className="profile-page" aria-labelledby="profile-heading">
    <header className="profile-header">
      <div className="profile-avatar" aria-hidden="true">{initials(profile, account.email)}</div>
      <div className="profile-heading-group">
        <p className="eyebrow">Your account</p>
        <h1 id="profile-heading">{profile.full_name || 'My Profile'}</h1>
        <p>{profile.professional_headline || 'A place for your professional details'}</p>
      </div>
      {!editing && <button type="button" onClick={beginEditing}>Edit profile</button>}
    </header>
    {saved && <p className="success-message" role="status">Profile saved successfully.</p>}

    {editing ? <form className="profile-card profile-form" onSubmit={saveProfile} noValidate aria-busy={saving}>
      <div className="profile-section-heading">
        <h2>Edit your details</h2>
        <p>Only you can see this information.</p>
      </div>
      <div className="profile-field-grid">
        {fields.map((field, index) => <div className="form-field" key={field.name}>
          <label htmlFor={`profile-${field.name}`}>{field.label}</label>
          <input id={`profile-${field.name}`} ref={index === 0 ? firstInput : null}
            type={field.type || 'text'} autoComplete={field.autoComplete || 'off'}
            maxLength={field.limit} value={draft[field.name]}
            aria-invalid={Boolean(fieldErrors[field.name])}
            aria-describedby={fieldErrors[field.name] ? `profile-${field.name}-error` : undefined}
            disabled={saving}
            onChange={(event) => {
              setDraft((current) => ({ ...current, [field.name]: event.target.value }))
              setFieldErrors((current) => ({ ...current, [field.name]: '' }))
            }} />
          {fieldErrors[field.name] && <span id={`profile-${field.name}-error`} className="profile-field-error">{fieldErrors[field.name]}</span>}
        </div>)}
      </div>
      <p className="profile-form-hint">Links must use HTTPS. Your verified email cannot be changed here.</p>
      {saveError && <p className="form-error" role="alert">{saveError}</p>}
      <p className="saving-message" role="status">{saving ? 'Saving profile…' : ''}</p>
      <div className="form-actions">
        <button type="button" className="secondary-button" disabled={saving} onClick={cancelEditing}>Cancel</button>
        <button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</button>
      </div>
    </form> : <>
      {empty && <div className="profile-empty">
        <h2>Make this space yours</h2>
        <p>Add your name, headline, location, and professional links to complete your profile.</p>
      </div>}
      <div className="profile-card profile-details">
        <div className="profile-section-heading"><h2>About you</h2><p>Your professional snapshot.</p></div>
        <dl>
          <div><dt>Full name</dt><dd>{profile.full_name || 'Not added yet'}</dd></div>
          <div><dt>Professional headline</dt><dd>{profile.professional_headline || 'Not added yet'}</dd></div>
          <div><dt>Location</dt><dd>{profile.location || 'Not added yet'}</dd></div>
          <div><dt>Verified email</dt><dd>{account.email} <span className="profile-verified">Verified</span></dd></div>
        </dl>
      </div>
      <div className="profile-card profile-details">
        <div className="profile-section-heading"><h2>Professional links</h2><p>Places to learn more about your work.</p></div>
        <dl>
          {linkFields.map(({ name, label }) => {
            const href = safeHttpsUrl(profile[name])
            return <div key={name}><dt>{label.replace(' URL', '')}</dt><dd>
              {href ? <a href={href} target="_blank" rel="noopener noreferrer">View {label.replace(' URL', '')}</a> : 'Not added yet'}
            </dd></div>
          })}
        </dl>
      </div>
    </>}
  </section>
}

export default ProfilePage
