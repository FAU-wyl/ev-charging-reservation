import { Navigate, useLocation } from 'react-router-dom'
import { canAccessAdmin } from '../api'
import { useAuth } from '../context/useAuth'

export default function ProtectedRoute({ children, admin = false }) {
  const { user, loading } = useAuth()
  const location = useLocation()
  if (loading) return <div className="page-state"><div className="spinner" />Restoring session…</div>
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />
  if (admin && !canAccessAdmin(user)) return <Navigate to="/reservations" replace />
  return children
}
