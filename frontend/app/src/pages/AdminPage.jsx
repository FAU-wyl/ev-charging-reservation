import { useCallback, useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { adminApi } from '../api'
import StatusBadge from '../components/StatusBadge'
import { useAuth } from '../context/useAuth'
import { formatDate } from '../utils'

const ADMIN_LINKS = [
  ['/admin', 'Overview'], ['/admin/bays', 'Bays'], ['/admin/reservations', 'Reservations'],
  ['/admin/events', 'Event monitor'], ['/admin/alerts', 'Alerts'], ['/admin/analytics', 'Analytics'],
]

function DemoPanel({ token, reservations, onDone }) {
  const active = reservations.find((item) => !['COMPLETED', 'CANCELLED', 'EXPIRED'].includes(item.status))
  const [bay, setBay] = useState(active?.bay_id || 'BAY-001')
  const [plate, setPlate] = useState('M-AB1234')
  const [reservationId, setReservationId] = useState(active?.id || '')
  const [busy, setBusy] = useState('')
  const [result, setResult] = useState('')
  const selectedBay = bay || active?.bay_id || 'BAY-001'
  const selectedReservation = reservationId || active?.id || ''
  const run = async (name, action) => {
    setBusy(name); setResult('')
    try { await action(); setResult(`${name} completed and application state was refreshed.`); await onDone() }
    catch (error) { setResult(error.message) } finally { setBusy('') }
  }
  return <section className="card demo-panel">
    <div className="section-title"><div><span className="eyebrow">Protected development tools</span><h2>Demo control room</h2></div><span className="mock-label">Mock only</span></div>
    <p>These ADMIN-only actions call the App Backend. The browser never calls Ecurious `/mock/*` directly.</p>
    <div className="demo-fields"><label>Bay<input value={selectedBay} onChange={(event) => setBay(event.target.value)} /></label><label>Synthetic plate<input value={plate} onChange={(event) => setPlate(event.target.value.toUpperCase())} /></label><label>App reservation<select value={selectedReservation} onChange={(event) => setReservationId(event.target.value)}><option value="">Select…</option>{reservations.map((item) => <option value={item.id} key={item.id}>{item.id} · {item.bay_id}</option>)}</select></label></div>
    <div className="demo-actions">
      <button disabled={busy} onClick={() => run('Correct plate arrival', () => adminApi.plate(token, { bay_id: selectedBay, license_plate: plate, confidence: 0.98 }))}>✓ Correct plate</button>
      <button disabled={busy} onClick={() => run('Wrong plate', () => adminApi.plate(token, { bay_id: selectedBay, license_plate: 'N-CD5678', confidence: 0.96 }))}>⚠ Wrong plate</button>
      <button disabled={busy} onClick={() => run('Vehicle entered', () => adminApi.occupancy(token, { bay_id: selectedBay, occupancy: 'occupied', license_plate: plate }))}>↓ Vehicle enters</button>
      <button disabled={busy} onClick={() => run('Vehicle left', () => adminApi.occupancy(token, { bay_id: selectedBay, occupancy: 'free' }))}>↑ Vehicle leaves</button>
      <button disabled={busy || !selectedReservation} onClick={() => run('Charging started', () => adminApi.charging(token, selectedReservation, 'start'))}>ϟ Start charging</button>
      <button disabled={busy || !selectedReservation} onClick={() => run('Charging stopped', () => adminApi.charging(token, selectedReservation, 'stop'))}>■ Stop charging</button>
      <button disabled={busy} onClick={() => run('No-show scan', () => adminApi.noShow(token, new Date(Date.now() + 2 * 3600000).toISOString()))}>⌛ Run No-Show</button>
    </div>
    {result && <div className="demo-result">{busy ? 'Working…' : result}</div>}
  </section>
}

function Overview({ data, token, reload }) {
  const overview = data.overview || {}
  const cards = [
    ['Total reservations', overview.total_reservations, 'All application bookings'],
    ['Active now', overview.active_reservations, 'Non-terminal reservations'],
    ['Checked in', overview.checked_in_vehicles, 'Arrived or charging'],
    ['No-shows', overview.no_show_count, 'Expired without arrival'],
    ['Blocked alerts', overview.blocked_bay_alerts, 'Open and needs attention'],
    ['Completion', `${overview.completion_rate || 0}%`, 'Completed / total'],
  ]
  return <><div className="kpi-grid">{cards.map(([label, value, detail]) => <article key={label}><span>{label}</span><strong>{value ?? 0}</strong><small>{detail}</small></article>)}</div><div className="admin-overview-grid"><section className="card"><div className="section-title"><div><span className="eyebrow">Live operations</span><h2>Recent reservations</h2></div><NavLink to="/admin/reservations">View all</NavLink></div><ReservationTable rows={(data.reservations || []).slice(0, 5)} compact /></section><section className="card"><div className="section-title"><div><span className="eyebrow">Attention queue</span><h2>Open alerts</h2></div><NavLink to="/admin/alerts">Manage</NavLink></div><div className="mini-alerts">{(data.alerts || []).filter((item) => item.status === 'OPEN').slice(0, 4).map((alert) => <div key={alert.id}><i>!</i><span><b>{alert.alert_type.replaceAll('_', ' ')}</b><small>{alert.bay_id} · {alert.message}</small></span></div>)}{!(data.alerts || []).some((item) => item.status === 'OPEN') && <div className="empty-state"><b>All clear</b><span>No open operational alerts.</span></div>}</div></section></div>{overview.provider_mode === 'mock' && <DemoPanel token={token} reservations={data.reservations || []} onDone={reload} />}</>
}

function ReservationTable({ rows, compact = false, onRelease, onAuthorize, releaseBusyId = '', authorizationBusyId = '', providerMode }) {
  const actionBusy = Boolean(releaseBusyId || authorizationBusyId)
  return <div className="table-wrap"><table><thead><tr><th>Reservation</th><th>Driver</th><th>Bay</th><th>Status</th>{!compact && <><th>Window</th><th>Action</th></>}</tr></thead><tbody>{rows.map((item) => {
    const terminal = ['COMPLETED', 'CANCELLED', 'EXPIRED'].includes(item.status)
    const canAuthorize = providerMode === 'live' && item.status === 'ATTENTION_REQUIRED' && item.hardware_sync_status === 'FAILED'
    return <tr key={item.id}><td><b>{item.station_name}</b><small>{item.id}<br />{item.license_plate}</small></td><td>{item.user_email}</td><td><b>{item.bay_id}</b></td><td><StatusBadge value={item.status} />{item.hardware_sync_status !== 'NOT_REQUIRED' && <small>Hardware sync: {item.hardware_sync_status}</small>}</td>{!compact && <><td>{formatDate(item.start_time)}<small>to {formatDate(item.end_time)}</small></td><td>{item.status === 'RELEASE_PENDING' ? <small>Hardware confirmation pending</small> : terminal ? <small>—</small> : <div className="reservation-admin-actions">{canAuthorize && <button className="btn primary compact" disabled={actionBusy} onClick={() => onAuthorize(item)}>{authorizationBusyId === item.id ? 'Authorizing…' : 'Force authorize / 强制授权'}</button>}<button className="btn danger compact" disabled={actionBusy} onClick={() => onRelease(item)}>{releaseBusyId === item.id ? 'Releasing…' : ['RELEASE_FAILED', 'RELEASE_UNKNOWN'].includes(item.status) ? 'Retry release / 重试' : 'Release / 释放'}</button></div>}</td></>}</tr>
  })}</tbody></table>{!rows.length && <div className="empty-state"><b>No reservations</b><span>New bookings will appear here.</span></div>}</div>
}

function HardwareController({ bay, token, reload }) {
  const [busy, setBusy] = useState('')
  const [result, setResult] = useState('')
  const online = bay.hardware_online !== false
  const run = async (movement, action) => {
    const verb = movement === 'raise' ? 'RAISE / 上升' : 'LOWER / 下降'
    const warning = movement === 'raise' ? '\nMake sure the bay is empty and the device is visible.' : ''
    if (!window.confirm(`REAL HARDWARE: ${verb} ${bay.bay_id}?${warning}`)) return
    setBusy(movement); setResult('')
    try {
      const response = await action()
      setResult(`${verb} command ${response.command_status}. Watch the device, then refresh its status.`)
      window.setTimeout(reload, 1500)
    } catch (error) {
      setResult(error.message)
    } finally {
      setBusy('')
    }
  }
  return <div className="hardware-controller">
    <div className="hardware-controller-title"><b>Manual hardware control</b><span>{online ? 'ONLINE' : 'OFFLINE'}</span></div>
    <p>Real device commands. Keep the bay in sight before confirming.</p>
    <div className="hardware-controller-actions">
      <button className="btn danger compact" disabled={Boolean(busy) || !online || bay.occupancy === 'occupied' || bay.protection === 'protected'} onClick={() => run('raise', () => adminApi.raiseBarrier(token, bay.bay_id))}>{busy === 'raise' ? 'Sending…' : '↑ Raise / 上升'}</button>
      <button className="btn ghost compact" disabled={Boolean(busy) || !online || bay.protection === 'released'} onClick={() => run('lower', () => adminApi.lowerBarrier(token, bay.bay_id))}>{busy === 'lower' ? 'Sending…' : '↓ Lower / 下降'}</button>
      <button className="text-button" disabled={Boolean(busy)} onClick={reload}>↻ Refresh status</button>
    </div>
    {bay.occupancy === 'occupied' && <small className="hardware-warning">Raise is blocked because the occupancy sensor reports a vehicle.</small>}
    {result && <small className="hardware-result">{result}</small>}
  </div>
}

function Bays({ rows, token, reload, providerMode }) {
  return <div className="bay-admin-grid">{rows.map((bay) => <article className={`card bay-admin-card ${bay.blocked ? 'blocked' : ''}`} key={bay.bay_id}><div><span className="eyebrow">{bay.station_name || 'Unmapped station'}</span><h2>{bay.bay_id}</h2></div><StatusBadge value={bay.blocked ? 'BLOCKED' : bay.availability_label} /><dl><div><dt>Physical occupancy</dt><dd>{bay.occupancy}</dd></div><div><dt>Protection hardware</dt><dd>{bay.protection}</dd></div><div><dt>Reservation</dt><dd>{bay.app_reservation_id || 'None'}</dd></div><div><dt>Last update</dt><dd>{bay.last_seen_at ? formatDate(bay.last_seen_at) : 'No signal yet'}</dd></div></dl>{providerMode === 'live' && <HardwareController bay={bay} token={token} reload={reload} />}</article>)}</div>
}

function Events({ rows }) {
  return <section className="card"><div className="event-timeline">{rows.map((event) => <div key={event.external_event_id}><time>{new Date(event.timestamp).toLocaleTimeString()}</time><i /><span><b>{event.event_type.replaceAll('.', ' ').toUpperCase()}</b><small>{event.bay_id} · {event.masked_plate || 'No plate payload'} · confidence {event.confidence ?? '—'}</small></span><StatusBadge value={event.processing_result} /></div>)}{!rows.length && <div className="empty-state"><b>No processed events</b><span>Use the demo control room to simulate arrival.</span></div>}</div></section>
}

function Alerts({ rows, acknowledge }) {
  return <div className="alert-admin-list">{rows.map((alert) => <article className="card" key={alert.id}><div className="alert-icon">!</div><div><span className="eyebrow">{alert.alert_type.replaceAll('_', ' ')}</span><h2>{alert.bay_id}</h2><p>{alert.message}</p><small>{formatDate(alert.created_at)} · {alert.reservation_id || 'No linked reservation'}</small></div><div><StatusBadge value={alert.status} />{alert.status === 'OPEN' && <button className="btn ghost compact" onClick={() => acknowledge(alert)}>Acknowledge</button>}</div></article>)}{!rows.length && <div className="empty-state large"><b>No alerts recorded</b><span>Wrong plate and confirmed blocked-bay events will appear here.</span></div>}</div>
}

function Analytics({ value = {} }) {
  const metrics = [['Check-in rate', value.check_in_rate], ['No-show rate', value.no_show_rate], ['Cancellation rate', value.cancellation_rate]]
  return <><div className="simulation-banner"><span>Simulation / Estimated</span><p>Commercial figures use configurable assumptions and are not real operating performance.</p></div><div className="analytics-grid"><section className="card revenue-card"><span className="eyebrow">Estimated revenue</span><strong>€{Number(value.estimated_revenue_eur || 0).toFixed(2)}</strong><p>Reservation fee €{value.assumptions?.reservation_fee_eur?.toFixed(2) || '0.00'} · No-show fee €{value.assumptions?.no_show_fee_eur?.toFixed(2) || '0.00'} · Simulated charging €{Number(value.assumptions?.simulated_charging_rate_eur_per_hour || 0).toFixed(2)}/h</p></section><section className="card"><span className="eyebrow">Conversion funnel</span><h2>Reservation outcomes</h2>{metrics.map(([label, number]) => <div className="metric-bar" key={label}><span>{label}<b>{number || 0}%</b></span><i><em style={{ width: `${Math.min(number || 0, 100)}%` }} /></i></div>)}</section><section className="card metric-number"><span>Charging sessions</span><strong>{value.completed_charging_sessions || 0}</strong><small>completed simulated sessions · €{Number(value.estimated_charging_revenue_eur || 0).toFixed(2)} estimated</small></section><section className="card metric-number"><span>Blocked Bay events</span><strong>{value.blocked_bay_events || 0}</strong><small>recorded operational incidents</small></section></div></>
}

export default function AdminPage({ section = 'overview' }) {
  const { token } = useAuth()
  const [data, setData] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [releaseBusyId, setReleaseBusyId] = useState('')
  const [authorizationBusyId, setAuthorizationBusyId] = useState('')
  const [operationResult, setOperationResult] = useState(null)
  const load = useCallback(async () => {
    try {
      const [overview, bays, reservations, events, alerts, analytics] = await Promise.all([
        adminApi.overview(token), adminApi.bays(token), adminApi.reservations(token),
        adminApi.events(token), adminApi.alerts(token), adminApi.analytics(token),
      ])
      setError(''); setData({ overview, bays, reservations, events, alerts, analytics })
    } catch (reason) { setError(reason.message) } finally { setLoading(false) }
  }, [token])
  useEffect(() => {
    const kickoff = setTimeout(load, 0)
    const timer = setInterval(load, 10000)
    return () => { clearTimeout(kickoff); clearInterval(timer) }
  }, [load])
  const titles = { overview: ['Operations overview', 'Live service signals and simulation KPIs'], bays: ['Bay management', 'Physical, reservation and hardware state remain distinct'], reservations: ['Reservation management', 'Provider-backed bookings across all drivers'], events: ['Vehicle event monitor', 'Deduplicated camera and occupancy event processing'], alerts: ['Operational alerts', 'Wrong plate is separate from confirmed blocked occupancy'], analytics: ['Analytics & business case', 'Transparent simulation assumptions for the CPO case'] }
  const providerMode = data.overview?.provider_mode
  const release = async (item) => {
    const liveNote = providerMode === 'live' ? '\nLive mode: this ends the reservation and synchronizes hardware authorization. It does not move the barrier.' : ''
    if (!window.confirm(`Release reservation ${item.id} for ${item.license_plate}?${liveNote}`)) return
    setReleaseBusyId(item.id); setOperationResult(null)
    try {
      const released = await adminApi.release(token, item.id)
      const pending = released.status === 'RELEASE_PENDING'
      setOperationResult({
        type: pending ? 'warning' : 'success',
        message: pending
          ? `${item.id}: release accepted; hardware confirmation is pending.`
          : `${item.id}: reservation released successfully.`,
      })
      await load()
    } catch (reason) {
      setOperationResult({ type: 'error', message: `${item.id}: ${reason.message}` })
    } finally {
      setReleaseBusyId('')
    }
  }
  const authorize = async (item) => {
    const warning = `Write ${item.license_plate} to the real hardware whitelist for ${item.bay_id}?\nThis does not move the barrier.`
    if (!window.confirm(warning)) return
    setAuthorizationBusyId(item.id); setOperationResult(null)
    try {
      const authorized = await adminApi.retryAuthorization(token, item.id)
      setOperationResult({
        type: 'success',
        message: `${authorized.id}: hardware authorization confirmed; reservation is now RESERVED.`,
      })
      await load()
    } catch (reason) {
      setOperationResult({ type: 'error', message: `${item.id}: ${reason.message}` })
    } finally {
      setAuthorizationBusyId('')
    }
  }
  const acknowledge = async (item) => { await adminApi.acknowledge(token, item.id); load() }
  return <div className="admin-shell">
    <aside className="admin-sidebar"><span className="eyebrow">CPO workspace</span><h2>Operations</h2><nav>{ADMIN_LINKS.map(([path, label]) => <NavLink end={path === '/admin'} to={path} key={path}>{label}</NavLink>)}</nav><div className={`admin-foot ${providerMode || 'loading'}`}><span className="live-dot" /> {providerMode === 'live' ? 'Live hardware provider' : providerMode === 'mock' ? 'Mock provider connected' : 'Checking provider…'}</div></aside>
    <div className="admin-content"><div className="admin-heading"><div><span className="eyebrow">Ecurious Control</span><h1>{titles[section][0]}</h1><p>{titles[section][1]}</p></div><div className="admin-heading-actions">{providerMode && <div className={`provider-mode ${providerMode}`}><span className="live-dot" /><div><b>{providerMode === 'live' ? 'LIVE HARDWARE' : 'MOCK SIMULATION'}</b><small>{providerMode === 'live' ? 'Commands affect the connected lock' : 'No physical hardware commands'}</small></div></div>}<button className="btn ghost compact" onClick={load}>↻ Refresh</button></div></div>
      {error && <div className="alert error">{error}</div>}
      {operationResult && <div className={`alert ${operationResult.type}`}>{operationResult.message}</div>}
      {loading ? <div className="page-state"><div className="spinner" />Loading operations…</div> : <>
        {section === 'overview' && <Overview data={data} token={token} reload={load} />}
        {section === 'bays' && <Bays rows={data.bays || []} token={token} reload={load} providerMode={providerMode} />}
        {section === 'reservations' && <section className="card"><div className="section-title"><div><span className="eyebrow">Operator actions</span><h2>All reservations</h2></div><small>Force authorize retries a failed whitelist write; Release ends only the selected reservation.</small></div><ReservationTable rows={data.reservations || []} onRelease={release} onAuthorize={authorize} releaseBusyId={releaseBusyId} authorizationBusyId={authorizationBusyId} providerMode={providerMode} /></section>}
        {section === 'events' && <Events rows={data.events || []} />}
        {section === 'alerts' && <Alerts rows={data.alerts || []} acknowledge={acknowledge} />}
        {section === 'analytics' && <Analytics value={data.analytics} />}
      </>}
    </div>
  </div>
}
