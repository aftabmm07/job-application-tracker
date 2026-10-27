import ApplicationAnalytics from '../components/ApplicationAnalytics.jsx'
import ApplicationDataState from '../components/ApplicationDataState.jsx'

function AnalyticsPage({ applications, loading, error, refreshApplications }) {
  return (
    <>
      <header className="dashboard-header">
        <p className="eyebrow">Your career, organized</p>
        <h1>Analytics</h1>
        <p>Explore the current status and monthly activity of all your applications.</p>
      </header>
      <ApplicationDataState loading={loading} error={error} onRetry={refreshApplications} context="analytics">
        <ApplicationAnalytics applications={applications} />
      </ApplicationDataState>
    </>
  )
}

export default AnalyticsPage
