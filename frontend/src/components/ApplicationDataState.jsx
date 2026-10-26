function ApplicationDataState({ loading, error, onRetry, children, context = 'applications' }) {
  const isAnalytics = context === 'analytics'
  if (loading) {
    return (
      <section className="state-panel state-panel-spacious" role="status">
        <h2>{isAnalytics ? 'Loading analytics…' : 'Loading applications…'}</h2>
        <p>Getting the latest information about your job search.</p>
      </section>
    )
  }
  if (error) {
    return (
      <section className="state-panel state-panel-spacious error-panel" role="alert">
        <h2>{isAnalytics ? 'Unable to load analytics' : 'Unable to load applications'}</h2>
        <p>{error}</p>
        <p>Please check your connection and try again.</p>
        <button type="button" onClick={onRetry}>Try again</button>
      </section>
    )
  }
  return children
}

export default ApplicationDataState
