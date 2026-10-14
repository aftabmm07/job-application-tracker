import {
  Bar, BarChart, CartesianGrid, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { applicationStatuses } from '../applicationStatuses.js'
import { calculateAnalytics } from '../applicationAnalytics.js'

const statusColors = {
  saved: '#64748b', applied: '#2563eb', interview: '#7c3aed', offer: '#16a34a', rejected: '#dc2626',
}

function formatMonth(month) {
  return new Intl.DateTimeFormat(undefined, {
    month: 'short', year: 'numeric', timeZone: 'UTC',
  }).format(new Date(`${month}-01T00:00:00Z`))
}

function StatusDistribution({ statusCounts, isEmpty }) {
  const data = applicationStatuses.map((status) => ({
    name: status[0].toUpperCase() + status.slice(1),
    count: statusCounts[status],
    fill: statusColors[status],
  }))

  return (
    <section className="analytics-card" aria-labelledby="status-chart-heading">
      <h3 id="status-chart-heading">Status distribution</h3>
      <p>Current status of every application, including undated records.</p>
      {isEmpty ? <div className="chart-empty">No applications yet. Add an application to see its status here.</div> : (
        <div className="chart-container">
          <ResponsiveContainer width="100%" height="100%" minWidth={0}>
            <PieChart accessibilityLayer>
              <Pie data={data} dataKey="count" nameKey="name" innerRadius="55%" outerRadius="80%"
                isAnimationActive={false} />
              <Tooltip formatter={(value, name) => [`${value} applications`, name]} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}
      <dl className="chart-legend">
        {data.map(({ name, count, fill }) => (
          <div key={name}>
            <dt><span className="legend-dot" style={{ background: fill }} aria-hidden="true" />{name}</dt>
            <dd>{count}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function MonthlyApplications({ monthlyData }) {
  return (
    <section className="analytics-card" aria-labelledby="monthly-chart-heading">
      <h3 id="monthly-chart-heading">Applications by month</h3>
      <p>Grouped by application date. Records without a date are excluded; only months with records are shown.</p>
      {monthlyData.length === 0 ? (
        <div className="chart-empty">No dated applications yet. Add an application date to see monthly activity.</div>
      ) : (
        <>
          <div className="chart-container">
            <ResponsiveContainer width="100%" height="100%" minWidth={0}>
              <BarChart data={monthlyData} accessibilityLayer margin={{ top: 12, right: 12, bottom: 16, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="month" tickFormatter={formatMonth} tick={{ fontSize: 12 }} minTickGap={24} />
                <YAxis allowDecimals={false} width={32} />
                <Tooltip labelFormatter={formatMonth} formatter={(value) => [value, 'Applications']} />
                <Bar dataKey="count" name="Applications" fill="#4338ca" radius={[4, 4, 0, 0]}
                  maxBarSize={56} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <details className="chart-data">
            <summary>View monthly counts as text</summary>
            <ul>{monthlyData.map(({ month, count }) => (
              <li key={month}>{formatMonth(month)}: {count} {count === 1 ? 'application' : 'applications'}</li>
            ))}</ul>
          </details>
        </>
      )}
    </section>
  )
}

function ApplicationAnalytics({ applications }) {
  const analytics = calculateAnalytics(applications)

  return (
    <section className="analytics-section" aria-labelledby="analytics-heading">
      <h2 id="analytics-heading">Application insights</h2>
      <p className="analytics-description">Based on all applications, regardless of table search or status filters.
        {' '}The total is shown in the summary cards above.</p>
      <dl className="rate-grid">
        <div className="summary-card"><dt>Interview rate</dt><dd>{Number(analytics.interviewRate.toFixed(1))}%</dd></div>
        <div className="summary-card"><dt>Offer rate</dt><dd>{Number(analytics.offerRate.toFixed(1))}%</dd></div>
      </dl>
      <p className="analytics-description">
        Interview rate = (Interview + Offer) ÷ submitted applications. Offer rate = Offer ÷ submitted applications.
        {' '}The denominator is Applied + Interview + Offer + Rejected ({analytics.denominator}); Saved is excluded.
        {' '}These are current-status-based indicators, not historical conversion rates: status history is not stored.
        {' '}With no submitted applications, both rates are 0%.
      </p>
      <div className="analytics-grid">
        <StatusDistribution statusCounts={analytics.statusCounts} isEmpty={applications.length === 0} />
        <MonthlyApplications monthlyData={analytics.monthlyData} />
      </div>
    </section>
  )
}

export default ApplicationAnalytics
