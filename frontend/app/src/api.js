export const POWER_CLASSES = [
  { min: 100, color: '#ef5b4d', label: '100+ kW' },
  { min: 43, color: '#f59e45', label: '43+ kW' },
  { min: 22, color: '#28a9d4', label: '22+ kW' },
  { min: 11, color: '#79879b', label: '11+ kW' },
  { min: 0, color: '#263346', label: 'under 11 kW' },
]

export const colorFor = (kw) =>
  (POWER_CLASSES.find((item) => kw >= item.min) || POWER_CLASSES.at(-1)).color

export const maxPower = (station) =>
  Math.max(0, ...(station.chargepoints || []).map((point) => Number(point.power) || 0))

export const statusTone = (status = '') => {
  if (['COMPLETED', 'CHECKED_IN', 'Free (Mock live status)'].includes(status)) return 'success'
  if (['EXPIRED', 'CANCELLED'].includes(status)) return 'muted'
  if (['BLOCKED', 'ATTENTION_REQUIRED', 'Occupied'].includes(status)) return 'danger'
  if (['CHARGING', 'CHARGE_COMPLETED'].includes(status)) return 'charging'
  return 'info'
}

export const canAccessAdmin = (user) => user?.role === 'ADMIN'

const API_BASE = (import.meta.env.VITE_APP_API_URL || '').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(message, status, code) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

export async function api(path, options = {}) {
  const { token, headers, body, ...rest } = options
  const response = await fetch(`${API_BASE}${path}`, {
    ...rest,
    headers: {
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const data = response.status === 204 ? null : await response.json().catch(() => ({}))
  if (!response.ok) throw new ApiError(data.message || 'Request failed', response.status, data.error)
  return data
}

export async function fetchStations(bounds, _zoom, signal) {
  const query = new URLSearchParams()
  if (bounds) {
    query.set('ne_lat', bounds.getNorth())
    query.set('ne_lng', bounds.getEast())
    query.set('sw_lat', bounds.getSouth())
    query.set('sw_lng', bounds.getWest())
  }
  return api(`/api/stations?${query}`, { signal })
}

export const fetchStation = (id) => api(`/api/stations/${id}`)
export const fetchBayAvailability = (id) => api(`/api/bays/${id}/availability`)

export async function geocode(text) {
  const response = await fetch(
    `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(text)}`,
  )
  if (!response.ok) throw new ApiError('Location search failed', response.status)
  const data = await response.json()
  return data[0] ? [Number(data[0].lat), Number(data[0].lon)] : null
}

export const authApi = {
  register: (email, password) => api('/api/auth/register', { method: 'POST', body: { email, password } }),
  login: (email, password) => api('/api/auth/login', { method: 'POST', body: { email, password } }),
  me: (token) => api('/api/auth/me', { token }),
  logout: (token) => api('/api/auth/logout', { method: 'POST', token }),
}

export const vehicleApi = {
  list: (token) => api('/api/vehicles', { token }),
  create: (token, body) => api('/api/vehicles', { method: 'POST', token, body }),
  update: (token, id, body) => api(`/api/vehicles/${id}`, { method: 'PATCH', token, body }),
  remove: (token, id) => api(`/api/vehicles/${id}`, { method: 'DELETE', token }),
}

export const reservationApi = {
  create: (token, body, requestKey) => api('/api/reservations', {
    method: 'POST', token, body, headers: { 'Idempotency-Key': requestKey },
  }),
  mine: (token) => api('/api/reservations/my', { token }),
  get: (token, id) => api(`/api/reservations/${id}`, { token }),
  cancel: (token, id) => api(`/api/reservations/${id}/cancel`, { method: 'POST', token }),
}

export const adminApi = {
  overview: (token) => api('/api/admin/overview', { token }),
  bays: (token) => api('/api/admin/bays', { token }),
  reservations: (token) => api('/api/admin/reservations', { token }),
  events: (token) => api('/api/admin/events', { token }),
  alerts: (token) => api('/api/admin/alerts', { token }),
  analytics: (token) => api('/api/admin/analytics', { token }),
  release: (token, id) => api(`/api/admin/reservations/${id}/release`, { method: 'POST', token }),
  acknowledge: (token, id) => api(`/api/admin/alerts/${id}/acknowledge`, {
    method: 'POST', token, body: { note: 'Acknowledged in demo dashboard' },
  }),
  plate: (token, body) => api('/api/admin/demo/plate', { method: 'POST', token, body }),
  occupancy: (token, body) => api('/api/admin/demo/occupancy', { method: 'POST', token, body }),
  charging: (token, id, action) => api(`/api/admin/demo/reservations/${id}/charging/${action}`, {
    method: 'POST', token,
  }),
  noShow: (token, now) => api('/api/admin/demo/no-show', {
    method: 'POST', token, body: { now },
  }),
}
