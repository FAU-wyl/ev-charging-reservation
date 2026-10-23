import { useEffect, useState } from 'react'
import { vehicleApi } from '../api'
import { useAuth } from '../context/useAuth'

export default function VehiclesPage() {
  const { token } = useAuth()
  const [vehicles, setVehicles] = useState([])
  const [form, setForm] = useState({ nickname: '', license_plate: '', is_default: false })
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const load = () => vehicleApi.list(token).then(setVehicles).catch((error) => setMessage(error.message))
  useEffect(load, [token])
  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setMessage('')
    try { await vehicleApi.create(token, form); setForm({ nickname: '', license_plate: '', is_default: false }); await load() }
    catch (error) { setMessage(error.message) } finally { setBusy(false) }
  }
  const makeDefault = async (vehicle) => { await vehicleApi.update(token, vehicle.id, { is_default: true }); load() }
  const remove = async (vehicle) => {
    if (!window.confirm(`Remove ${vehicle.nickname}?`)) return
    try { await vehicleApi.remove(token, vehicle.id); load() } catch (error) { setMessage(error.message) }
  }
  return <div className="content-page">
    <div className="page-heading"><div><span className="eyebrow">Garage</span><h1>My vehicles</h1><p>Choose which plate the arrival camera should expect.</p></div><span className="count-pill">{vehicles.length} vehicles</span></div>
    {message && <div className="alert error">{message}</div>}
    <div className="two-column">
      <section className="card"><div className="section-title"><div><span className="eyebrow">Registered vehicles</span><h2>Your garage</h2></div></div>
        <div className="vehicle-list">{vehicles.length ? vehicles.map((vehicle) => <article className="vehicle-card" key={vehicle.id}>
          <div className="vehicle-icon">EV</div><div><h3>{vehicle.nickname}</h3><span className="plate">{vehicle.license_plate}</span></div>
          <div className="vehicle-actions">{vehicle.is_default ? <span className="default-label">Default</span> : <button className="text-button" onClick={() => makeDefault(vehicle)}>Set default</button>}<button className="icon-button danger" onClick={() => remove(vehicle)}>×</button></div>
        </article>) : <div className="empty-state"><b>No vehicles yet</b><span>Add a synthetic test plate to make a reservation.</span></div>}</div>
      </section>
      <form className="card form-card" onSubmit={submit}><span className="eyebrow">Add a vehicle</span><h2>New car</h2>
        <label>Nickname<input required placeholder="e.g. Weekend EV" value={form.nickname} onChange={(event) => setForm({ ...form, nickname: event.target.value })} /></label>
        <label>License plate<input required placeholder="M-AB1234" value={form.license_plate} onChange={(event) => setForm({ ...form, license_plate: event.target.value.toUpperCase() })} /></label>
        <label className="check-row"><input type="checkbox" checked={form.is_default} onChange={(event) => setForm({ ...form, is_default: event.target.checked })} /> Make this my default vehicle</label>
        <button className="btn primary wide" disabled={busy}>{busy ? 'Adding…' : 'Add vehicle'}</button>
        <small className="privacy-note">Use authorised synthetic plates for the demo. Plates are personal data.</small>
      </form>
    </div>
  </div>
}
