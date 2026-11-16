import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet } from 'react-router-dom'
import { vehicleApi } from '../api'
import { useAuth } from '../context/useAuth'

export default function Layout() {
  const { user, token, logout } = useAuth()
  const [vehicleOpen, setVehicleOpen] = useState(false)
  const [vehicleForm, setVehicleForm] = useState({ nickname: '', license_plate: '' })
  const [vehicleBusy, setVehicleBusy] = useState(false)
  const [vehicleMessage, setVehicleMessage] = useState('')
  useEffect(() => {
    if (!vehicleOpen) return undefined
    const closeOnEscape = (event) => { if (event.key === 'Escape') setVehicleOpen(false) }
    window.addEventListener('keydown', closeOnEscape)
    return () => window.removeEventListener('keydown', closeOnEscape)
  }, [vehicleOpen])
  const openVehicleDialog = () => {
    setVehicleMessage('')
    setVehicleOpen(true)
  }
  const saveVehicle = async (event) => {
    event.preventDefault(); setVehicleBusy(true); setVehicleMessage('')
    try {
      await vehicleApi.create(token, { ...vehicleForm, is_default: false })
      setVehicleForm({ nickname: '', license_plate: '' })
      setVehicleMessage('Vehicle added successfully. You can now use this plate for reservations.')
    } catch (error) {
      setVehicleMessage(error.message)
    } finally {
      setVehicleBusy(false)
    }
  }
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
          {user && <button type="button" onClick={openVehicleDialog}>Vehicles</button>}
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
      {user && vehicleOpen && <div className="modal-backdrop" role="presentation" onMouseDown={() => setVehicleOpen(false)}>
        <section className="vehicle-modal" role="dialog" aria-modal="true" aria-labelledby="vehicle-dialog-title" onMouseDown={(event) => event.stopPropagation()}>
          <button type="button" className="icon-button modal-close" aria-label="Close vehicle dialog" onClick={() => setVehicleOpen(false)}>×</button>
          <span className="eyebrow">Quick vehicle setup</span>
          <h2 id="vehicle-dialog-title">Add a vehicle / 添加车辆</h2>
          <p>Register a name and plate without leaving the current page.</p>
          {vehicleMessage && <div className={`alert ${vehicleMessage.startsWith('Vehicle added') ? 'success' : 'error'}`}>{vehicleMessage}</div>}
          <form onSubmit={saveVehicle}>
            <label>Name / 姓名或车辆昵称<input autoFocus required maxLength="80" placeholder="e.g. My EV" value={vehicleForm.nickname} onChange={(event) => setVehicleForm({ ...vehicleForm, nickname: event.target.value })} /></label>
            <label>License plate / 车牌号<input required maxLength="20" placeholder="例如：京A12345 / M-AB1234" value={vehicleForm.license_plate} onChange={(event) => setVehicleForm({ ...vehicleForm, license_plate: event.target.value.toUpperCase() })} /></label>
            <button className="btn primary wide" disabled={vehicleBusy}>{vehicleBusy ? 'Saving…' : 'Add vehicle / 添加车辆'}</button>
          </form>
          <small className="privacy-note">The first vehicle is selected as the default automatically.</small>
        </section>
      </div>}
    </div>
  )
}
