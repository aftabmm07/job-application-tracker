import { Link } from 'react-router-dom'

function NotFoundPage() {
  return (
    <section className="state-panel">
      <h1>Page not found</h1>
      <p>The page you requested does not exist.</p>
      <Link className="navigation-back" to="/applications">Back to Applications</Link>
    </section>
  )
}

export default NotFoundPage
