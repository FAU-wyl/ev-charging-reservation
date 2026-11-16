export const canConfirmDeparture = (reservation) =>
  reservation?.status === 'CHARGE_COMPLETED'
  && reservation?.charging_session?.status === 'COMPLETED'
