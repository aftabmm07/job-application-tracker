function ApplicationDataState({ loading, error, onRetry, children }) {
  if (loading) {
    return (
      <section className="state-panel" role="status">
        <h2>Loading applications…</h2>
        <p>Getting the latest information about your job search.</p>
      </section>
    )
  }
  if (error) {
    return (
      <section className="state-panel error-panel" role="alert">
        <h2>Unable to load applications</h2>
        <p>{error}</p>
        <p>Please check your connection and try again.</p>
        <button type="button" onClick={onRetry}>Try again</button>
      </section>
    )
  }
  return children
}

export default ApplicationDataState
