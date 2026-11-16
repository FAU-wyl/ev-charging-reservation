import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { reservationApi } from '../api'
import StatusBadge from '../components/StatusBadge'
import { useAuth } from '../context/useAuth'
import { formatDate } from '../utils'

const TERMINAL = ['COMPLETED', 'CANCELLED', 'EXPIRED']
export default function ReservationsPage() {
  const { token } = useAuth()
  const [reservations, setReservations] = useState([])
  const [filter, setFilter] = useState('ACTIVE')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const load = useCallback(() => reservationApi.mine(token).then(setReservations).catch((reason) => setError(reason.message)).finally(() => setLoading(false)), [token])
  useEffect(() => { load(); const timer = setInterval(load, 8000); return () => clearInterval(timer) }, [load])
  const shown = useMemo(() => reservations.filter((reservation) => filter === 'ALL' || (filter === 'ACTIVE' ? !TERMINAL.includes(reservation.status) : reservation.status === filter)), [reservations, filter])
  const cancel = async (reservation) => {
    if (!window.confirm('Cancel this reservation?')) return
    try { await reservationApi.cancel(token, reservation.id); load() } catch (reason) { setError(reason.message) }
  }
  return <div className="content-page">
    <div className="page-heading"><div><span className="eyebrow">Driver journey</span><h1>My reservations</h1><p>Live application status refreshes automatically every 8 seconds.</p></div><Link className="btn primary" to="/">Find a charger</Link></div>
    <div className="segmented">{['ACTIVE', 'COMPLETED', 'CANCELLED', 'EXPIRED', 'ALL'].map((item) => <button className={filter === item ? 'active' : ''} onClick={() => setFilter(item)} key={item}>{item.toLowerCase()}</button>)}</div>
    {error && <div className="alert error">{error}</div>}
    {loading ? <div className="page-state"><div className="spinner" />Loading reservations…</div> : <div className="reservation-list">
      {shown.length ? shown.map((reservation) => <article className="reservation-card" key={reservation.id}>
        <div className="reservation-time"><span>{new Date(reservation.start_time).toLocaleDateString(undefined, { month: 'short' }).toUpperCase()}</span><b>{new Date(reservation.start_time).getDate()}</b><small>{new Date(reservation.start_time).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</small></div>
        <div className="reservation-main"><div><span className="eyebrow">{reservation.bay_id} · {reservation.id}</span><h2>{reservation.station_name}</h2></div><StatusBadge value={reservation.status} /><div className="reservation-meta"><span><small>Vehicle</small>{reservation.vehicle_nickname} · {reservation.license_plate}</span><span><small>Window</small>{formatDate(reservation.start_time)} — {formatDate(reservation.end_time)}</span><span><small>Provider state</small>{reservation.ecurious_status}</span></div>{reservation.attention_message && <div className="attention-message">⚠ {reservation.attention_message}</div>}</div>
        <div className="reservation-actions"><Link className="btn ghost compact" to={`/reservations/${reservation.id}`}>View journey</Link>{['RESERVED', 'ATTENTION_REQUIRED', 'BLOCKED'].includes(reservation.status) && <button className="text-button danger" onClick={() => cancel(reservation)}>Cancel</button>}</div>
      </article>) : <div className="empty-state large"><b>No {filter.toLowerCase()} reservations</b><span>Book an Ecurious-connected bay from the map.</span><Link className="btn primary compact" to="/">Explore stations</Link></div>}
    </div>}
  </div>
}
