import { describe, expect, it } from 'vitest'
import { canConfirmDeparture } from './reservationState'

describe('reservation departure controls', () => {
  it('shows departure confirmation after charging has stopped', () => {
    expect(canConfirmDeparture({
      status: 'CHARGE_COMPLETED',
      charging_session: { status: 'COMPLETED' },
    })).toBe(true)
  })

  it('does not show departure confirmation during active charging', () => {
    expect(canConfirmDeparture({
      status: 'CHARGING',
      charging_session: { status: 'ACTIVE' },
    })).toBe(false)
  })
})
