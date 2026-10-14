import { useEffect, useState } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import AppLayout from './components/AppLayout.jsx'
import ApplicationsPage from './pages/ApplicationsPage.jsx'
import AnalyticsPage from './pages/AnalyticsPage.jsx'
import NotFoundPage from './pages/NotFoundPage.jsx'
import './App.css'

function App() {
  // Shared state stays mounted when routes change. Only CRUD or Retry triggers a refresh.
  const [applications, setApplications] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [requestNumber, setRequestNumber] = useState(0)
  const [searchQuery, setSearchQuery] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  useEffect(() => {
    const controller = new AbortController()

    async function loadApplications() {
      setLoading(true)
      setError('')
      try {
        const response = await fetch('/applications', { signal: controller.signal })
        if (!response.ok) throw new Error(`The server returned HTTP ${response.status}.`)
        const data = await response.json()
        if (!Array.isArray(data)) throw new Error('The server returned an unexpected response.')
        if (!controller.signal.aborted) setApplications(data)
      } catch (error) {
        if (!controller.signal.aborted) setError(error.message || 'The server could not be reached.')
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }

    loadApplications()
    return () => controller.abort()
  }, [requestNumber])

  function refreshApplications() {
    setRequestNumber((number) => number + 1)
  }

  const dataProps = { applications, loading, error, refreshApplications }

  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/applications" replace />} />
        <Route path="applications" element={
          <ApplicationsPage {...dataProps} searchQuery={searchQuery} setSearchQuery={setSearchQuery}
            statusFilter={statusFilter} setStatusFilter={setStatusFilter} />
        } />
        <Route path="analytics" element={<AnalyticsPage {...dataProps} />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}

export default App
