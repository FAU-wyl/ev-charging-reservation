import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { fetchBayAvailability, fetchStation, maxPower, reservationApi, vehicleApi } from '../api'
import StatusBadge from '../components/StatusBadge'
import { useAuth } from '../context/useAuth'

const localInput = (date) => {
  const offset = date.getTimezoneOffset() * 60000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

export default function StationPage() {
  const { stationId } = useParams()
  const { user, token } = useAuth()
  const navigate = useNavigate()
  const [station, setStation] = useState(null)
  const [bays, setBays] = useState([])
  const [vehicles, setVehicles] = useState([])
  const [form, setForm] = useState(() => ({
    bay_id: '', vehicle_id: '',
    start_time: localInput(new Date(Date.now() + 15 * 60000)),
    end_time: localInput(new Date(Date.now() + 75 * 60000)),
  }))
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let live = true
    fetchStation(stationId).then(async (value) => {
      if (!live) return
      setStation(value)
      const available = await Promise.all(value.ecurious_bay_ids.map((id) => fetchBayAvailability(id).catch(() => null)))
      if (live) {
        const valid = available.filter(Boolean)
        setBays(valid)
        setForm((current) => ({ ...current, bay_id: valid.find((bay) => bay.reservation_supported)?.bay_id || '' }))
      }
    }).catch((reason) => live && setError(reason.message)).finally(() => live && setLoading(false))
    return () => { live = false }
  }, [stationId])

  useEffect(() => {
    if (!token) return
    vehicleApi.list(token).then((value) => {
      setVehicles(value)
      const defaultVehicle = value.find((item) => item.is_default) || value[0]
      if (defaultVehicle) setForm((current) => ({ ...current, vehicle_id: defaultVehicle.id }))
    }).catch((reason) => setError(reason.message))
  }, [token])

  const selectedBay = useMemo(() => bays.find((bay) => bay.bay_id === form.bay_id), [bays, form.bay_id])
  const submit = async (event) => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const result = await reservationApi.create(token, {
        station_id: station.id,
        bay_id: form.bay_id,
        vehicle_id: form.vehicle_id,
        start_time: new Date(form.start_time).toISOString(),
        end_time: new Date(form.end_time).toISOString(),
      }, crypto.randomUUID())
      navigate(`/reservations/${result.id}`, { state: { created: true } })
    } catch (reason) { setError(reason.message) } finally { setBusy(false) }
  }

  if (loading) return <div className="page-state"><div className="spinner" />Loading station…</div>
  if (!station) return <div className="page-state error">{error || 'Station not found'}</div>
  return <div className="content-page station-detail-page">
    <Link className="back-link" to="/">← Back to map</Link>
    <div className="station-detail-hero">
      <div><span className="eyebrow">{station.source} · Station {station.external_station_id}</span><h1>{station.name}</h1><p>{station.street}, {station.postcode} {station.city}, {station.country}</p><div className="detail-tags"><span>{maxPower(station)} kW max</span><span>{station.network || 'Network not specified'}</span><span>{station.verified ? 'Verified directory entry' : 'Unverified directory entry'}</span></div></div>
      <div className="charge-orb"><b>{maxPower(station)}</b><span>kW</span></div>
    </div>
    {error && <div className="alert error">{error}</div>}
    <div className="booking-layout">
      <section className="card"><span className="eyebrow">Station details</span><h2>Connectors & availability</h2>
        <div className="connector-grid">{station.chargepoints.map((point, index) => <div key={`${point.type}-${index}`}><span className="plug-icon">⌁</span><b>{point.type}</b><small>{point.count} connectors · {point.power} kW</small></div>)}</div>
        <div className="bay-list"><h3>Ecurious-connected bays</h3>{bays.length ? bays.map((bay) => <button className={form.bay_id === bay.bay_id ? 'selected' : ''} disabled={!bay.reservation_supported} onClick={() => setForm({ ...form, bay_id: bay.bay_id })} key={bay.bay_id}><span><b>{bay.bay_id}</b><small>{bay.reservation_supported ? 'Reservation supported' : 'Connected for status only'}</small></span><StatusBadge value={bay.availability_label} /></button>) : <div className="availability-unknown"><b>Availability Unknown</b><span>This GoingElectric station has no confirmed Ecurious Bay mapping.</span></div>}</div>
        <a className="source-link" href={station.source_url} target="_blank" rel="noreferrer">View original GoingElectric directory entry ↗</a>
      </section>
      <section className="card booking-card"><span className="eyebrow">Reserve a protected bay</span><h2>Plan your charging window</h2>
        {!station.reservation_supported ? <div className="availability-unknown"><b>Reservation unavailable</b><span>Only explicitly mapped Ecurious bays can be booked. Live availability is unknown for this station.</span></div>
          : !user ? <div className="login-prompt"><b>Sign in to reserve</b><p>Your account connects the reservation to your vehicle.</p><Link className="btn primary wide" to="/login" state={{ from: `/stations/${station.id}` }}>Log in</Link></div>
          : !vehicles.length ? <div className="login-prompt"><b>Add a vehicle first</b><p>A plate is required for automatic arrival matching.</p><Link className="btn primary wide" to="/vehicles">Add vehicle</Link></div>
          : <form onSubmit={submit}>
            <label>Bay<select required value={form.bay_id} onChange={(event) => setForm({ ...form, bay_id: event.target.value })}>{bays.filter((bay) => bay.reservation_supported).map((bay) => <option key={bay.bay_id}>{bay.bay_id}</option>)}</select></label>
            <label>Vehicle<select required value={form.vehicle_id} onChange={(event) => setForm({ ...form, vehicle_id: event.target.value })}>{vehicles.map((vehicle) => <option value={vehicle.id} key={vehicle.id}>{vehicle.nickname} · {vehicle.license_plate}</option>)}</select></label>
            <div className="form-grid"><label>Starts<input required type="datetime-local" value={form.start_time} onChange={(event) => setForm({ ...form, start_time: event.target.value })} /></label><label>Ends<input required type="datetime-local" value={form.end_time} onChange={(event) => setForm({ ...form, end_time: event.target.value })} /></label></div>
            {selectedBay && <div className="booking-summary"><span>Live Mock status</span><StatusBadge value={selectedBay.availability_label} /></div>}
            <button className="btn primary wide" disabled={busy || !form.bay_id}>{busy ? 'Confirming with Ecurious…' : 'Reserve bay'}</button>
            <small className="privacy-note">Success is shown only after the provider confirms your reservation.</small>
          </form>}
      </section>
    </div>
  </div>
}
