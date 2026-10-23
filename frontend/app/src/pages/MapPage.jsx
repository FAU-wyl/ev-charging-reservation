import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { MapContainer, Marker, TileLayer, ZoomControl, useMap, useMapEvents } from 'react-leaflet'
import { useNavigate } from 'react-router-dom'
import { colorFor, fetchStations, geocode, maxPower, POWER_CLASSES } from '../api'
import StatusBadge from '../components/StatusBadge'

const CONNECTORS = ['All', 'Type2', 'CCS', 'CHAdeMO', 'Schuko', 'Tesla']
const POWERS = [['Any', 0], ['11+ kW', 11], ['22+ kW', 22], ['43+ kW', 43], ['100+ kW', 100]]

const pinIcon = (color, reservable, fault) => L.divIcon({
  className: 'pin',
  iconSize: [34, 46],
  iconAnchor: [17, 43],
  html: `<svg viewBox="0 0 34 46" width="34" height="46"><path d="M17 1C8 1 1 8 1 17c0 11 16 28 16 28s16-17 16-28C33 8 26 1 17 1z" fill="${color}" stroke="${reservable ? '#0e7a5f' : '#fff'}" stroke-width="${reservable ? 4 : 2}"/><circle cx="17" cy="17" r="6" fill="#fff"/></svg>${fault ? '<span class="warn">!</span>' : ''}${reservable ? '<span class="reserve-dot">R</span>' : ''}`,
})

function Loader({ onData, onError, onLoading, flyTo }) {
  const map = useMap()
  const controller = useRef()
  const timer = useRef()
  const run = useCallback(() => {
    controller.current?.abort()
    controller.current = new AbortController()
    onLoading(true)
    fetchStations(map.getBounds(), map.getZoom(), controller.current.signal)
      .then(onData)
      .catch((error) => { if (error.name !== 'AbortError') onError(error.message) })
      .finally(() => onLoading(false))
  }, [map, onData, onError, onLoading])
  const load = useCallback(() => {
    clearTimeout(timer.current)
    timer.current = setTimeout(run, 350)
  }, [run])
  useMapEvents({ moveend: load })
  useEffect(() => {
    load()
    return () => { clearTimeout(timer.current); controller.current?.abort() }
  }, [load])
  useEffect(() => { if (flyTo) map.flyTo(flyTo, 15) }, [flyTo, map])
  return null
}

function MapControls() {
  const map = useMap()
  return <div className="side-btns">
    <button aria-label="Go to my location" onClick={() => map.locate({ setView: true, maxZoom: 15 })}>◎</button>
    <button aria-label="Toggle fullscreen" onClick={() => document.fullscreenElement
      ? document.exitFullscreen()
      : map.getContainer().closest('.map-wrap').requestFullscreen()}>⛶</button>
  </div>
}

function StationPopup({ station, onClose, onOpen }) {
  return <aside className="station-popup">
    <button className="icon-button close" onClick={onClose} aria-label="Close">×</button>
    <div className="station-popup-head">
      <div><span className="eyebrow">{station.source}</span><h2>{station.name}</h2><p>{station.street}, {station.postcode} {station.city}</p></div>
      <StatusBadge value={station.availability_label} />
    </div>
    <div className="station-popup-grid">
      <div><span className="field-label">Connectors</span>{station.chargepoints.map((point, index) =>
        <div className="connector-row" key={`${point.type}-${index}`}><b>{point.count} × {point.type}</b><span>{point.power} kW</span></div>)}</div>
      <div className="station-facts">
        <div><span>Network</span><b>{station.network || 'Not specified'}</b></div>
        <div><span>Directory status</span><b>{station.fault ? 'Fault report in fixture' : 'No fault report'}</b></div>
        <div><span>Ecurious</span><b>{station.reservation_supported ? `Connected · ${station.ecurious_bay_ids.join(', ')}` : 'Not connected'}</b></div>
      </div>
    </div>
    <div className="popup-actions">
      <a className="btn ghost" href={station.source_url} target="_blank" rel="noreferrer">GoingElectric details ↗</a>
      <button className="btn primary" onClick={onOpen}>{station.reservation_supported ? 'Check & reserve' : 'View station'}</button>
    </div>
  </aside>
}

export default function MapPage() {
  const navigate = useNavigate()
  const [stations, setStations] = useState([])
  const [selected, setSelected] = useState(null)
  const [flyTo, setFlyTo] = useState(null)
  const [query, setQuery] = useState('')
  const [list, setList] = useState(false)
  const [connector, setConnector] = useState('All')
  const [power, setPower] = useState(0)
  const [network, setNetwork] = useState('All')
  const [reservableOnly, setReservableOnly] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const onData = useCallback((value) => { setStations(value); setError('') }, [])
  const onError = useCallback((value) => setError(value), [])
  const onLoading = useCallback((value) => setLoading(value), [])
  const networks = useMemo(() => ['All', ...new Set(stations.map((item) => item.network).filter(Boolean))], [stations])
  const shown = useMemo(() => stations.filter((station) =>
    (connector === 'All' || station.chargepoints.some((point) => point.type.includes(connector)))
    && maxPower(station) >= power
    && (network === 'All' || station.network === network)
    && (!reservableOnly || station.reservation_supported)
    && (!query.trim() || `${station.name} ${station.city} ${station.street}`.toLowerCase().includes(query.toLowerCase())),
  ), [stations, connector, power, network, reservableOnly, query])

  const submitSearch = async (event) => {
    event.preventDefault()
    if (!query.trim() || shown.length) return
    try {
      const point = await geocode(query)
      if (point) setFlyTo(point); else setError('No matching place found')
    } catch (reason) { setError(reason.message) }
  }

  const choose = (station) => { setSelected(station); setFlyTo([station.lat, station.lng]) }
  return <div className="map-page">
    <section className="map-hero">
      <div><span className="eyebrow">Smart reservation for public charging</span><h1>Find your charger.<br />Know your bay is waiting.</h1></div>
      <div className="hero-stat"><strong>{shown.filter((item) => item.reservation_supported).length}</strong><span>reservable locations in view</span></div>
    </section>
    <section className="map-card">
      <div className="map-wrap">
        <form className="searchbar" onSubmit={submitSearch}>
          <span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Station, address, city or network" />
          {query && <button type="button" onClick={() => setQuery('')}>×</button>}
        </form>
        <button className="chipbtn listbtn" onClick={() => setList((value) => !value)}>{list ? 'Map view' : 'List view'} <span>{shown.length}</span></button>
        <MapContainer center={[48.1374, 11.5755]} zoom={14} zoomControl={false} className="map">
          <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' url="https://tile.openstreetmap.org/{z}/{x}/{y}.png" />
          <ZoomControl position="bottomright" />
          <Loader onData={onData} onError={onError} onLoading={onLoading} flyTo={flyTo} />
          <MapControls />
          {shown.map((station) => <Marker key={station.id} position={[station.lat, station.lng]}
            icon={pinIcon(colorFor(maxPower(station)), station.reservation_supported, station.fault)}
            eventHandlers={{ click: () => choose(station) }} />)}
        </MapContainer>
        <div className={`map-status ${error ? 'error' : ''}`}>{loading ? 'Loading stations…' : error || `${shown.length} stations in this view`}</div>
        {list && <div className="station-list">{shown.length ? shown.map((station) =>
          <button key={station.id} onClick={() => { choose(station); setList(false) }}>
            <i style={{ background: colorFor(maxPower(station)) }} /><span><b>{station.name}</b><small>{station.street} · {station.city}</small></span>
            <em>{maxPower(station)} kW</em>{station.reservation_supported && <strong>Reservable</strong>}
          </button>) : <div className="empty-state"><b>No matching stations</b><span>Try clearing a filter or moving the map.</span></div>}</div>}
        {selected && <StationPopup station={selected} onClose={() => setSelected(null)} onOpen={() => navigate(`/stations/${selected.id}`)} />}
      </div>
      <div className="filterbar">
        <label><span>Plug</span><select value={connector} onChange={(event) => setConnector(event.target.value)}>{CONNECTORS.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label><span>Minimum power</span><select value={power} onChange={(event) => setPower(Number(event.target.value))}>{POWERS.map(([label, value]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label><span>Network</span><select value={network} onChange={(event) => setNetwork(event.target.value)}>{networks.map((item) => <option key={item}>{item}</option>)}</select></label>
        <label className="toggle-filter"><input type="checkbox" checked={reservableOnly} onChange={(event) => setReservableOnly(event.target.checked)} /><span>Reservable only<small>Mapped to Ecurious</small></span></label>
        <div className="legend"><span>Power</span>{POWER_CLASSES.slice(0, 4).map((item) => <i key={item.label} title={item.label} style={{ background: item.color }} />)}</div>
      </div>
    </section>
    <p className="data-note">Connector count describes installed plugs, not live availability. Unmapped stations remain “Availability Unknown”.</p>
  </div>
}
