export function formatDate(date) {
  if (!date) return 'Not provided'

  // Backend dates are YYYY-MM-DD. Use local calendar parts to avoid timezone shifts.
  const [year, month, day] = date.split('-').map(Number)
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(
    new Date(year, month - 1, day),
  )
}
