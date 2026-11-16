# ECURIOUS Hackathon — Seven Mission Areas Completion Report

Date: 2026-10-24  
Scope: React + FastAPI App Backend + SQLite + existing fictional Mock Backend  
Safety: no real ECURIOUS hardware write was executed while completing or testing this work.

## Result

All seven Mission Areas now have executable backend behavior, authenticated APIs, persisted
state, a usable React flow, and automated tests. They are demo-ready in Mock mode. Features
that depend on a real camera, occupancy sensor, EEPROM whitelist, or multiple physical locks
remain explicitly marked as requiring a controlled on-site Live validation.

| Mission Area | Implementation | Tests | Demo Ready |
|---|---|---|---|
| No-Shows | Background lifecycle scan uses `start + 15 min`; in-app arrival reminders; safe physical-state check; whitelist/release reconciliation; persisted No-Show cases; estimated fee; review queue and waiver path when hardware/OCR evidence is uncertain | Automatic reminder/expiry/record/fee, idempotence and no-penalty review tests; existing Live boundary/offline/occupied/whitelist-failure tests retained | **YES — Mock**; Live EEPROM removal requires on-site validation |
| Blocked Bays | Occupancy-backed detection; driver notification plus Admin alert; acknowledge/escalate/resolve; safe alternative search; Admin reallocation; explicit no-alternative message; automatic recovery when original bay becomes free | Wrong-plate/occupancy regression plus alternative discovery, escalation, reallocation and notification test | **YES — Mock**; multi-lock Live reallocation requires physical locks |
| License Plate Recognition | Reservation/vehicle binding; vehicle replacement before check-in; matching LPR triggers automatic check-in; Occupancy remains an independent blocked-bay/safety signal; authenticated Live manual unlock fallback; empty/OCR-failure handling; masked logs and retention purge | Chinese/European-style plate normalization, vehicle replacement, immediate camera check-in, masking and retention purge; existing Live latest-plate/SSE/manual fallback tests retained | **YES — Mock**; real European-plate OCR and SSE delivery still require on-site validation |
| Interoperability | Scoped `X-API-Key` Partner REST API for bays, create, status and cancel; unified `APP-RES-*` ID; partner ownership isolation; generated OpenAPI; OCPI/OCPP clearly documented as future work | Missing/wrong key, create/read/cancel, unified ID and OpenAPI description test | **YES — local Partner API**; no external partner sandbox was supplied |
| New Use Cases | Configurable Standard, Hotel, Retail Member and Enterprise Fleet policies; enabled flag, duration, advance window, reference requirement and fee discount; driver booking UI and Admin configuration UI; core ReservationService reused | Hotel reference validation, successful discounted booking and Admin configuration test | **YES — Hotel scenario end-to-end** |
| Utilisation | Time-range filter; sensor-event Occupancy Rate; Reservation Utilisation; No-Show Rate; Cancellation Rate; Idle Time; reserved hours; source labels distinguish Live hardware from Mock/simulated data | Controlled occupancy interval and one-hour range test validates 16.7% rate, 2.5 idle hours and source label | **YES**; accuracy depends on event coverage in Live operation |
| Business Case | Persisted estimated Reservation/No-Show charge ledger; driver breakdown; configurable free cancellation, waiver, CPO subscription and revenue share; operator/platform analysis; all values marked Simulation/Estimated | Fee creation, no-show fee, operator waiver, free-cancellation waiver and commercial assumptions tests | **YES — Simulation only**; no payment, invoice or meter integration |

## Functional implementation

### 1. No-Shows

- The application lifecycle task runs alongside the existing polling/Live maintenance loops.
- An arrival reminder is persisted once per reservation before its start.
- A reservation is eligible for No-Show only at `start_time + grace`.
- Mock mode verifies a current free bay, releases the provider reservation, and records
  `EXPIRED`. Live mode continues to use the existing safe hardware reconciliation and then
  records the business outcome.
- Offline, unknown, occupied, provider-error or inconclusive evidence creates
  `NO_SHOW_REVIEW`; no No-Show fee is applied automatically.
- Confirmed No-Shows create a persisted case, notification and estimated charge. Admin can
  waive a charge with an audit reason.

### 2. Blocked Bays

- `occupancy.changed` remains the physical source for Blocked Bay decisions.
- A blocked incident produces an Admin alert and a driver-visible in-app notification.
- Admin can acknowledge, escalate to CPO support/on-site staff, or resolve the alert.
- Alternative lookup filters out the current bay, unsupported bays, overlapping reservations,
  offline hardware and non-free occupancy.
- Reallocation preserves the application reservation ID and fee history, audits the move,
  updates the provider/hardware authorization as appropriate, and notifies the driver.
- If no safe alternative exists, the UI explicitly keeps the incident in the attention queue.
- A `free` event resolves the blocked alert and restores a still-valid blocked reservation.

### 3. License Plate Recognition

- A driver can switch to another owned vehicle before check-in; the provider reservation or
  Live whitelist is updated rather than creating a separate application booking.
- A matching camera plate records `CHECK_IN_SUCCESSFUL` and immediately moves the active
  reservation to `CHECKED_IN` / `IN USE`; a delayed Occupancy SSE event cannot block arrival.
- Occupancy remains an independent blocked-bay and hardware-safety signal.
- Empty `photo_captured` events query `/plate/latest`; empty/stale results are ignored and do
  not create `WRONG_PLATE`.
- Live manual fallback remains limited to the current reservation holder or Admin, is audited,
  and still requires a subsequent occupancy event to complete check-in.
- Admin event/API output stores masked plate values. Raw latest-camera state has a configurable
  retention purge (`PRIVACY_RETENTION_DAYS`, default 30 days).

### 4. Interoperability

Partner endpoints:

```text
GET  /api/partner/v1/bays
POST /api/partner/v1/reservations
GET  /api/partner/v1/reservations/{reservation_id}
POST /api/partner/v1/reservations/{reservation_id}/cancel
```

Authentication uses `X-API-Key`. Only a SHA-256 key hash is stored. Each API client has a
service identity and explicit scopes (`bays:read`, `reservations:read`,
`reservations:write`); reservation reads/cancels are restricted to that client. The partner
API reuses the main ReservationService, conflict checks, use-case policy and app reservation
ID.

FastAPI publishes the contract at `/docs` and `/openapi.json`. The application intentionally
does **not** claim OCPI or OCPP conformance. A future adapter can map the internal station,
EVSE/reservation and charging-session models to OCPI/OCPP without changing the driver API.

### 5. New Use Cases

Seeded, configurable policies:

| Code | Example rules |
|---|---|
| `STANDARD` | Public reservation, 240-minute maximum |
| `HOTEL` | Guest reference required, 720-minute maximum, 20% reservation-fee discount |
| `RETAIL` | Member reference required, 120-minute maximum, 15% discount |
| `FLEET` | Fleet reference required, 480-minute maximum, 10% discount |

Admin can enable/disable a policy and change maximum duration, advance-booking limit, discount
and reference requirement. The Station booking form consumes the same policy API and shows the
required reference field. No separate reservation engine was introduced.

### 6. Utilisation

`GET /api/admin/analytics?from_time=<ISO>&to_time=<ISO>` now returns:

- Occupancy Rate based on persisted occupancy events over mapped-bay capacity.
- Reservation Utilisation based on reservations that reached check-in/use.
- No-Show and cancellation rates.
- Idle hours and reserved hours.
- The exact reporting range and source label.
- Commercial totals and assumptions for the same range.

Mock data is labelled `Mock occupancy events (simulated)`. Live mode labels occupancy as
ECURIOUS sensor events. Missing hardware events are not silently presented as measured usage.

### 7. Business Case

- `RESERVATION_FEE` is created from the configured base fee and scenario discount.
- `NO_SHOW_FEE` is created only for auto-confirmed No-Shows.
- Cancellation earlier than `FREE_CANCELLATION_MINUTES` waives the reservation fee.
- Admin can waive an estimated fee with a required reason.
- Driver detail displays itemized costs and notifications.
- Admin Analytics displays reservation, No-Show and simulated charging revenue, waived fees,
  subscription assumption, revenue share, platform revenue and CPO net estimate.
- No endpoint captures payment; no value is described as settled, invoiced or measured energy.

## New and extended APIs

```text
GET   /api/use-cases
POST  /api/reservations/{id}/vehicle
GET   /api/reservations/{id}/finance

GET   /api/admin/use-cases
PATCH /api/admin/use-cases/{code}
GET   /api/admin/reservations/{id}/alternatives
POST  /api/admin/reservations/{id}/reallocate
POST  /api/admin/alerts/{id}/escalate
POST  /api/admin/alerts/{id}/resolve
GET   /api/admin/charges
POST  /api/admin/charges/{id}/waive
POST  /api/admin/lifecycle/run
POST  /api/admin/privacy/purge
GET   /api/admin/analytics?from_time=...&to_time=...

GET   /api/partner/v1/bays
POST  /api/partner/v1/reservations
GET   /api/partner/v1/reservations/{id}
POST  /api/partner/v1/reservations/{id}/cancel
```

Existing Reserve, Arrive, Charge, Stop, Depart/Release, hardware control and Admin APIs remain
available. Live hardware commands still require authenticated roles and existing safety checks.

## Database additions

Additive SQLite migration preserves existing databases. Added reservation columns:

- `use_case_code`
- `membership_reference`
- `api_client_id`

Added tables:

- `use_case_policies`
- `reservation_notifications`
- `reservation_charges`
- `no_show_cases`
- `api_clients`
- `reservation_reallocations`

`alerts.escalation_level` is added through the same additive migration.

## React demonstration steps

### Preparation

1. Start the fictional Mock Backend on port 8000.
2. Start App Backend on port 8001 using `app_backend/.env.mock.example`.
3. Start Vite on port 5173.
4. Use the demo DRIVER and ADMIN accounts documented in the root README.

### No-Shows

1. DRIVER creates a reservation.
2. ADMIN opens Overview and uses `Run No-Show` for the accelerated demo, or waits for the
   configured background deadline.
3. DRIVER opens the journey and sees the notification plus Reservation/No-Show estimated fees.
4. ADMIN opens Analytics to inspect the No-Show rate and charge ledger; waive the fee to demo
   the review path.

### Blocked Bays

1. Create an active reservation for `BAY-001`.
2. In Mock Demo Control Room send `Wrong plate`, then `Vehicle enters`.
3. Open Admin Reservations and click `Find alternative`.
4. Choose an offered bay. The reservation retains its `APP-RES-*` ID and the driver receives a
   reallocation notification. If all other bays are unavailable, the Admin receives an explicit
   no-alternative message.
5. Open Alerts to acknowledge, escalate or resolve the incident.

### License Plate Recognition

1. DRIVER adds two vehicles and creates a reservation.
2. On the journey page, switch the authorized vehicle before arrival.
3. ADMIN sends the new correct plate. Status immediately becomes `IN USE / CHECKED_IN`.
4. ADMIN sends `Vehicle enters` separately to demonstrate Occupancy telemetry.
5. In controlled Live mode, use `Manual LPR fallback` only with the physical device visible;
   physical Occupancy must still confirm the manual-unlock fallback path.

### Interoperability

Load a non-production demo key from environment and call:

```bash
curl -H "X-API-Key: $ECURIOUS_PARTNER_KEY" http://127.0.0.1:8001/api/partner/v1/bays
```

Use `/docs` to execute create, status and cancel. Do not paste or commit a real key.

### New Use Cases

1. ADMIN opens `Admin → Scenarios` and reviews Hotel rules.
2. DRIVER opens a reservable station, selects `Hotel guest`, enters a guest reference and books.
3. The journey shows `HOTEL`; the finance card shows the configured discounted estimated fee.

### Utilisation

1. Generate Mock plate/occupancy and completion events.
2. Open `Admin → Analytics`.
3. Change 7/30/90-day range and inspect Occupancy, Reservation Utilisation, No-Show and Idle
   Time together with their source labels.

### Business Case

1. Open a driver journey to see itemized estimated fees.
2. Create a No-Show to add the configured estimated fee.
3. In Admin Analytics click `Waive`, enter a reason, and verify the ledger/status/KPI updates.
4. Review the subscription and revenue-share assumptions; all values remain simulated.

## Automated regression evidence

Final clean run:

```text
Mock Backend pytest:  20 passed, 0 failed
App Backend pytest:   74 passed, 0 failed
Frontend Vitest:      15 passed, 0 failed
Total:               109 passed, 0 failed
Oxlint:                passed, 0 warnings
Vite production build: passed, 81 modules transformed
Python compileall:     passed
Both FastAPI imports:  passed
git diff --check:      passed
```

The seven dedicated App Backend tests are in
`app_backend/tests/test_mission_areas.py`, one end-to-end test per Mission Area. Existing release,
hardware safety, SSE ordering, No-Show boundaries, charging and reservation regressions remain
enabled.

## Modified files

### Backend and configuration

- `app_backend/app_backend/config.py`
- `app_backend/app_backend/database.py`
- `app_backend/app_backend/main.py`
- `app_backend/app_backend/schemas.py`
- `app_backend/app_backend/api/admin.py`
- `app_backend/app_backend/api/reservations.py`
- `app_backend/app_backend/api/missions.py` (new)
- `app_backend/app_backend/api/partner.py` (new)
- `app_backend/app_backend/services/admin.py`
- `app_backend/app_backend/services/event_sync.py`
- `app_backend/app_backend/services/reservations.py`
- `app_backend/app_backend/services/missions.py` (new)
- `app_backend/.env.example`
- `app_backend/.env.mock.example`

### React

- `frontend/app/src/App.jsx`
- `frontend/app/src/api.js`
- `frontend/app/src/index.css`
- `frontend/app/src/pages/AdminPage.jsx`
- `frontend/app/src/pages/ReservationDetailPage.jsx`
- `frontend/app/src/pages/StationPage.jsx`

### Tests and documentation

- `app_backend/tests/test_mission_areas.py` (new)
- `app_backend/tests/test_events_admin.py`
- `app_backend/tests/test_live_hardware.py`
- `frontend/app/src/api.test.js`
- `README.md`
- `app_backend/README.md`
- `MISSION_AREAS_COMPLETION_REPORT.md` (new)

Existing uncommitted work was not reset, overwritten wholesale, committed, pushed, merged or
switched to another branch.

## Items still requiring real hardware validation

The following are implemented and covered with Fake Adapter/MockTransport tests, but were not
executed against physical hardware during this task:

1. EEPROM whitelist change after vehicle replacement.
2. EEPROM removal at an automatic Live No-Show deadline.
3. Real European plate recognition plus real Occupancy SSE delivery.
4. Authenticated manual fallback followed by physical occupancy confirmation.
5. Physical multi-lock alternative discovery and reallocation; the current team setup may have
   only one assigned business lock.
6. Reallocation rollback behavior during a real network/4G interruption.
7. Driver departure and physical lock rise in the complete new seven-area demo sequence.

Previously reported team manual raise/lower evidence remains separate; this completion run did
not repeat it. Live writes must continue to be executed one at a time with the device visible
and explicit human confirmation.

## Known boundaries

- SQLite and in-process background loops are appropriate for the Hackathon single-instance
  demo, not a multi-region production deployment.
- Reminders and operational notices are in-app records; no email/SMS provider is connected.
- The demo Partner API uses environment-configured keys; production should add key rotation,
  per-client administration, rate limits, mTLS/OAuth and tenant isolation.
- Occupancy analytics depends on receiving sensor events; missing events are not imputed as real
  measurements.
- Charging, fees, CPO subscription, revenue share and all monetary totals are simulations.
- OCPI/OCPP, payment, invoices and real charger meter values are not implemented or claimed.
