import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'
import { reservationApi } from '../api'
import StatusBadge from '../components/StatusBadge'
import { useAuth } from '../context/useAuth'
import { formatDate } from '../utils'

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
  const load = useCallback(() => reservationApi.get(token, reservationId).then(setReservation).catch((reason) => setError(reason.message)), [token, reservationId])
  useEffect(() => { load(); const timer = setInterval(load, 5000); return () => clearInterval(timer) }, [load])
  if (error) return <div className="page-state error">{error}</div>
  if (!reservation) return <div className="page-state"><div className="spinner" />Loading journey…</div>
  return <div className="content-page narrow-page">
    <Link className="back-link" to="/reservations">← My reservations</Link>
    {location.state?.created && <div className="alert success"><b>Reservation confirmed.</b> The provider returned ID {reservation.ecurious_reservation_id}.</div>}
    <div className="reservation-detail-head"><div><span className="eyebrow">{reservation.id}</span><h1>{reservation.station_name}</h1><p>{reservation.bay_id} · {reservation.vehicle_nickname} · {reservation.license_plate}</p></div><StatusBadge value={reservation.status} /></div>
    {reservation.attention_message && <div className="alert warning"><b>Attention required</b>{reservation.attention_message}</div>}
    <div className="detail-grid">
      <section className="card journey-card"><span className="eyebrow">Live journey</span><h2>Reservation progress</h2><div className="journey">{stepsFor(reservation.status).map(([label, done, detail], index) => <div className={done ? 'done' : ''} key={label}><i>{done ? '✓' : index + 1}</i><span><b>{label}</b><small>{detail}</small></span></div>)}</div><p className="poll-note"><span className="live-dot" /> Updates every 5 seconds from App Backend</p></section>
      <section className="card"><span className="eyebrow">Booking details</span><h2>Your charging window</h2><dl className="detail-list"><div><dt>Starts</dt><dd>{formatDate(reservation.start_time)}</dd></div><div><dt>Ends</dt><dd>{formatDate(reservation.end_time)}</dd></div><div><dt>Provider reservation</dt><dd>{reservation.ecurious_reservation_id}</dd></div><div><dt>Ecurious state</dt><dd>{reservation.ecurious_status}</dd></div><div><dt>Charging signal</dt><dd>{reservation.simulated_charging ? 'Simulation / active' : 'Simulation / inactive'}</dd></div></dl><p className="data-note left">Charging state is simulated and is not claimed as real charger telemetry.</p></section>
    </div>
  </div>
}
