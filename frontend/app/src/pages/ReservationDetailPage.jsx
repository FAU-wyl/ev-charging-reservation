import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { reservationApi } from '../api'
import StatusBadge from '../components/StatusBadge'
import { useAuth } from '../context/useAuth'
import { formatDate } from '../utils'
import { canConfirmDeparture } from './reservationState'

const stepsFor = (status) => {
  const checked = ['CHECKED_IN', 'CHARGING', 'CHARGE_COMPLETED', 'COMPLETED'].includes(status)
  const charging = ['CHARGING', 'CHARGE_COMPLETED', 'COMPLETED'].includes(status)
  const complete = status === 'COMPLETED'
  return [
    ['Reservation confirmed', true, 'Provider accepted and protected the booking'],
    ['Automatic check-in', checked, checked ? 'Expected plate matched' : 'Waiting for the arrival camera'],
    ['Charging', charging, charging ? 'Simulation started by the operator' : 'Not started'],
    ['Completed & released', complete, complete ? 'Reservation resource released' : 'Pending'],
  ]
}

export default function ReservationDetailPage() {
  const { reservationId } = useParams()
  const location = useLocation()
  const { token } = useAuth()
  const [reservation, setReservation] = useState(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [clock, setClock] = useState(0)
  const load = useCallback(() => reservationApi.get(token, reservationId).then(setReservation).catch((reason) => setError(reason.message)), [token, reservationId])
  useEffect(() => { load(); const timer = setInterval(load, 5000); return () => clearInterval(timer) }, [load])
  useEffect(() => { const timer = setInterval(() => setClock(Date.now()), 1000); return () => clearInterval(timer) }, [])
  if (error && !reservation) return <div className="page-state error">{error}</div>
  if (!reservation) return <div className="page-state"><div className="spinner" />Loading journey…</div>
  const session = reservation.charging_session
  const departureReady = canConfirmDeparture(reservation)
  const duration = session?.status === 'ACTIVE'
    ? Math.max(0, Math.floor((clock - new Date(session.started_at).getTime()) / 1000))
    : (session?.duration_seconds || 0)
  const estimatedCost = session?.status === 'ACTIVE'
    ? duration / 3600 * session.rate_eur_per_hour
    : (session?.estimated_cost_eur || 0)
  const runCharging = async (action) => {
    setBusy(true); setError(''); setMessage('')
    try {
      if (action === 'start') await reservationApi.startCharging(token, reservation.id)
      else await reservationApi.stopCharging(token, session.session_id)
      await load()
    } catch (reason) { setError(reason.message) } finally { setBusy(false) }
  }
  const confirmDeparture = async () => {
    if (!window.confirm("Confirm that the vehicle has fully left the bay? The physical parking lock will rise only if the sensor reports FREE.")) return
    setBusy(true); setError(''); setMessage('')
    try {
      const result = await reservationApi.confirmDeparture(token, reservation.id)
      setMessage(result.hardware_command
        ? `Departure confirmed. Raise command ${result.hardware_command.command_status}; physical confirmation is pending.`
        : 'Departure confirmed.')
      await load()
    } catch (reason) { setError(reason.message) } finally { setBusy(false) }
  }
  return <div className="content-page narrow-page">
    <Link className="back-link" to="/reservations">← My reservations</Link>
    {location.state?.created && <div className="alert success"><b>Reservation confirmed.</b> The provider returned ID {reservation.ecurious_reservation_id}.</div>}
    {message && <div className="alert success"><b>Departure update.</b> {message}</div>}
    {error && <div className="alert error"><b>Departure not confirmed.</b> {error}</div>}
    <div className="reservation-detail-head"><div><span className="eyebrow">{reservation.id}</span><h1>{reservation.station_name}</h1><p>{reservation.bay_id} · {reservation.vehicle_nickname} · {reservation.license_plate}</p></div><StatusBadge value={reservation.status} /></div>
    {reservation.attention_message && <div className="alert warning"><b>Attention required</b>{reservation.attention_message}</div>}
    <div className="detail-grid">
      <section className="card journey-card"><span className="eyebrow">Live journey</span><h2>Reservation progress</h2><div className="journey">{stepsFor(reservation.status).map(([label, done, detail], index) => <div className={done ? 'done' : ''} key={label}><i>{done ? '✓' : index + 1}</i><span><b>{label}</b><small>{detail}</small></span></div>)}</div><p className="poll-note"><span className="live-dot" /> Updates every 5 seconds from App Backend</p></section>
      <section className="card"><span className="eyebrow">Simulated Charging</span><h2>Charging session</h2><dl className="detail-list"><div><dt>Booking starts</dt><dd>{formatDate(reservation.start_time)}</dd></div><div><dt>Booking ends</dt><dd>{formatDate(reservation.end_time)}</dd></div><div><dt>Session ID</dt><dd>{session?.session_id || 'Not started'}</dd></div><div><dt>Session started</dt><dd>{session ? formatDate(session.started_at) : '—'}</dd></div><div><dt>Session ended</dt><dd>{session?.ended_at ? formatDate(session.ended_at) : session?.status === 'ACTIVE' ? 'Charging now' : '—'}</dd></div><div><dt>Duration</dt><dd>{Math.floor(duration / 60)}m {duration % 60}s</dd></div><div><dt>Estimated cost</dt><dd>€{Number(estimatedCost).toFixed(2)}</dd></div><div><dt>Pricing</dt><dd>€{Number(session?.rate_eur_per_hour || 0).toFixed(2)} / hour</dd></div></dl><div className="charging-actions">{reservation.status === 'CHECKED_IN' && !session && <button className="btn primary compact" disabled={busy} onClick={() => runCharging('start')}>Start Charging</button>}{session?.status === 'ACTIVE' && <button className="btn primary compact" disabled={busy} onClick={() => runCharging('stop')}>Stop Charging</button>}{departureReady && <><StatusBadge value="Charging Completed" /><button className="btn primary compact" disabled={busy} onClick={confirmDeparture}>{busy ? 'Checking bay…' : "I've left / 已离开"}</button></>}{reservation.status === 'COMPLETED' && <StatusBadge value="Departure Completed" />}</div>{departureReady && <p className="data-note left">Move the vehicle completely out of the bay, then confirm departure. The lock rises only after the sensor reports FREE.</p>}<p className="data-note left">Simulation / Estimated — no charger energy or payment telemetry is claimed.</p></section>
    </div>
  </div>
}
