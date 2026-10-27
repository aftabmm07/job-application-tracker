import { useEffect, useRef } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'

function AppLayout({ onLogout }) {
  const { pathname } = useLocation()
  const contentRef = useRef(null)

  useEffect(() => {
    document.title = `${pathname === '/applications' ? 'Applications' : pathname === '/analytics' ? 'Analytics' : pathname === '/profile' ? 'My Profile' : 'Not Found'} | Job Application Tracker`
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
            <NavLink to="/profile" end>My Profile</NavLink>
            <button
              type="button"
              className="logout-button"
              onClick={onLogout}
            >
              Log out
            </button>
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
