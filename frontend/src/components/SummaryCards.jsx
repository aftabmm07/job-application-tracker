const statuses = ['saved', 'applied', 'interview', 'offer', 'rejected']

function SummaryCards({ applications }) {
  const cards = [
    { label: 'Total applications', count: applications.length },
    ...statuses.map((status) => ({
      label: status[0].toUpperCase() + status.slice(1),
      count: applications.filter((application) => application.status === status).length,
    })),
  ]

  return (
    <section aria-label="Application summary">
      <dl className="summary-grid">
        {cards.map(({ label, count }) => (
          <div className="summary-card" key={label}>
            <dt>{label}</dt>
            <dd>{count}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

export default SummaryCards
