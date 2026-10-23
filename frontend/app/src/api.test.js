import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, canAccessAdmin, maxPower, statusTone } from './api'

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

  it('maps operational states to consistent UI tones', () => {
    expect(statusTone('CHECKED_IN')).toBe('success')
    expect(statusTone('BLOCKED')).toBe('danger')
    expect(statusTone('CHARGING')).toBe('charging')
    expect(statusTone('RESERVED')).toBe('info')
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
})
