import { Link, NavLink, Outlet } from 'react-router-dom'
import { useAuth } from '../context/useAuth'

export default function Layout() {
  const { user, logout } = useAuth()
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link className="brand" to="/">
          <span className="brand-mark">E</span>
          <span><b>Ecurious</b><small>Reserve. Arrive. Charge.</small></span>
        </Link>
        <nav className="main-nav" aria-label="Main navigation">
          <NavLink to="/" end>Find charging</NavLink>
          {user && <NavLink to="/reservations">My reservations</NavLink>}
          {user && <NavLink to="/vehicles">Vehicles</NavLink>}
          {user?.role === 'ADMIN' && <NavLink to="/admin">Admin</NavLink>}
        </nav>
        <div className="account-area">
          {user ? (
            <>
              <span className="user-pill"><i>{user.email[0].toUpperCase()}</i><span>{user.email}<small>{user.role}</small></span></span>
              <button className="btn ghost compact" onClick={logout}>Log out</button>
            </>
          ) : (
            <><Link className="btn ghost compact" to="/login">Log in</Link><Link className="btn primary compact" to="/register">Create account</Link></>
          )}
        </div>
      </header>
      <main><Outlet /></main>
      <footer className="site-footer">
        <span>Hackathon prototype · Fictional Ecurious integration</span>
        <span>Station directory: GoingElectric development fixture · Map © OpenStreetMap</span>
      </footer>
    </div>
  )
}
