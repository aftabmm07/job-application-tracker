import { useEffect, useRef } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'

function AppLayout() {
  const { pathname } = useLocation()
  const contentRef = useRef(null)

  useEffect(() => {
    document.title = `${pathname === '/applications' ? 'Applications' : pathname === '/analytics' ? 'Analytics' : 'Not Found'} | Job Application Tracker`
    contentRef.current?.focus()
  }, [pathname])

  return (
    <>
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="app-navigation">
        <div className="navigation-inner">
          <Link className="app-brand" to="/applications">Job Application Tracker</Link>
          <nav aria-label="Main navigation">
            <NavLink to="/applications" end>Applications</NavLink>
            <NavLink to="/analytics" end>Analytics</NavLink>
          </nav>
        </div>
      </header>
      <main id="main-content" className="dashboard" tabIndex={-1} ref={contentRef}>
        <Outlet />
      </main>
    </>
  )
}

export default AppLayout
