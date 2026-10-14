import { applicationStatuses } from './applicationStatuses.js'

export function calculateAnalytics(applications) {
  const statusCounts = Object.fromEntries(applicationStatuses.map((status) => [status, 0]))
  const monthlyCounts = {}

  for (const application of applications) {
    statusCounts[application.status] += 1
    if (application.application_date) {
      // ISO YYYY-MM-DD dates group by year AND month, without timezone conversion.
      const month = application.application_date.slice(0, 7)
      monthlyCounts[month] = (monthlyCounts[month] || 0) + 1
    }
  }

  const denominator = statusCounts.applied + statusCounts.interview
    + statusCounts.offer + statusCounts.rejected
  const interviewRate = denominator === 0 ? 0
    : ((statusCounts.interview + statusCounts.offer) / denominator) * 100
  const offerRate = denominator === 0 ? 0 : (statusCounts.offer / denominator) * 100
  const monthlyData = Object.keys(monthlyCounts).sort().map((month) => ({
    month,
    count: monthlyCounts[month],
  }))

  return { statusCounts, denominator, interviewRate, offerRate, monthlyData }
}
