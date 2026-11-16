import { afterEach, describe, expect, it, vi } from 'vitest'
import { adminApi, api, canAccessAdmin, maxPower, postAuthPath, reservationApi, statusLabel, statusTone } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('frontend domain helpers', () => {
  it('derives power without treating connector count as availability', () => {
    expect(maxPower({ chargepoints: [
      { type: 'Type2', power: 22, count: 4 },
      { type: 'CCS', power: 150, count: 1 },
    ] })).toBe(150)
  })

  it('keeps administrator access role-based', () => {
    expect(canAccessAdmin({ role: 'ADMIN' })).toBe(true)
    expect(canAccessAdmin({ role: 'DRIVER' })).toBe(false)
    expect(canAccessAdmin(null)).toBe(false)
  })

  it('routes administrators to the control room after login', () => {
    expect(postAuthPath({ role: 'ADMIN' })).toBe('/admin')
    expect(postAuthPath({ role: 'DRIVER' })).toBe('/reservations')
    expect(postAuthPath({ role: 'ADMIN' }, '/admin/alerts')).toBe('/admin/alerts')
  })

  it('maps operational states to consistent UI tones', () => {
    expect(statusTone('CHECKED_IN')).toBe('success')
    expect(statusTone('BLOCKED')).toBe('danger')
    expect(statusTone('CHARGING')).toBe('charging')
    expect(statusTone('RESERVED')).toBe('info')
    expect(statusTone('RELEASE_FAILED')).toBe('danger')
    expect(statusTone('RELEASE_PENDING')).toBe('info')
  })

  it('presents a checked-in reservation as in use', () => {
    expect(statusLabel('CHECKED_IN')).toBe('IN USE')
    expect(statusLabel('ATTENTION_REQUIRED')).toBe('ATTENTION REQUIRED')
  })
})

describe('API client', () => {
  it('sends session tokens and JSON bodies to the App Backend', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({ status: 'ok' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    await expect(api('/api/example', {
      method: 'POST', token: 'session-token', body: { value: 1 },
    })).resolves.toEqual({ status: 'ok' })
    expect(fetchMock).toHaveBeenCalledWith('/api/example', expect.objectContaining({
      method: 'POST',
      headers: expect.objectContaining({ Authorization: 'Bearer session-token' }),
      body: '{"value":1}',
    }))
  })

  it('surfaces the backend error code and message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false, status: 409,
      json: async () => ({ error: 'BAY_ALREADY_RESERVED', message: 'Time slot unavailable' }),
    }))
    await expect(api('/api/reservations')).rejects.toMatchObject({
      status: 409, code: 'BAY_ALREADY_RESERVED', message: 'Time slot unavailable',
    })
  })

  it('uses persisted charging session endpoints', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({ session_id: 'CHG-1' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    await reservationApi.startCharging('token', 'APP-RES-1')
    await reservationApi.stopCharging('token', 'CHG-1')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/charging/sessions')
    expect(fetchMock.mock.calls[0][1].body).toBe('{"reservation_id":"APP-RES-1"}')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/charging/sessions/CHG-1/stop')
  })

  it('maps explicit admin movement controls to the hardware endpoints', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({ command_status: 'ACCEPTED' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    await adminApi.raiseBarrier('admin-token', 'lock-0001')
    await adminApi.lowerBarrier('admin-token', 'lock-0001')
    expect(fetchMock.mock.calls[0][0]).toBe('/api/bays/lock-0001/lock')
    expect(fetchMock.mock.calls[1][0]).toBe('/api/bays/lock-0001/unlock')
    expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({
      method: 'POST', headers: expect.objectContaining({ Authorization: 'Bearer admin-token' }),
    }))
  })

  it('releases one selected reservation through the admin endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({ id: 'APP-RES-1', status: 'COMPLETED' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    await adminApi.release('admin-token', 'APP-RES-1')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/reservations/APP-RES-1/release',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ Authorization: 'Bearer admin-token' }),
      }),
    )
  })

  it('retries hardware authorization for one selected reservation', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true, status: 200, json: async () => ({ id: 'APP-RES-1', status: 'RESERVED' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    await adminApi.retryAuthorization('admin-token', 'APP-RES-1')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/admin/reservations/APP-RES-1/retry-hardware-authorization',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ Authorization: 'Bearer admin-token' }),
      }),
    )
  })

  it('confirms driver departure through the reservation endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        reservation: { id: 'APP-RES-1', status: 'COMPLETED' },
        hardware_command: { command_status: 'ACCEPTED' },
      }),
    })
    vi.stubGlobal('fetch', fetchMock)
    await reservationApi.confirmDeparture('driver-token', 'APP-RES-1')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/reservations/APP-RES-1/depart',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ Authorization: 'Bearer driver-token' }),
      }),
    )
  })
})
